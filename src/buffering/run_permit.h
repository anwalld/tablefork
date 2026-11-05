#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <string>

namespace chat
{
// This is an execution fence, not proof that an already-sent command rolled
// back. Callers retain their stamp across awaits and check it before the next
// side effect. Only the authenticated coordinator may change an epoch.
class RunPermit
{
  public:
    using Clock = std::chrono::steady_clock;
    using Time = Clock::time_point;
    using Now = std::function<Time()>;
    enum class Mode { Paused, Draining, Direct };
    struct Challenge
    {
        std::string boot;
        std::string epoch;
        std::string nonce;
        uint64_t revision;
    };
    struct Stamp
    {
        std::string epoch;
        uint64_t revision;
        Time deadline;
    };

    RunPermit(std::string boot, std::chrono::milliseconds lifetime,
              Now now = [] { return Clock::now(); });
    // A new coordinator must fence this boot before issuing a challenge.
    bool fence(const std::string &expectedEpoch, const std::string &epoch,
               uint64_t revision);
    bool pause(const std::string &epoch, uint64_t revision);
    std::optional<Challenge> challenge(const std::string &epoch,
                                      uint64_t revision,
                                      std::string unpredictableNonce);
    bool grant(const Challenge &challenge, Mode mode);
    std::optional<Stamp> enter() const;
    bool valid(const Stamp &stamp) const;
    struct State { std::string epoch; uint64_t revision; Mode mode; };
    State state() const;
    Mode mode() const;
    const std::string &boot() const { return boot_; }

  private:
    const std::string boot_;
    const std::chrono::milliseconds lifetime_;
    const Now now_;
    mutable std::mutex mutex_;
    std::string epoch_;
    uint64_t revision_{0};
    Mode mode_{Mode::Paused};
    Time deadline_{};
    std::optional<Challenge> pending_;
    Time challengeDeadline_{};
};
}  // namespace chat
