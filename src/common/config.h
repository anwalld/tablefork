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
    // How often each instance checks that its connections' sessions still exist.
    double sessionCheckSeconds;
    bool cookieSecure;
    std::vector<std::string> allowedOrigins;

    size_t workerCount;
    size_t workerQueueLimit;
    size_t hotCacheSize;

    // Common passwords that registration rejects, one per line.
    std::string passwordBlocklistFile;

    // Login delay: attempts allowed before waiting, then a wait that doubles
    // from the base up to the max. A name's count expires after the TTL.
    int loginFreeAttempts;
    int loginBackoffBaseSeconds;
    int loginBackoffMaxSeconds;
    int loginFailTtlSeconds;
    int deviceTtlDays;

    // The AI service, as host:port; empty to run without AIs.
    std::string aiGateway;
    std::string aiToken;

    // Messages a user may send per window, across all connections and rooms.
    int messageRateLimit;
    int messageRateWindowSeconds;

    static const Config &get();
};

// The positive integer in text, or fallback when text is empty, not a number
// or not positive.
long positiveOr(const char *text, long fallback);
}  // namespace chat
