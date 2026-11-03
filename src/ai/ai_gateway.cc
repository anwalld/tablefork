#include "ai/ai_gateway.h"

#include <drogon/utils/Utilities.h>
#include <grpcpp/grpcpp.h>
#include <deque>
#include <set>
#include <trantor/utils/Logger.h>

namespace chat
{
namespace
{
std::unique_ptr<AiGateway> gGateway;
}  // namespace

AiGateway *aiGateway()
{
    return gGateway.get();
}

void setAiGateway(std::unique_ptr<AiGateway> gateway)
{
    gGateway = std::move(gateway);
}

// Serializes callbacks with destruction, including already queued timers.
struct AiGateway::Callbacks
{
    std::mutex mutex;
    AiGateway *owner;
    explicit Callbacks(AiGateway *value) : owner(value) {}
};

// One bidirectional stream. gRPC allows a single outstanding write, so writes
// are queued and the next starts when the previous one is done.
class AiGateway::Stream : public grpc::ClientBidiReactor<ai::GatewayEvent, ai::AiAction>
{
  public:
    Stream(AiGateway &owner, std::deque<ai::GatewayEvent> initial)
        : callbacks_(owner.callbacks_), started_(std::chrono::steady_clock::now())
    {
        context_.AddMetadata("authorization", "Bearer " + owner.options_.token);
        owner.stub_->async()->Connect(&context_, this);
        queue_ = std::move(initial);
        for (const auto &event : queue_) queuedIds_.insert(event.event_id());
        StartRead(&incoming_);
        if (!queue_.empty())
        {
            writing_ = true;
            StartWrite(&queue_.front());
        }
        StartCall();
    }

    void write(ai::GatewayEvent event)
    {
        std::lock_guard lock(writeMutex_);
        if (finished_ || queuedIds_.contains(event.event_id()))
        {
            return;
        }
        queuedIds_.insert(event.event_id());
        queue_.push_back(std::move(event));
        if (!writing_)
        {
            writing_ = true;
            StartWrite(&queue_.front());
        }
    }

    void cancel()
    {
        context_.TryCancel();
    }

    void OnWriteDone(bool ok) override
    {
        std::lock_guard lock(writeMutex_);
        if (!ok)
        {
            // The stream is broken; OnDone follows.
            writing_ = false;
            return;
        }
        queuedIds_.erase(queue_.front().event_id());
        queue_.pop_front();
        if (!queue_.empty() && !finished_)
        {
            StartWrite(&queue_.front());
        }
        else
        {
            writing_ = false;
        }
    }

    void OnReadDone(bool ok) override
    {
        if (!ok)
        {
            return;
        }
        received_ = true;
        {
            std::lock_guard lock(callbacks_->mutex);
            if (callbacks_->owner) callbacks_->owner->onAction(std::move(incoming_));
        }
        incoming_.Clear();
        StartRead(&incoming_);
    }

    void OnDone(const grpc::Status &status) override
    {
        {
            std::lock_guard lock(writeMutex_);
            finished_ = true;
        }
        if (!status.ok())
        {
            LOG_WARN << "AI gateway stream ended: " << status.error_message();
        }
        const auto lived = std::chrono::steady_clock::now() - started_;
        std::lock_guard lock(callbacks_->mutex);
        if (callbacks_->owner)
            callbacks_->owner->onStreamDone(this, received_ || lived > std::chrono::seconds(5));
    }

