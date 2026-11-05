#include <drogon/drogon_test.h>
#include "buffering/leaky_gate.h"
using namespace std::chrono_literals;
DROGON_TEST(DrainSlotsHaveNoCatchupBurstAndBoundConcurrency)
{
    chat::LeakyGate::Clock::time_point now{};
    chat::LeakyGate gate(50, 8, [&] { return now; });
    for (int i=0;i<8;++i)
    {
        CHECK(gate.tryStart());
        CHECK(!gate.tryStart());
        now += 20ms;
    }
    CHECK(!gate.tryStart());
    gate.finish();
    CHECK(gate.tryStart());
    for (int i=0;i<8;++i) gate.finish();
    now += 10s;
    CHECK(gate.tryStart());
    CHECK(!gate.tryStart());
    now += 19ms;
    CHECK(!gate.tryStart());
    now += 1ms;
    CHECK(gate.tryStart());
    chat::LeakyGate otherBackend(50, 8, [&] { return now; });
    CHECK(otherBackend.tryStart());
}
