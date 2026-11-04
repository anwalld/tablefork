#pragma once

#include <drogon/WebSocketConnection.h>
#include <drogon/nosql/RedisSubscriber.h>
#include <drogon/utils/coroutine.h>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <string>
#include <vector>

namespace chat
{
// Groups distinct keys into batches of at most `size`, for one MGET each.
std::vector<std::vector<std::string>> keyBatches(const std::vector<std::string> &keys,
                                                 size_t size);

// Uniform startup offset for a positive session check period.
double firstCheckDelay(double period, std::mt19937 &rng);

// Closes WebSocket connections whose session has ended. Logouts are announced
// on a Redis channel and handled at once; sessions that simply expire are
// found by a check every SESSION_CHECK_SECONDS. One per instance.
class SessionWatcher
{
  public:
    // The close code clients treat as "logged out": do not reconnect.
    static constexpr uint16_t kSessionEnded = 4401;

    static SessionWatcher &instance();

    void start();
    void add(const drogon::WebSocketConnectionPtr &connection, const std::string &sessionKey);
    void remove(const drogon::WebSocketConnectionPtr &connection);

    // Closes this instance's connections that use the session.
    void end(const std::string &sessionKey);

    // One check of every tracked session. Public for tests.
    drogon::Task<> check();

  private:
    std::mutex mutex_;
    std::map<drogon::WebSocketConnectionPtr, std::string> connections_;
    std::shared_ptr<drogon::nosql::RedisSubscriber> subscriber_;
};
}  // namespace chat
