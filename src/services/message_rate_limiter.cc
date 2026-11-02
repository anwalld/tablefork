#include "services/message_rate_limiter.h"

#include <chrono>
#include <drogon/drogon.h>

#include "common/config.h"
#include "common/errors.h"

namespace chat
{
namespace
{
// KEYS[1]: the window's counter. ARGV[1]: its TTL. Returns the new count.
constexpr const char *kCountScript = R"lua(
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
)lua";
}  // namespace

std::string messageRateKey(int64_t userId, int64_t nowSeconds, int windowSeconds)
{
    return "msg_rate:" + std::to_string(userId) + ":" +
           std::to_string(nowSeconds / windowSeconds);
}

int secondsLeftInWindow(int64_t nowSeconds, int windowSeconds)
{
    return static_cast<int>(windowSeconds - nowSeconds % windowSeconds);
}

drogon::Task<> MessageRateLimiter::admit(int64_t userId)
{
    const auto &config = Config::get();
    const int64_t now = std::chrono::duration_cast<std::chrono::seconds>(
                            std::chrono::system_clock::now().time_since_epoch())
                            .count();
    long long count = 0;
    try
    {
        const auto result =
            co_await drogon::app().getRedisClient()->execCommandCoro(
                "EVAL %s 1 %s %d",
                kCountScript,
                messageRateKey(userId, now, config.messageRateWindowSeconds)
                    .c_str(),
                config.messageRateWindowSeconds + 1);
        count = result.asInteger();
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "message rate: " << e.what();
        throw ApiError::unavailable();
    }
    if (count > config.messageRateLimit)
    {
        throw ApiError::rateLimited(
            secondsLeftInWindow(now, config.messageRateWindowSeconds));
    }
}
}  // namespace chat
