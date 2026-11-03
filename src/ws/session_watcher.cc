#include "ws/session_watcher.h"

#include <drogon/drogon.h>
#include <set>

#include "common/config.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
std::vector<std::vector<std::string>> keyBatches(const std::vector<std::string> &keys,
                                                 size_t size)
{
    const std::set<std::string> distinct(keys.begin(), keys.end());
    std::vector<std::vector<std::string>> batches;
    for (const auto &key : distinct)
    {
        if (batches.empty() || batches.back().size() == size)
        {
            batches.emplace_back();
        }
        batches.back().push_back(key);
    }
    return batches;
}

SessionWatcher &SessionWatcher::instance()
{
    static SessionWatcher watcher;
    return watcher;
}

void SessionWatcher::start()
{
    subscriber_ = drogon::app().getRedisClient()->newSubscriber();
    subscriber_->subscribe(SessionStore::kEndedChannel,
                           [this](const std::string &, const std::string &key) { end(key); });
    // Our own timer: Drogon's WebSocket ping only keeps connections alive.
    drogon::app().getLoop()->runEvery(Config::get().sessionCheckSeconds, [this] {
        drogon::async_run([this]() -> drogon::Task<> { co_await check(); });
    });
}

void SessionWatcher::add(const drogon::WebSocketConnectionPtr &connection,
                         const std::string &sessionKey)
{
    std::lock_guard lock(mutex_);
    connections_[connection] = sessionKey;
}

void SessionWatcher::remove(const drogon::WebSocketConnectionPtr &connection)
{
    std::lock_guard lock(mutex_);
    connections_.erase(connection);
}

void SessionWatcher::end(const std::string &sessionKey)
{
    std::vector<drogon::WebSocketConnectionPtr> closing;
    {
        std::lock_guard lock(mutex_);
        for (auto it = connections_.begin(); it != connections_.end();)
        {
            if (it->second == sessionKey)
            {
                closing.push_back(it->first);
                it = connections_.erase(it);
            }
            else
            {
                ++it;
            }
        }
    }
    for (const auto &connection : closing)
    {
        // Stop pushes first, then tell the client not to come back.
        RoomHub::instance().removeConnection(connection);
        connection->shutdown(static_cast<drogon::CloseCode>(kSessionEnded), "session_ended");
    }
}

drogon::Task<> SessionWatcher::check()
{
    std::vector<std::string> keys;
    {
        std::lock_guard lock(mutex_);
        for (const auto &[connection, key] : connections_)
        {
            keys.push_back(key);
        }
    }
    for (const auto &batch : keyBatches(keys, 500))
    {
        // Session keys are "session:" plus hex, safe to place in the command.
        std::string command = "MGET";
        for (const auto &key : batch)
        {
            command += " " + key;
        }
        std::vector<std::string> ended;
        try
        {
            const auto values =
                co_await drogon::app().getRedisClient()->execCommandCoro(command.c_str());
            const auto items = values.asArray();
            for (size_t i = 0; i < items.size() && i < batch.size(); ++i)
            {
                if (items[i].isNil())
                {
                    ended.push_back(batch[i]);
                }
            }
        }
        catch (const drogon::nosql::RedisException &e)
        {
            // A Redis hiccup must not log everyone out; try again next time.
            LOG_WARN << "session check skipped: " << e.what();
            co_return;
        }
        for (const auto &key : ended)
        {
            end(key);
        }
    }
}
}  // namespace chat
