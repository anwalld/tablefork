#include "services/device_trust.h"

#include <algorithm>
#include <cctype>
#include <drogon/drogon.h>
#include <trantor/utils/Date.h>

#include "common/errors.h"

namespace chat
{
namespace
{
constexpr int kWindowDays = 30;

std::string trustedKey(int64_t userId)
{
    return "trusted:" + std::to_string(userId);
}

// KEYS[1] the user's trusted devices. ARGV: kind, device, then the window's
// days. Adds up both devices' daily counts and swaps when the newcomer sent
// more than 1.5 times as many. Returns 1 when the device is trusted.
constexpr const char *kEvaluate = R"lua(
local kind, device = ARGV[1], ARGV[2]
local current = redis.call('HGET', KEYS[1], kind)
if not current or current == device then
  redis.call('HSET', KEYS[1], kind, device)
  return 1
end
local function sent(who)
  local total = 0
  for i = 3, #ARGV do
    total = total + tonumber(redis.call('GET', 'device_sends:' .. who .. ':' .. ARGV[i]) or '0')
  end
  return total
end
if sent(device) * 2 > sent(current) * 3 then
  redis.call('HSET', KEYS[1], kind, device)
  return 1
end
return 0
)lua";
}  // namespace

std::string deviceKind(const std::string &userAgent)
{
    std::string lowered = userAgent;
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    for (const char *marker : {"mobile", "android", "iphone", "ipad", "ipod", "tablet"})
    {
        if (lowered.find(marker) != std::string::npos)
        {
            return "phone";
        }
    }
    return "computer";
}

bool shouldReplace(long long candidate, long long current)
{
    // candidate > 1.5 * current, in integers.
    return candidate * 2 > current * 3;
}

std::vector<std::string> recentDays(int days)
{
    std::vector<std::string> result;
    const auto now = trantor::Date::now();
    for (int i = 0; i < days; ++i)
    {
        result.push_back(now.after(-86400.0 * i).toCustomFormattedString("%Y%m%d", false));
    }
    return result;
}

drogon::Task<bool> DeviceTrust::evaluate(int64_t userId, std::string device, std::string kind)
{
    if (device.empty())
    {
        co_return false;
    }
    std::string command = "EVAL %s 1 %s %s %s";
    for (const auto &day : recentDays(kWindowDays))
    {
        // Dates are digits only, safe to place in the command.
        command += " " + day;
    }
    try
    {
        const auto result = co_await drogon::app().getRedisClient()->execCommandCoro(
            command.c_str(), kEvaluate, trustedKey(userId).c_str(), kind.c_str(), device.c_str());
        co_return result.asInteger() == 1;
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "device trust: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<bool> DeviceTrust::isTrusted(int64_t userId, std::string device, std::string kind)
{
    if (device.empty())
    {
        co_return false;
    }
    try
    {
        const auto current = co_await drogon::app().getRedisClient()->execCommandCoro(
            "HGET %s %s", trustedKey(userId).c_str(), kind.c_str());
        co_return !current.isNil() && current.asString() == device;
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_ERROR << "device trust: " << e.what();
        throw ApiError::unavailable();
    }
}

drogon::Task<> DeviceTrust::countSend(std::string device)
{
    if (device.empty())
    {
        co_return;
    }
    const auto key = "device_sends:" + device + ":" + recentDays(1)[0];
    try
    {
        auto redis = drogon::app().getRedisClient();
        co_await redis->execCommandCoro("INCR %s", key.c_str());
        co_await redis->execCommandCoro("EXPIRE %s %d", key.c_str(), 31 * 86400);
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "device send count: " << e.what();
    }
}
}  // namespace chat
