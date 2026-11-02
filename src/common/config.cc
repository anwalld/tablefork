#include "common/config.h"

#include <cerrno>
#include <cstdlib>
#include <drogon/utils/Utilities.h>

namespace chat
{
namespace
{
std::string envOr(const char *name, const std::string &fallback)
{
    const char *value = std::getenv(name);
    return value && *value ? value : fallback;
}

long envOr(const char *name, long fallback)
{
    const char *value = std::getenv(name);
    return value && *value ? std::atol(value) : fallback;
}

int positiveEnvOr(const char *name, long fallback)
{
    return static_cast<int>(positiveOr(std::getenv(name), fallback));
}

Config load()
{
    Config c;
    c.mysqlHost = envOr("MYSQL_HOST", "mysql");
    c.mysqlPort = static_cast<unsigned short>(envOr("MYSQL_PORT", 3306L));
    c.mysqlDatabase = envOr("MYSQL_DATABASE", "chat");
    c.mysqlUser = envOr("MYSQL_USER", "chat");
    c.mysqlPassword = envOr("MYSQL_PASSWORD", "");
    c.mysqlConnections = envOr("MYSQL_CONNECTIONS", 24L);

    c.redisHost = envOr("REDIS_HOST", "redis-master");
    c.redisPort = static_cast<unsigned short>(envOr("REDIS_PORT", 6379L));
    c.redisPassword = envOr("REDIS_PASSWORD", "");
    c.redisConnections = envOr("REDIS_CONNECTIONS", 4L);

    c.sessionTtlSeconds = static_cast<int>(envOr("SESSION_TTL_SECONDS", 604800L));
    c.cookieSecure = envOr("COOKIE_SECURE", 0L) != 0;
    c.allowedOrigins = drogon::utils::splitString(
        envOr("ALLOWED_ORIGINS", "http://localhost:8080"), ",");

    c.workerCount = envOr("WORKER_COUNT", 16L);
    c.workerQueueLimit = envOr("WORKER_QUEUE_LIMIT", 1024L);
    c.hotCacheSize = envOr("HOT_CACHE_SIZE", 200L);

    c.passwordBlocklistFile = envOr(
        "PASSWORD_BLOCKLIST_FILE", "/usr/local/share/chat/common-passwords.txt");
    c.loginFreeAttempts = positiveEnvOr("LOGIN_FREE_ATTEMPTS", 5);
    c.loginBackoffBaseSeconds = positiveEnvOr("LOGIN_BACKOFF_BASE_SECONDS", 1);
    c.loginBackoffMaxSeconds = positiveEnvOr("LOGIN_BACKOFF_MAX_SECONDS", 60);
    c.loginFailTtlSeconds = positiveEnvOr("LOGIN_FAIL_TTL_SECONDS", 900);
    c.deviceTtlDays = positiveEnvOr("DEVICE_TTL_DAYS", 180);
    c.messageRateLimit = positiveEnvOr("MESSAGE_RATE_LIMIT", 30);
    c.messageRateWindowSeconds = positiveEnvOr("MESSAGE_RATE_WINDOW_SECONDS", 10);
    return c;
}
}  // namespace

long positiveOr(const char *text, long fallback)
{
    if (!text || !*text)
    {
        return fallback;
    }
    char *end = nullptr;
    errno = 0;
    const long value = std::strtol(text, &end, 10);
    if (errno || *end || value <= 0 || value > 1000000000L)
    {
        return fallback;
    }
    return value;
}

const Config &Config::get()
{
    static const Config config = load();
    return config;
}
}  // namespace chat
