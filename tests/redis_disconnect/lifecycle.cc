#include <drogon/drogon.h>
#include <array>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <string>

int main(int argc, char **argv)
{
    const std::string mode = argc > 1 ? argv[1] : "reconnect";
    const bool invalid = mode == "invalid-auth" || mode == "invalid-db";
    std::array<std::atomic<int>, 200> completions{};
    std::atomic<int> successes{0}, errors{0}, deliveries{0}, recovered{0};
    auto &app = drogon::app();
    app.createRedisClient(std::getenv("REDIS_IP"), 6379, "default",
                          mode == "invalid-auth" ? "invalid-test-password" : "",
                          2, false, 0.5, mode == "invalid-db" ? 999 : 0);
    std::shared_ptr<drogon::nosql::RedisSubscriber> subscriber;
    app.registerBeginningAdvice([&] {
        if (!invalid)
        {
            subscriber = app.getRedisClient()->newSubscriber();
            subscriber->subscribe("lifecycle-probe", [&](const auto &, const auto &) { ++deliveries; });
            app.getLoop()->runAfter(8.2, [&] { subscriber->unsubscribe("lifecycle-probe"); });
        }
        for (size_t index = 0; index < completions.size(); ++index)
        {
            app.getLoop()->runAfter(0.04 * index, [&, index] {
                app.getRedisClient()->execCommandAsync(
                    [&, index](const auto &) {
                        ++completions[index];
                        ++successes;
                        if (index >= 150) ++recovered;
                    },
                    [&, index](const auto &) { ++completions[index]; ++errors; },
                    "PUBLISH lifecycle-probe %d", static_cast<int>(index));
            });
        }
        app.getLoop()->runAfter(12, [&] {
            bool once = true;
            for (const auto &completion : completions) once = once && completion.load() == 1;
            std::cout << "successes=" << successes << " errors=" << errors
                      << " deliveries=" << deliveries << " recovered=" << recovered
                      << " exactly_once=" << once << std::endl;
            const bool passed = once && (invalid ? successes == 0 : recovered > 0 && deliveries > 0);
            std::_Exit(passed ? 0 : 2);
        });
        std::cout << "READY" << std::endl;
    });
    app.run();
}
