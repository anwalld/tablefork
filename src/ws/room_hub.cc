#include "ws/room_hub.h"
#include "buffering/guarded_redis.h"
#include "buffering/request_buffer.h"

#include <drogon/drogon.h>
#include <json/json.h>
#include <algorithm>
#include <optional>
#include <utility>
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

void RoomHub::markRedisTrouble()
{
    redisTrouble_.store(true);
}

void RoomHub::start()
{
    subscriber()->subscribe(
        kReconcileChannel,
        [this](const std::string &, const std::string &) {
            // Reconciliation can unsubscribe; never run it under Drogon's
            // SubscribeContext channel lock.
            drogon::app().getLoop()->queueInLoop([this] {
                drogon::async_run([this]() -> drogon::Task<> { co_await reconcile(); });
            });
        });
    drogon::app().getLoop()->runEvery(1.0, [this] {
        if (!redisTrouble_.load())
        {
            return;
        }
        drogon::async_run([this]() -> drogon::Task<> {
            try
            {
                // PING may succeed while ACL still denies PUBLISH.
                co_await drogon::app().getRedisClient()->execCommandCoro(
                    "PUBLISH %s 1", kReconcileChannel);
                redisTrouble_.store(false);
                co_await reconcile();
            }
            catch (const drogon::nosql::RedisException &)
            {
                // Keep the dirty flag until publication actually recovers.
            }
        });
    });
}

drogon::Task<> RoomHub::reconcile()
{
    auto *loop = drogon::app().getLoop();
    co_await drogon::switchThreadCoro(loop);
    if (reconciling_.exchange(true))
    {
        reconcileAgain_.store(true);
        co_return;
    }
    do
    {
        reconcileAgain_.store(false);
        try
        {
            using Membership = std::pair<int64_t, int64_t>;
            std::set<Membership> distinct;
            {
                std::lock_guard lock(mutex_);
                for (const auto &[roomId, room] : rooms_)
                {
                    for (const auto &[connection, memberId] : room.connections)
                    {
                        distinct.emplace(roomId, memberId);
                    }
                }
            }
            const std::vector<Membership> snapshot(distinct.begin(), distinct.end());
            const auto db = drogon::app().getDbClient();
            // Bind all values; only the number of placeholders is dynamic.
            const auto query = [db](const std::string &prefix,
                                     const std::vector<Membership> &pairs,
                                     size_t offset) -> drogon::Task<drogon::orm::Result> {
                const auto end = std::min(offset + 500, pairs.size());
                std::string sql = prefix;
                std::vector<int64_t> args;
                for (auto i = offset; i < end; ++i)
                {
                    if (i != offset) sql += ",";
                    sql += "(?, ?)";
                    args.push_back(pairs[i].first);
                    args.push_back(pairs[i].second);
                }
                sql += ")";
                co_return co_await db->execSqlCoro(sql, std::as_const(args));
            };
            std::set<Membership> present;
            for (size_t offset = 0; offset < snapshot.size(); offset += 500)
            {
                const auto rows = co_await query(
                    "SELECT room_id, member_id FROM room_members WHERE (room_id, member_id) IN (",
                    snapshot, offset);
                for (const auto &row : rows)
                {
                    present.emplace(row["room_id"].as<int64_t>(), row["member_id"].as<int64_t>());
                }
            }
            std::vector<Membership> missing;
            for (const auto &pair : snapshot)
            {
                if (!present.contains(pair)) missing.push_back(pair);
            }
            std::set<Membership> banned;
            for (size_t offset = 0; offset < missing.size(); offset += 500)
            {
                const auto rows = co_await query(
                    "SELECT b.room_id, m.id AS member_id FROM room_bans b "
                    "JOIN members m ON m.user_id = b.user_id WHERE (b.room_id, m.id) IN (",
                    missing, offset);
                for (const auto &row : rows)
                {
                    banned.emplace(row["room_id"].as<int64_t>(), row["member_id"].as<int64_t>());
                }
            }
            // SQL coroutines may resume on a database thread. Local removal
            // and its subscription changes belong on the application loop.
            co_await drogon::switchThreadCoro(loop);
            for (const auto &pair : missing)
            {
                removeMemberLocally(pair.first, pair.second, banned.contains(pair) ? "banned" : "kicked");
            }
        }
        catch (const drogon::orm::DrogonDbException &e)
        {
            LOG_WARN << "room reconcile skipped: " << e.base().what();
        }
        co_await drogon::switchThreadCoro(loop);
    } while (reconcileAgain_.exchange(false));
    reconciling_.store(false);
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
                    // Drogon holds the channel's SubscribeContext mutex here.
                    // Removal may unsubscribe that channel, so leave the Redis
                    // callback before touching room state or subscriptions.
                    drogon::app().getLoop()->queueInLoop([this, roomId, message] {
                        onChannelMessage(roomId, message);
                    });
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

drogon::Task<> RoomHub::removeMember(int64_t roomId, int64_t memberId, std::string reason, RequestContextPtr context)
{
    removeMemberLocally(roomId, memberId, reason);
    Json::Value event;
    event["type"] = "member_left";
    event["member_id"] = Json::Int64(memberId);
    event["reason"] = reason;
    co_await publish(roomId, toJsonString(event), context);
    // Preserve publication order: revoke first, then refresh the member list.
    Json::Value changed;
    changed["type"] = "members_changed";
    changed["room_id"] = Json::Int64(roomId);
    co_await publish(roomId, toJsonString(changed), context);
}

void RoomHub::removeMemberLocally(int64_t roomId, int64_t memberId, const std::string &reason)
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
    event["reason"] = reason;
    const auto payload = toJsonString(event);
    for (const auto &connection : removed)
    {
        connection->send(payload);
    }
}

drogon::Task<bool> RoomHub::publish(int64_t roomId, const std::string &payload, RequestContextPtr context)
{
    if (!context && RequestBuffer::instance().enabled())
    {
        try { context = RequestBuffer::instance().backgroundContext(); }
        catch (const ApiError &) { co_return false; }
    }
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "PUBLISH %s %s", channelFor(roomId).c_str(), payload.c_str());
        co_return true;
    }
    catch (const ApiError &) { co_return false; }
    catch (const drogon::nosql::RedisException &e)
    {
        markRedisTrouble();
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
            removeMemberLocally(roomId, (*event)["member_id"].asInt64(), event->get("reason", "left").asString());
        }
        else if (event->isMember("only_member_ids"))
        {
            // Meant for some members only; they get it without the targets.
            std::set<int64_t> targets;
            for (const auto &id : (*event)["only_member_ids"])
            {
                targets.insert(id.asInt64());
            }
            auto copy = *event;
            copy.removeMember("only_member_ids");
            sendToRoom(roomId, toJsonString(copy), &targets);
        }
        else
        {
            sendToRoom(roomId, payload, nullptr);
        }
        return;
    }
    sendToRoom(roomId, payload, nullptr);
}

void RoomHub::sendToRoom(int64_t roomId,
                         const std::string &payload,
                         const std::set<int64_t> *targets)
{
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
            if (!targets || targets->contains(memberId))
            {
                connections.push_back(connection);
            }
        }
    }
    for (const auto &connection : connections)
    {
        connection->send(payload);
    }
}
}  // namespace chat
