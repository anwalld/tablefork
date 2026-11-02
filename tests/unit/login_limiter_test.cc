#include <drogon/drogon_test.h>

#include "services/login_limiter.h"

using chat::backoffSchedule;

DROGON_TEST(BackoffDoublesUpToTheMax)
{
    CHECK(backoffSchedule(1, 60) == std::vector<int>({1, 2, 4, 8, 16, 32, 60}));
    CHECK(backoffSchedule(5, 30) == std::vector<int>({5, 10, 20, 30}));
}

DROGON_TEST(BackoffWithBaseAtOrAboveMaxIsFlat)
{
    CHECK(backoffSchedule(60, 60) == std::vector<int>({60}));
    CHECK(backoffSchedule(90, 60) == std::vector<int>({60}));
}

DROGON_TEST(BackoffNeverWaitsZero)
{
    CHECK(backoffSchedule(0, 4) == std::vector<int>({1, 2, 4}));
}

DROGON_TEST(LimiterKeysHideTheirInput)
{
    const auto key = chat::LoginLimiter::nameKey("alice");
    CHECK(key.rfind("login_fail:u:", 0) == 0);
    CHECK(key.find("alice") == std::string::npos);
    CHECK(key != chat::LoginLimiter::nameKey("Alice"));
    CHECK(chat::LoginLimiter::deviceKey("t").rfind("login_fail:d:", 0) == 0);
}
