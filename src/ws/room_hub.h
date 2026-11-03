#pragma once

#include <cstdint>
#include <drogon/WebSocketConnection.h>
#include <drogon/nosql/RedisClient.h>
#include <drogon/nosql/RedisSubscriber.h>
#include <drogon/utils/coroutine.h>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <unordered_map>

namespace chat
{
// This instance's view of who is in which room, bridged to Redis Pub/Sub. The
// instance subscribes to a room's channel for as long as it has a connection in
// that room. Everything is delivered through the channel, including to the
// sender's own instance, so one instance exercises the multi-instance path.
class RoomHub
{
  public:
    static RoomHub &instance();

    // Resolves once the Redis subscription is live, so a history read made
    // after it cannot miss a message.
    drogon::Task<> subscribe(drogon::WebSocketConnectionPtr connection,
                             int64_t roomId,
                             int64_t memberId);
    void unsubscribe(const drogon::WebSocketConnectionPtr &connection,
                     int64_t roomId);
    void removeConnection(const drogon::WebSocketConnectionPtr &connection);

    // Stops delivery to a member who left, here and on other instances.
    drogon::Task<> removeMember(int64_t roomId, int64_t memberId);

    // Returns false when Redis refused or could not be reached.
    drogon::Task<bool> publish(int64_t roomId, const std::string &payload);

  private:
    struct Room
    {
        std::unordered_map<drogon::WebSocketConnectionPtr, int64_t> connections;
        bool ready{false};
    };

    void onChannelMessage(int64_t roomId, const std::string &payload);
    // Sends to every local connection in the room, or only to those of the
    // targeted members.
    void sendToRoom(int64_t roomId,
                    const std::string &payload,
                    const std::set<int64_t> *targets);
    void removeMemberLocally(int64_t roomId, int64_t memberId);
    void eraseIfEmpty(int64_t roomId);
    std::shared_ptr<drogon::nosql::RedisSubscriber> subscriber();

    std::mutex mutex_;
    std::unordered_map<int64_t, Room> rooms_;
    std::shared_ptr<drogon::nosql::RedisSubscriber> subscriber_;
};
}  // namespace chat
