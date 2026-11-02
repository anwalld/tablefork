#include <drogon/drogon_test.h>

#include "services/message_rate_limiter.h"

using chat::messageRateKey;
using chat::secondsLeftInWindow;

DROGON_TEST(MessageRateKeyChangesPerWindow)
{
    CHECK(messageRateKey(7, 100, 10) == "msg_rate:7:10");
    CHECK(messageRateKey(7, 109, 10) == "msg_rate:7:10");
    CHECK(messageRateKey(7, 110, 10) == "msg_rate:7:11");
    CHECK(messageRateKey(8, 100, 10) == "msg_rate:8:10");
}

DROGON_TEST(SecondsLeftCountsToTheWindowEnd)
{
    CHECK(secondsLeftInWindow(100, 10) == 10);
    CHECK(secondsLeftInWindow(103, 10) == 7);
    CHECK(secondsLeftInWindow(109, 10) == 1);
}
