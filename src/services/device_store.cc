#include "services/device_store.h"

#include <drogon/Cookie.h>
#include <drogon/drogon.h>

#include "common/config.h"
#include "buffering/guarded_redis.h"
#include "common/errors.h"

namespace chat
{
namespace
{
std::string keyFor(const std::string &token)
{
    return "device:" + drogon::utils::getSha256(token);
}

int ttlSeconds()
{
    return Config::get().deviceTtlDays * 86400;
}
}  // namespace

drogon::Task<std::optional<std::string>> DeviceStore::owner(std::string token, RequestContextPtr context)
{
    if (token.empty())
    {
        co_return std::nullopt;
    }
    try
    {
        const auto result =
            co_await guardedRedis(context)->execCommandCoro(
                "GET %s", keyFor(token).c_str());
        if (result.isNil())
        {
            co_return std::nullopt;
        }
        co_return result.asString();
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "device owner: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<std::string> DeviceStore::issue(std::string username, RequestContextPtr context)
{
    auto token = drogon::utils::secureRandomString(48);
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "SET %s %s EX %d",
            keyFor(token).c_str(),
            username.c_str(),
            ttlSeconds());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "device issue: " << e.what();
        throw ApiError::unavailable();
    }
    co_return token;
}

drogon::Task<> DeviceStore::refresh(std::string token, RequestContextPtr context)
{
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "EXPIRE %s %d", keyFor(token).c_str(), ttlSeconds());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        // The login already succeeded; the token just expires sooner.
        LOG_ERROR << "device refresh: " << e.what();
    }
}

void DeviceStore::setCookie(const drogon::HttpResponsePtr &resp,
                            const std::string &token)
{
    drogon::Cookie cookie(kDeviceCookie, token);
    // Only the auth endpoints need it.
    cookie.setPath("/api/auth");
    cookie.setHttpOnly(true);
    cookie.setSecure(Config::get().cookieSecure);
    cookie.setSameSite(drogon::Cookie::SameSite::kLax);
    cookie.setMaxAge(ttlSeconds());
    resp->addCookie(cookie);
}
}  // namespace chat
