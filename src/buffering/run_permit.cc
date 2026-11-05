#include "buffering/run_permit.h"

#include <stdexcept>
#include <utility>

namespace chat
{
RunPermit::RunPermit(std::string boot, std::chrono::milliseconds lifetime, Now now)
    : boot_(std::move(boot)), lifetime_(lifetime), now_(std::move(now))
{
    if (boot_.empty() || lifetime_ <= std::chrono::milliseconds::zero())
        throw std::invalid_argument("invalid run permit configuration");
}

bool RunPermit::fence(const std::string &expectedEpoch, const std::string &epoch,
                      uint64_t revision)
{
    if (epoch.empty()) throw std::invalid_argument("empty coordinator epoch");
    std::lock_guard lock(mutex_);
    // Compare-and-swap prevents a delayed old coordinator fence from replacing
    // a newer epoch. The control endpoint also binds this to the boot ID.
    if (epoch_ != expectedEpoch || (epoch_ == epoch && revision <= revision_))
        return false;
    epoch_ = epoch;
    revision_ = revision;
    mode_ = Mode::Paused;
    pending_.reset();
    deadline_ = {};
    return true;
}

bool RunPermit::pause(const std::string &epoch, uint64_t revision)
{
    std::lock_guard lock(mutex_);
    if (epoch != epoch_ || revision < revision_) return false;
    if (revision == revision_ && mode_ != Mode::Paused) return false;
    revision_ = revision;
    mode_ = Mode::Paused;
    deadline_ = {};
    pending_.reset();
    return true;
}

std::optional<RunPermit::Challenge> RunPermit::challenge(
    const std::string &epoch, uint64_t revision, std::string unpredictableNonce)
{
    std::lock_guard lock(mutex_);
    if (epoch != epoch_ || revision != revision_ || unpredictableNonce.empty())
        return std::nullopt;
    pending_ = Challenge{boot_, epoch_, std::move(unpredictableNonce), revision_};
    // Delayed delivery consumes the lease budget, never extends it.
    challengeDeadline_ = now_() + lifetime_;
    return pending_;
}

bool RunPermit::grant(const Challenge &challenge, Mode mode)
{
    std::lock_guard lock(mutex_);
    if (mode == Mode::Paused || !pending_ || challenge.boot != boot_ ||
        challenge.epoch != epoch_ || challenge.revision != revision_ ||
        challenge.nonce != pending_->nonce || now_() >= challengeDeadline_)
        return false;
    pending_.reset();
    deadline_ = challengeDeadline_;
    mode_ = mode;
    return true;
}

std::optional<RunPermit::Stamp> RunPermit::enter() const
{
    std::lock_guard lock(mutex_);
    if (mode_ == Mode::Paused || now_() >= deadline_) return std::nullopt;
    return Stamp{epoch_, revision_, deadline_};
}

bool RunPermit::valid(const Stamp &stamp) const
{
    std::lock_guard lock(mutex_);
    return mode_ != Mode::Paused && stamp.epoch == epoch_ &&
           stamp.revision == revision_ && now_() < stamp.deadline &&
           now_() < deadline_;
}

RunPermit::State RunPermit::state() const
{
    std::lock_guard lock(mutex_);
    return {epoch_, revision_, now_() >= deadline_ ? Mode::Paused : mode_};
}
RunPermit::Mode RunPermit::mode() const { return state().mode; }
}  // namespace chat
