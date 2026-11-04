#include <arpa/inet.h>
#include <drogon/drogon.h>
#include <netdb.h>
#include <stdexcept>
#include <string>

#include "ai/ai_actions.h"
#include "ai/ai_gateway.h"
#include "common/config.h"
#include "common/util.h"
#include "services/password_policy.h"
#include "ws/session_watcher.h"
#include "ws/room_hub.h"

namespace
{
// Drogon's Redis client only accepts an IP address.
std::string resolveIpv4(const std::string &host)
{
    addrinfo hints{};
    hints.ai_family = AF_INET;
    addrinfo *result = nullptr;
    if (getaddrinfo(host.c_str(), nullptr, &hints, &result) != 0)
    {
        throw std::runtime_error("cannot resolve " + host);
    }
    char ip[INET_ADDRSTRLEN];
    inet_ntop(AF_INET,
              &reinterpret_cast<sockaddr_in *>(result->ai_addr)->sin_addr,
              ip,
              sizeof(ip));
    freeaddrinfo(result);
    return ip;
}
}  // namespace

int main()
{
    const auto &config = chat::Config::get();
    // Fails at startup rather than on the first registration.
    chat::PasswordPolicy::instance();
    if (!config.aiGateway.empty())
    {
        auto gateway = std::make_unique<chat::AiGateway>(
            chat::AiGateway::Options{config.aiGateway, config.aiToken, drogon::app().getLoop()},
            chat::handleAiAction);
        gateway->start();
        chat::setAiGateway(std::move(gateway));
    }
    drogon::app()
        .addListener("0.0.0.0", 8000)
        .setThreadNum(0)
        .createDbClient("mysql",
                        config.mysqlHost,
                        config.mysqlPort,
                        config.mysqlDatabase,
                        config.mysqlUser,
                        config.mysqlPassword,
                        config.mysqlConnections,
                        "",
                        "default",
                        false,
                        "utf8mb4",
                        10.0)
        .createRedisClient(resolveIpv4(config.redisHost),
                           config.redisPort,
                           "default",
                           config.redisPassword,
                           config.redisConnections,
                           false,
                           2.0)
        // Log routing evidence per instance without cookies or request bodies.
        .registerPostHandlingAdvice([](const drogon::HttpRequestPtr &req,
                                       const drogon::HttpResponsePtr &resp) {
            LOG_INFO << req->methodString() << " " << req->getPath() << " "
                     << static_cast<int>(resp->statusCode());
        })
        // Redis clients exist only once the app runs.
        .registerBeginningAdvice([] {
            chat::RoomHub::instance().start();
            chat::SessionWatcher::instance().start();
        })
        .setExceptionHandler([](const std::exception &e,
                                const drogon::HttpRequestPtr &req,
                                std::function<void(const drogon::HttpResponsePtr &)>
                                    &&callback) {
            if (const auto *error = dynamic_cast<const chat::ApiError *>(&e))
            {
                callback(chat::errorResponse(*error));
                return;
            }
            LOG_ERROR << req->getPath() << ": " << e.what();
            const auto error = chat::ApiError::unavailable();
            callback(chat::errorResponse(error));
        })
        .run();
    return 0;
}
