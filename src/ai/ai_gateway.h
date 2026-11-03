#pragma once

#include <chrono>
#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <trantor/net/EventLoop.h>

#include "ai.grpc.pb.h"

namespace chat
{
// The chat server's end of the stream to the AI service.
//
// Events are numbered and kept until the AI service acknowledges them; after
// a reconnect the unacknowledged ones are sent again, in order. Actions from
// the AI service are handed to the handler on `loop`. gRPC calls back on its
// own threads, so everything shared is behind mutex_.
class AiGateway
{
  public:
    using ActionHandler = std::function<void(ai::AiAction)>;

    struct Options
    {
        std::string address;
        std::string token;
        trantor::EventLoop *loop;
        size_t maxUnacked = 10000;
        std::chrono::milliseconds minBackoff{500};
        std::chrono::milliseconds maxBackoff{10000};
    };

    AiGateway(Options options, ActionHandler handler);
    ~AiGateway();

    // Connects, and keeps reconnecting until stop().
    void start();
    void stop();

    // Queues an event, filling in its instance and event ids. Thread-safe and
    // never blocks; while disconnected the event waits for the next stream.
    void send(ai::GatewayEvent event);

    struct StoreKeyResult
    {
        bool reachable;
        ai::StoreKeyReply reply;
    };
    // Must be awaited on a Drogon event loop thread.
    drogon::Task<StoreKeyResult> storeKey(int64_t userId,
                                          std::string provider,
                                          std::string apiKey);

    size_t unackedCount();

  private:
    class Stream;
    friend class Stream;

    void connect();
    void onAction(ai::AiAction action);
    void onStreamDone(Stream *stream, bool wasHealthy);

    Options options_;
    ActionHandler handler_;
    std::string instanceId_;
    std::shared_ptr<grpc::Channel> channel_;
    std::unique_ptr<ai::AiGateway::Stub> stub_;

    std::mutex mutex_;
    int64_t nextEventId_{1};
    std::map<int64_t, ai::GatewayEvent> unacked_;
    std::unique_ptr<Stream> stream_;
    std::chrono::milliseconds backoff_;
    bool stopped_{false};
};

// The process-wide gateway, set up in main. Null when AI_GATEWAY is unset.
AiGateway *aiGateway();
void setAiGateway(std::unique_ptr<AiGateway> gateway);
}  // namespace chat
