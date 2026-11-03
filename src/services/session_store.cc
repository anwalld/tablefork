#include "services/session_store.h"

#include <drogon/Cookie.h>
#include <drogon/drogon.h>

#include "common/config.h"
#include "common/errors.h"

namespace chat
{
namespace
{
std::string userKey(int64_t userId)
{
    return "user_sessions:" + std::to_string(userId);
}

// v2|userId|memberId|device|kind|username. The name goes last because it
// may contain '|'; device is hex and kind a word, so neither can.
std::string encode(const Session &s)
{
    return "v2|" + std::to_string(s.userId) + "|" + std::to_string(s.memberId) + "|" +
           s.device + "|" + s.deviceKind + "|" + s.username;
}

std::optional<Session> decode(const std::string &value)
{
    std::vector<std::string> fields;
    size_t start = 0;
    const bool v2 = value.rfind("v2|", 0) == 0;
    if (v2)
    {
        start = 3;
    }
    // Sessions from before v2: userId|memberId|username, with no device.
    const size_t count = v2 ? 4 : 2;
    while (fields.size() < count)
    {
        const auto bar = value.find('|', start);
        if (bar == std::string::npos)
        {
            return std::nullopt;
        }
        fields.push_back(value.substr(start, bar - start));
        start = bar + 1;
    }
    Session session{std::stoll(fields[0]), std::stoll(fields[1]), value.substr(start)};
    if (v2)
    {
        session.device = fields[2];
        session.deviceKind = fields[3];
    }
    return session;
}

drogon::Cookie sessionCookie(const std::string &value, int maxAge)
{
    drogon::Cookie cookie(kSessionCookie, value);
    cookie.setPath("/");
    cookie.setHttpOnly(true);
    cookie.setSecure(Config::get().cookieSecure);
    cookie.setSameSite(drogon::Cookie::SameSite::kLax);
    cookie.setMaxAge(maxAge);
    return cookie;
}
}  // namespace

std::string SessionStore::keyFor(const std::string &token)
{
    return "session:" + drogon::utils::getSha256(token);
}

drogon::Task<std::string> SessionStore::create(const Session &session)
{
    auto token = drogon::utils::secureRandomString(48);
    try
    {
        auto redis = drogon::app().getRedisClient();
        const auto ttl = Config::get().sessionTtlSeconds;
        co_await redis->execCommandCoro("SET %s %s EX %d",
                                        keyFor(token).c_str(),
                                        encode(session).c_str(),
                                        ttl);
        // The user's sessions, so that one logout can end them all. Keys of
        // sessions that expired on their own may linger here; ending them
        // again does nothing.
        co_await redis->execCommandCoro(
            "SADD %s %s", userKey(session.userId).c_str(), keyFor(token).c_str());
        co_await redis->execCommandCoro(
            "EXPIRE %s %d", userKey(session.userId).c_str(), ttl);
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "session create: " << e.what();
        throw ApiError::unavailable();
    }
    co_return token;
}

drogon::Task<std::optional<Session>> SessionStore::find(const std::string &token)
{
    if (token.empty())
    {
        co_return std::nullopt;
    }
    try
    {
        const auto result =
            co_await drogon::app().getRedisClient()->execCommandCoro(
                "GET %s", keyFor(token).c_str());
        if (result.isNil())
        {
            co_return std::nullopt;
        }
        co_return decode(result.asString());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "session find: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<> SessionStore::remove(const std::string &token)
{
    try
    {
        co_await drogon::app().getRedisClient()->execCommandCoro(
            "DEL %s", keyFor(token).c_str());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "session remove: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<std::vector<std::string>> SessionStore::end(const Session &session,
                                                         const std::string &token,
                                                         bool everywhere)
{
    std::vector<std::string> ended;
    try
    {
        auto redis = drogon::app().getRedisClient();
        const auto users = userKey(session.userId);
        if (everywhere)
        {
            const auto members =
                co_await redis->execCommandCoro("SMEMBERS %s", users.c_str());
            for (const auto &member : members.asArray())
            {
                ended.push_back(member.asString());
            }
            ended.push_back(keyFor(token));
            for (const auto &key : ended)
            {
                co_await redis->execCommandCoro("DEL %s", key.c_str());
            }
            co_await redis->execCommandCoro("DEL %s", users.c_str());
        }
        else
        {
            ended.push_back(keyFor(token));
            co_await redis->execCommandCoro("DEL %s", ended.back().c_str());
            co_await redis->execCommandCoro(
                "SREM %s %s", users.c_str(), ended.back().c_str());
        }
        for (const auto &key : ended)
        {
            co_await redis->execCommandCoro("PUBLISH %s %s", kEndedChannel, key.c_str());
        }
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "session end: " << e.what();
        throw ApiError::unavailable();
    }
    co_return ended;
}

drogon::Task<bool> SessionStore::extend(const Session &session, const std::string &token)
{
    try
    {
        auto redis = drogon::app().getRedisClient();
        const auto ttl = Config::get().sessionTtlSeconds;
        const auto extended = co_await redis->execCommandCoro(
            "EXPIRE %s %d", keyFor(token).c_str(), ttl);
        if (extended.asInteger() == 0)
        {
            co_return false;
        }
        co_await redis->execCommandCoro(
            "EXPIRE %s %d", userKey(session.userId).c_str(), ttl);
        co_return true;
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "session extend: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<Session> SessionStore::require(const drogon::HttpRequestPtr &req)
{
    auto session = co_await find(req->getCookie(kSessionCookie));
    if (!session)
    {
        throw ApiError::unauthenticated();
    }
    co_return *session;
}

void SessionStore::setCookie(const drogon::HttpResponsePtr &resp,
                             const std::string &token)
{
    resp->addCookie(sessionCookie(token, Config::get().sessionTtlSeconds));
}

void SessionStore::clearCookie(const drogon::HttpResponsePtr &resp)
{
    resp->addCookie(sessionCookie("", 0));
}
}  // namespace chat
