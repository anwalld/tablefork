#pragma once
#include <chrono>
#include <cstddef>
#include <functional>
#include <mutex>
#include <stdexcept>

namespace chat
{
// One instance per backend. No saved credits; a delayed poll starts at most
// one task, then schedules the next slot from now, not from a stale deadline.
class LeakyGate
{
  public:
    using Clock = std::chrono::steady_clock;
    using Now = std::function<Clock::time_point()>;
    LeakyGate(size_t rate, size_t maxRunning, Now now = [] { return Clock::now(); })
        : limit_(maxRunning), now_(std::move(now))
    {
        if (!rate || rate > 1000000000 || !maxRunning)
            throw std::invalid_argument("invalid drain limits");
        interval_ = std::chrono::nanoseconds((1000000000ULL + rate - 1) / rate);
    }
    bool tryStart()
    {
        std::lock_guard lock(mutex_);
        const auto now = now_();
        if (running_ >= limit_ || (started_ && now < next_)) return false;
        started_ = true;
        next_ = now + interval_;
        ++running_;
        return true;
    }
    void finish()
    {
        std::lock_guard lock(mutex_);
        if (!running_) throw std::logic_error("drain slot released twice");
        --running_;
    }
  private:
    const size_t limit_;
    const Now now_;
    std::chrono::nanoseconds interval_{};
    std::mutex mutex_;
    Clock::time_point next_{};
    size_t running_{0};
    bool started_{false};
};
}  // namespace chat
