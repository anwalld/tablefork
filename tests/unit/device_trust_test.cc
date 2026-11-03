#include <drogon/drogon_test.h>

#include "services/device_trust.h"

using chat::deviceKind;
using chat::recentDays;
using chat::shouldReplace;

DROGON_TEST(DeviceKindFromUserAgent)
{
    CHECK(deviceKind("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148") == "phone");
    CHECK(deviceKind("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/120 Mobile Safari/537.36") == "phone");
    CHECK(deviceKind("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)") == "phone");
    CHECK(deviceKind("Mozilla/5.0 (Linux; Android 13; SM-X700) Chrome/120 Safari/537.36") == "phone");
    CHECK(deviceKind("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15") == "computer");
    CHECK(deviceKind("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120") == "computer");
    CHECK(deviceKind("python-requests/2.32.3") == "computer");
    CHECK(deviceKind("") == "computer");
}

DROGON_TEST(ReplaceOnlyAboveOneAndAHalfTimes)
{
    CHECK(!shouldReplace(15, 10));  // exactly 1.5 times is not enough
    CHECK(shouldReplace(16, 10));
    CHECK(!shouldReplace(0, 0));
    CHECK(shouldReplace(1, 0));
    CHECK(!shouldReplace(3, 2));
    CHECK(shouldReplace(4, 2));
}

DROGON_TEST(RecentDaysAreDistinctDatesTodayFirst)
{
    const auto days = recentDays(30);
    REQUIRE(days.size() == 30);
    CHECK(days[0].size() == 8);
    CHECK(days[0] > days[1]);
    CHECK(days[1] > days[29]);
}
