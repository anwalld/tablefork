#include <drogon/drogon_test.h>
#include "common/trace.h"

DROGON_TEST(TraceIdentifiersStayStableAcrossStages)
{
    CHECK(chat::safeTraceId("room:message-123") == "room:message-123");
    const auto encoded = chat::safeTraceId("untrusted value\nwith delimiters");
    CHECK(encoded == chat::safeTraceId("untrusted value\nwith delimiters"));
    CHECK(encoded.size() == 64);
    CHECK(encoded.find_first_not_of("0123456789abcdef") == std::string::npos);
}
