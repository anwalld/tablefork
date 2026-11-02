#pragma once

#include <cstddef>
#include <string>
#include <vector>

namespace chat
{
struct Config
{
    std::string mysqlHost;
    unsigned short mysqlPort;
    std::string mysqlDatabase;
    std::string mysqlUser;
    std::string mysqlPassword;
    size_t mysqlConnections;

    std::string redisHost;
    unsigned short redisPort;
    std::string redisPassword;
    size_t redisConnections;

    int sessionTtlSeconds;
    bool cookieSecure;
    std::vector<std::string> allowedOrigins;

    size_t workerCount;
    size_t workerQueueLimit;
    size_t hotCacheSize;

    static const Config &get();
};
}  // namespace chat
