#include "ai/ai_gateway.h"

#include <drogon/utils/Utilities.h>
#include <grpcpp/grpcpp.h>
#include <deque>
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

// One bidirectional stream. gRPC allows a single outstanding write, so writes
// are queued and the next starts when the previous one is done.
class AiGateway::Stream : public grpc::ClientBidiReactor<ai::GatewayEvent, ai::AiAction>
{
  public:
    Stream(AiGateway &owner, std::deque<ai::GatewayEvent> initial)
        : owner_(owner), started_(std::chrono::steady_clock::now())
    {
        context_.AddMetadata("authorization", "Bearer " + owner_.options_.token);
        owner_.stub_->async()->Connect(&context_, this);
        queue_ = std::move(initial);
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
        if (finished_)
        {
            return;
        }
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
        owner_.onAction(std::move(incoming_));
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
        owner_.onStreamDone(this, received_ || lived > std::chrono::seconds(5));
    }

  private:
    AiGateway &owner_;
    grpc::ClientContext context_;
    ai::AiAction incoming_;
    std::mutex writeMutex_;
    std::deque<ai::GatewayEvent> queue_;
    bool writing_{false};
    bool finished_{false};
    bool received_{false};
    std::chrono::steady_clock::time_point started_;
};

AiGateway::AiGateway(Options options, ActionHandler handler)
    : options_(std::move(options)),
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
}

void AiGateway::start()
{
    options_.loop->queueInLoop([this] { connect(); });
}

void AiGateway::stop()
{
    std::unique_lock lock(mutex_);
    stopped_ = true;
    if (stream_)
    {
        auto *stream = stream_.get();
        lock.unlock();
        stream->cancel();
    }
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
    }
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
        unacked_.erase(unacked_.begin());
    }
    unacked_.emplace(event.event_id(), event);
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
        return;
    }
    options_.loop->queueInLoop(
        [this, action = std::move(action)]() mutable { handler_(std::move(action)); });
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
    // The reactor may not be destroyed inside its own OnDone.
    options_.loop->queueInLoop([this, stream, delay] {
        std::unique_ptr<Stream> finished;
        {
            std::lock_guard lock(mutex_);
            if (stream_.get() == stream)
            {
                finished = std::move(stream_);
            }
            if (stopped_)
            {
                return;
            }
        }
        options_.loop->runAfter(std::chrono::duration<double>(delay).count(),
                                [this] { connect(); });
    });
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
