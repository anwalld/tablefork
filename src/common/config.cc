#include "common/config.h"

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
    return c;
}
}  // namespace

const Config &Config::get()
{
    static const Config config = load();
    return config;
}
}  // namespace chat
