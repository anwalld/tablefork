#include "services/login_limiter.h"

#include <algorithm>
#include <drogon/drogon.h>

#include "common/config.h"
#include "buffering/guarded_redis.h"
#include "common/errors.h"

namespace chat
{
namespace
{
// KEYS[1]: the counter. ARGV: free attempts, TTL seconds, schedule as "1,2,4".
// Returns the milliseconds left to wait, or 0 once the attempt is counted.
// Redis' own clock is used so that every app instance agrees.
constexpr const char *kAdmitScript = R"lua(
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local nextAt = tonumber(redis.call('HGET', KEYS[1], 'next_at') or '0')
if now < nextAt then
  return nextAt - now
end
local count = redis.call('HINCRBY', KEYS[1], 'count', 1)
local free = tonumber(ARGV[1])
if count >= free then
  local waits = {}
  for w in string.gmatch(ARGV[3], '%d+') do
    waits[#waits + 1] = tonumber(w)
  end
  local wait = waits[math.min(count - free + 1, #waits)]
  redis.call('HSET', KEYS[1], 'next_at', now + wait * 1000)
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 0
)lua";

std::string scheduleArgument()
{
    const auto &config = Config::get();
    std::string text;
    for (const int wait : backoffSchedule(config.loginBackoffBaseSeconds,
                                          config.loginBackoffMaxSeconds))
    {
        text += (text.empty() ? "" : ",") + std::to_string(wait);
    }
    return text;
}
}  // namespace

std::vector<int> backoffSchedule(int baseSeconds, int maxSeconds)
{
    std::vector<int> waits;
    long wait = std::max(baseSeconds, 1);
    while (wait < maxSeconds)
    {
        waits.push_back(static_cast<int>(wait));
        wait *= 2;
    }
    waits.push_back(std::max(maxSeconds, 1));
    return waits;
}

std::string LoginLimiter::nameKey(const std::string &username)
{
    return "login_fail:u:" + drogon::utils::getSha256(username);
}

std::string LoginLimiter::deviceKey(const std::string &deviceToken)
{
    return "login_fail:d:" + drogon::utils::getSha256(deviceToken);
}

drogon::Task<> LoginLimiter::admit(std::string key, RequestContextPtr context)
{
    static const std::string schedule = scheduleArgument();
    const auto &config = Config::get();
    long long waitMs = 0;
    try
    {
        const auto result =
            co_await guardedRedis(context)->execCommandCoro(
                "EVAL %s 1 %s %d %d %s",
                kAdmitScript,
                key.c_str(),
                config.loginFreeAttempts,
                config.loginFailTtlSeconds,
                schedule.c_str());
        waitMs = result.asInteger();
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "login limiter: " << e.what();
        throw ApiError::unavailable();
    }
    if (waitMs > 0)
    {
        throw ApiError::tooManyAttempts(static_cast<int>((waitMs + 999) / 1000));
    }
}

drogon::Task<> LoginLimiter::clear(std::string key, RequestContextPtr context)
{
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "DEL %s", key.c_str());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        // The login already succeeded; a stale count only delays the next one.
        LOG_ERROR << "login limiter clear: " << e.what();
    }
}
}  // namespace chat
