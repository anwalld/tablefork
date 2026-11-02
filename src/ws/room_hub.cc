#include "ws/room_hub.h"

#include <drogon/drogon.h>
#include <json/json.h>
#include <optional>
#include <sstream>
#include <vector>

#include "common/util.h"

namespace chat
{
namespace
{
std::string channelFor(int64_t roomId)
{
    return "room:" + std::to_string(roomId);
}

std::optional<Json::Value> parse(const std::string &payload)
{
    Json::CharReaderBuilder builder;
    std::istringstream stream(payload);
    Json::Value value;
    std::string errors;
    if (!Json::parseFromStream(builder, stream, &value, &errors))
    {
        return std::nullopt;
    }
    return value;
}
}  // namespace

RoomHub &RoomHub::instance()
{
    static RoomHub hub;
    return hub;
}

std::shared_ptr<drogon::nosql::RedisSubscriber> RoomHub::subscriber()
{
    if (!subscriber_)
    {
        subscriber_ = drogon::app().getRedisClient()->newSubscriber();
    }
    return subscriber_;
}

drogon::Task<> RoomHub::subscribe(drogon::WebSocketConnectionPtr connection,
                                  int64_t roomId,
                                  int64_t memberId)
{
    {
        std::lock_guard lock(mutex_);
        auto [room, created] = rooms_.try_emplace(roomId);
        room->second.connections[connection] = memberId;
        if (created)
        {
            subscriber()->subscribe(
                channelFor(roomId),
                [this, roomId](const std::string &, const std::string &message) {
                    onChannelMessage(roomId, message);
                });
        }
    }

    // The subscribe call gives no confirmation, so publish a marker until it
    // comes back through the channel.
    Json::Value marker;
    marker["type"] = "sync";
    const auto payload = toJsonString(marker);
    for (int attempt = 0; attempt < 50; ++attempt)
    {
        {
            std::lock_guard lock(mutex_);
            const auto room = rooms_.find(roomId);
            if (room == rooms_.end() || room->second.ready)
            {
                co_return;
            }
        }
        // Without Redis no confirmation can come back. Report success anyway:
        // the client reads history next and catches up when Redis returns.
        if (!co_await publish(roomId, payload))
        {
            co_return;
        }
        co_await drogon::sleepCoro(drogon::app().getLoop(), 0.02);
    }
    LOG_WARN << "subscription to room " << roomId << " not confirmed";
}

void RoomHub::unsubscribe(const drogon::WebSocketConnectionPtr &connection,
                          int64_t roomId)
{
    std::lock_guard lock(mutex_);
    const auto room = rooms_.find(roomId);
    if (room != rooms_.end())
    {
        room->second.connections.erase(connection);
        eraseIfEmpty(roomId);
    }
}

void RoomHub::removeConnection(const drogon::WebSocketConnectionPtr &connection)
{
    std::lock_guard lock(mutex_);
    std::vector<int64_t> roomIds;
    for (const auto &[roomId, room] : rooms_)
    {
        roomIds.push_back(roomId);
    }
    for (const auto roomId : roomIds)
    {
        rooms_[roomId].connections.erase(connection);
        eraseIfEmpty(roomId);
    }
}

void RoomHub::eraseIfEmpty(int64_t roomId)
{
    const auto room = rooms_.find(roomId);
    if (room != rooms_.end() && room->second.connections.empty())
    {
        rooms_.erase(room);
        subscriber()->unsubscribe(channelFor(roomId));
    }
}

drogon::Task<> RoomHub::removeMember(int64_t roomId, int64_t memberId)
{
    removeMemberLocally(roomId, memberId);
    Json::Value event;
    event["type"] = "member_left";
    event["member_id"] = Json::Int64(memberId);
    co_await publish(roomId, toJsonString(event));
}

void RoomHub::removeMemberLocally(int64_t roomId, int64_t memberId)
{
    std::vector<drogon::WebSocketConnectionPtr> removed;
    {
        std::lock_guard lock(mutex_);
        const auto room = rooms_.find(roomId);
        if (room == rooms_.end())
        {
            return;
        }
        auto &connections = room->second.connections;
        for (auto it = connections.begin(); it != connections.end();)
        {
            if (it->second == memberId)
            {
                removed.push_back(it->first);
                it = connections.erase(it);
            }
            else
            {
                ++it;
            }
        }
        eraseIfEmpty(roomId);
    }
    Json::Value event;
    event["type"] = "room_left";
    event["room_id"] = Json::Int64(roomId);
    const auto payload = toJsonString(event);
    for (const auto &connection : removed)
    {
        connection->send(payload);
    }
}

drogon::Task<bool> RoomHub::publish(int64_t roomId, const std::string &payload)
{
    try
    {
        co_await drogon::app().getRedisClient()->execCommandCoro(
            "PUBLISH %s %s", channelFor(roomId).c_str(), payload.c_str());
        co_return true;
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "publish to room " << roomId << ": " << e.what();
        co_return false;
    }
}

void RoomHub::onChannelMessage(int64_t roomId, const std::string &payload)
{
    if (payload.find("\"type\":\"message\"") == std::string::npos)
    {
        const auto event = parse(payload);
        if (!event)
        {
            return;
        }
        const auto type = (*event)["type"].asString();
        if (type == "sync")
        {
            std::lock_guard lock(mutex_);
            const auto room = rooms_.find(roomId);
            if (room != rooms_.end())
            {
                room->second.ready = true;
            }
        }
        else if (type == "member_left")
        {
            removeMemberLocally(roomId, (*event)["member_id"].asInt64());
        }
        return;
    }

    std::vector<drogon::WebSocketConnectionPtr> connections;
    {
        std::lock_guard lock(mutex_);
        const auto room = rooms_.find(roomId);
        if (room == rooms_.end())
        {
            return;
        }
        room->second.ready = true;
        connections.reserve(room->second.connections.size());
        for (const auto &[connection, memberId] : room->second.connections)
        {
            connections.push_back(connection);
        }
    }
    for (const auto &connection : connections)
    {
        connection->send(payload);
    }
}
}  // namespace chat
