#include "services/session_store.h"

#include <drogon/Cookie.h>
#include <drogon/drogon.h>

#include "common/config.h"
#include "common/errors.h"

namespace chat
{
namespace
{
std::string keyFor(const std::string &token)
{
    return "session:" + drogon::utils::getSha256(token);
}

std::string encode(const Session &s)
{
    return std::to_string(s.userId) + "|" + std::to_string(s.memberId) + "|" +
           s.username;
}

std::optional<Session> decode(const std::string &value)
{
    const auto first = value.find('|');
    const auto second = value.find('|', first + 1);
    if (first == std::string::npos || second == std::string::npos)
    {
        return std::nullopt;
    }
    return Session{std::stoll(value.substr(0, first)),
                   std::stoll(value.substr(first + 1, second - first - 1)),
                   value.substr(second + 1)};
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

drogon::Task<std::string> SessionStore::create(const Session &session)
{
    auto token = drogon::utils::secureRandomString(48);
    try
    {
        co_await drogon::app().getRedisClient()->execCommandCoro(
            "SET %s %s EX %d",
            keyFor(token).c_str(),
            encode(session).c_str(),
            Config::get().sessionTtlSeconds);
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
