#include <drogon/drogon_test.h>
#include "buffering/run_permit.h"

using chat::RunPermit;
using namespace std::chrono_literals;

DROGON_TEST(PermitStartsClosedAndNeverRenewsFromDelivery)
{
    RunPermit::Time now{};
    RunPermit permit("boot-a", 5s, [&] { return now; });
    CHECK(!permit.enter());
    CHECK(permit.fence("", "epoch-1", 1));
    auto challenge = permit.challenge("epoch-1", 1, "nonce-1");
    REQUIRE(challenge);
    now += 4s;
    CHECK(permit.grant(*challenge, RunPermit::Mode::Draining));
    auto stamp = permit.enter();
    REQUIRE(stamp);
    CHECK(!permit.grant(*challenge, RunPermit::Mode::Direct));
    now += 1s;
    CHECK(!permit.enter());
    CHECK(!permit.valid(*stamp));
    CHECK(permit.mode() == RunPermit::Mode::Paused);
}

DROGON_TEST(PermitRejectsFrozenDelayedWrongBootAndOldPauseGrants)
{
    RunPermit::Time now{};
    RunPermit permit("boot-a", 5s, [&] { return now; });
    CHECK(permit.fence("", "epoch-1", 1));
    auto old = *permit.challenge("epoch-1", 1, "nonce-1");
    now += 5s;
    CHECK(!permit.grant(old, RunPermit::Mode::Direct));
    auto fresh = *permit.challenge("epoch-1", 1, "nonce-2");
    auto wrongBoot = fresh;
    wrongBoot.boot = "boot-before-restart";
    CHECK(!permit.grant(wrongBoot, RunPermit::Mode::Direct));
    CHECK(permit.grant(fresh, RunPermit::Mode::Direct));
    auto stamp = *permit.enter();
    CHECK(permit.pause("epoch-1", 2));
    CHECK(!permit.valid(stamp));
    CHECK(!permit.grant(fresh, RunPermit::Mode::Direct));
    CHECK(!permit.challenge("epoch-1", 1, "nonce-3"));
    CHECK(!permit.pause("epoch-1", 1));
    CHECK(permit.mode() == RunPermit::Mode::Paused);
    CHECK(permit.fence("epoch-1", "epoch-2", 1));
    CHECK(!permit.fence("", "epoch-1", 1));
    CHECK(!permit.challenge("epoch-1", 2, "nonce-4"));
    auto current = *permit.challenge("epoch-2", 1, "nonce-5");
    CHECK(permit.grant(current, RunPermit::Mode::Direct));
    CHECK(!permit.valid(stamp));
    now += 60s; // no timer callback ran while the process was frozen
    CHECK(!permit.enter());
}

DROGON_TEST(PermitRenewalDoesNotReviveExpiredRequestStamp)
{
    RunPermit::Time now{};
    RunPermit permit("boot-a", 5s, [&] { return now; });
    CHECK(permit.fence("", "epoch-1", 1));
    CHECK(permit.grant(*permit.challenge("epoch-1", 1, "n1"), RunPermit::Mode::Direct));
    auto stamp = *permit.enter();
    now += 4s;
    auto superseded = *permit.challenge("epoch-1", 1, "n2");
    auto current = *permit.challenge("epoch-1", 1, "n3");
    CHECK(!permit.grant(superseded, RunPermit::Mode::Direct));
    CHECK(permit.grant(current, RunPermit::Mode::Direct));
    now += 1s;
    CHECK(permit.enter());
    CHECK(!permit.valid(stamp));
    CHECK(!permit.pause("epoch-1", 1));
    CHECK(permit.pause("epoch-1", 2));
    CHECK(!permit.fence("epoch-1", "epoch-1", 1)); // old duplicate fence cannot roll revision back
    CHECK(!permit.challenge("epoch-1", 1, "n4"));
}
