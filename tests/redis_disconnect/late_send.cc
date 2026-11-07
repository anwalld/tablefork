#include <drogon/drogon.h>
#include <trantor/net/EventLoopThread.h>
#include <atomic>
#include <iostream>
#include "RedisConnection.h"
int main()
{
    auto &app = drogon::app();
    trantor::EventLoopThread redisThread;
    redisThread.run();
    std::shared_ptr<drogon::nosql::RedisConnection> connection;
    std::atomic<int> callbacks{0};
    app.registerBeginningAdvice([&] {
        // Construct and install callbacks on the same event loop. The
        // constructor's queued connect cannot overtake callback registration.
        redisThread.getLoop()->queueInLoop([&] {
            connection = std::make_shared<drogon::nosql::RedisConnection>(
                trantor::InetAddress(std::getenv("REDIS_IP"), 6379), "", "", 0, redisThread.getLoop());
            connection->setConnectCallback([&](auto &&) { std::cout << "READY" << std::endl; });
            connection->setDisconnectCallback([&](auto &&) {
                // Allow hiredis to free its context before a cross-thread send.
                app.getLoop()->runAfter(0.1, [&] {
                    connection->sendCommand(
                        [&](const auto &) { std::cerr << "unexpected success" << std::endl; std::_Exit(3); },
                        [&](const auto &error) {
                            ++callbacks;
                            std::cout << "late_send_error=" << error.what() << std::endl;
                        }, "PING");
                    app.getLoop()->runAfter(0.5, [&] {
                        std::cout << "callbacks=" << callbacks.load() << std::endl;
                        std::_Exit(callbacks.load() == 1 ? 0 : 2);
                    });
                });
            });
        });
        app.getLoop()->runAfter(10, [] { std::_Exit(4); });
    });
    app.run();
}