  private:
    std::shared_ptr<Callbacks> callbacks_;
    grpc::ClientContext context_;
    ai::AiAction incoming_;
    std::mutex writeMutex_;
    std::deque<ai::GatewayEvent> queue_;
    std::set<int64_t> queuedIds_;
    bool writing_{false};
    bool finished_{false};
    bool received_{false};
    std::chrono::steady_clock::time_point started_;
};

AiGateway::AiGateway(Options options, ActionHandler handler)
    : callbacks_(std::make_shared<Callbacks>(this)),
      options_(std::move(options)),
      handler_(std::move(handler)),
      instanceId_(drogon::utils::getUuid()),
      channel_(grpc::CreateChannel(options_.address, grpc::InsecureChannelCredentials())),
      stub_(ai::AiGateway::NewStub(channel_)),
      backoff_(options_.minBackoff)
{
}

AiGateway::~AiGateway()
{
    stop();
    {
        std::unique_lock lock(mutex_);
        streamDone_.wait(lock, [this] { return streamFinished_; });
    }
    std::lock_guard lock(callbacks_->mutex);
    callbacks_->owner = nullptr;
}

void AiGateway::start()
{
    const auto callbacks = callbacks_;
    options_.loop->queueInLoop([callbacks] {
        std::lock_guard guard(callbacks->mutex);
        auto *owner = callbacks->owner;
        if (!owner) return;
        owner->connect();
        std::lock_guard lock(owner->mutex_);
        if (owner->stopped_) return;
        const auto interval = std::chrono::duration<double>(owner->options_.ackTimeout).count();
        owner->retryTimer_ = owner->options_.loop->runEvery(interval, [callbacks] {
            std::lock_guard guard(callbacks->mutex);
            if (callbacks->owner) callbacks->owner->retryUnacked();
        });
    });
}

void AiGateway::stop()
{
    std::unique_lock lock(mutex_);
    stopped_ = true;
    if (retryTimer_)
    {
        options_.loop->invalidateTimer(retryTimer_);
        retryTimer_ = 0;
    }
    if (stream_) stream_->cancel();
}

void AiGateway::connect()
{
    std::lock_guard lock(mutex_);
    if (stopped_ || stream_)
    {
        return;
    }
    std::deque<ai::GatewayEvent> initial;
    for (const auto &[id, event] : unacked_)
    {
        initial.push_back(event);
        lastSent_[id] = std::chrono::steady_clock::now();
    }
    streamFinished_ = false;
    stream_ = std::make_unique<Stream>(*this, std::move(initial));
}

void AiGateway::send(ai::GatewayEvent event)
{
    std::lock_guard lock(mutex_);
    event.set_instance_id(instanceId_);
    event.set_event_id(nextEventId_++);
    if (unacked_.size() >= options_.maxUnacked)
    {
        LOG_ERROR << "AI gateway: too many unacknowledged events, dropping event "
                  << unacked_.begin()->first;
        lastSent_.erase(unacked_.begin()->first);
        unacked_.erase(unacked_.begin());
    }
    unacked_.emplace(event.event_id(), event);
    lastSent_[event.event_id()] = std::chrono::steady_clock::now();
    if (stream_)
    {
        stream_->write(std::move(event));
    }
}

void AiGateway::onAction(ai::AiAction action)
{
    if (action.has_ack())
    {
        std::lock_guard lock(mutex_);
        unacked_.erase(action.ack().event_id());
        lastSent_.erase(action.ack().event_id());
        return;
    }
    options_.loop->queueInLoop(
        [callbacks = callbacks_, action = std::move(action)]() mutable {
            std::lock_guard guard(callbacks->mutex);
            if (callbacks->owner) callbacks->owner->handler_(std::move(action));
        });
}

void AiGateway::onStreamDone(Stream *stream, bool wasHealthy)
{
    std::chrono::milliseconds delay;
    {
        std::lock_guard lock(mutex_);
        if (wasHealthy)
        {
            backoff_ = options_.minBackoff;
        }
        delay = backoff_;
        backoff_ = std::min(backoff_ * 2, options_.maxBackoff);
    }
    const auto callbacks = callbacks_;
    options_.loop->queueInLoop([callbacks, stream, delay] {
        std::lock_guard guard(callbacks->mutex);
        auto *owner = callbacks->owner;
        if (!owner) return;
        {
            std::lock_guard lock(owner->mutex_);
            if (owner->stream_.get() == stream) owner->stream_.reset();
            if (owner->stopped_) return;
        }
        owner->options_.loop->runAfter(std::chrono::duration<double>(delay).count(), [callbacks] {
            std::lock_guard guard(callbacks->mutex);
            if (callbacks->owner) callbacks->owner->connect();
        });
    });
    {
        std::lock_guard lock(mutex_);
        streamFinished_ = true;
        streamDone_.notify_all();
    }
}

void AiGateway::retryUnacked()
{
    std::lock_guard lock(mutex_);
    if (stopped_ || !stream_) return;
    const auto now = std::chrono::steady_clock::now();
    for (const auto &[id, event] : unacked_)
    {
        if (now - lastSent_[id] >= options_.ackTimeout)
        {
            stream_->write(event);
            lastSent_[id] = now;
        }
    }
}

size_t AiGateway::unackedCount()
{
    std::lock_guard lock(mutex_);
    return unacked_.size();
}

namespace
{
struct UnaryCall
{
    grpc::ClientContext context;
    ai::StoreKeyRequest request;
    ai::StoreKeyReply reply;
    grpc::Status status;
};

struct StoreKeyAwaiter
{
    ai::AiGateway::Stub *stub;
    std::shared_ptr<UnaryCall> call;

    bool await_ready() const noexcept
    {
        return false;
    }

    void await_suspend(std::coroutine_handle<> handle)
    {
        auto *loop = trantor::EventLoop::getEventLoopOfCurrentThread();
        stub->async()->StoreKey(&call->context,
                                &call->request,
                                &call->reply,
                                [call = call, handle, loop](grpc::Status status) {
                                    call->status = std::move(status);
                                    loop->queueInLoop([handle] { handle.resume(); });
                                });
    }

    void await_resume() const noexcept
    {
    }
};
}  // namespace

drogon::Task<AiGateway::StoreKeyResult> AiGateway::storeKey(int64_t userId,
                                                            std::string provider,
                                                            std::string apiKey)
{
    auto call = std::make_shared<UnaryCall>();
    call->context.AddMetadata("authorization", "Bearer " + options_.token);
    call->context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(30));
    call->request.set_user_id(userId);
    call->request.set_provider(std::move(provider));
    call->request.set_api_key(std::move(apiKey));
    co_await StoreKeyAwaiter{stub_.get(), call};
    if (!call->status.ok())
    {
        LOG_WARN << "AI gateway StoreKey: " << call->status.error_message();
        co_return StoreKeyResult{false, {}};
    }
    co_return StoreKeyResult{true, call->reply};
}
}  // namespace chat
