#include <drogon/drogon_test.h>
#include <grpcpp/grpcpp.h>
#include <atomic>
#include <condition_variable>
#include <thread>
#include <trantor/net/EventLoopThread.h>

#include "ai/ai_gateway.h"

using namespace std::chrono_literals;

namespace
{
// A stand-in AI service: records events, acknowledges them when told to, and
// can push actions to the connected client.
class FakeService final : public chat::ai::AiGateway::Service
{
  public:
    grpc::Status Connect(
        grpc::ServerContext *context,
        grpc::ServerReaderWriter<chat::ai::AiAction, chat::ai::GatewayEvent> *stream) override
    {
        {
            std::lock_guard lock(mutex);
            token = context->client_metadata().find("authorization")->second.data();
            writer = stream;
        }
        chat::ai::GatewayEvent event;
        while (stream->Read(&event))
        {
            std::lock_guard lock(mutex);
            events.push_back(event.event_id());
            if (ack)
            {
                chat::ai::AiAction action;
                action.mutable_ack()->set_event_id(event.event_id());
                stream->Write(action);
            }
            changed.notify_all();
        }
        std::lock_guard lock(mutex);
        writer = nullptr;
        return grpc::Status::OK;
    }

    bool waitForEvents(size_t count)
    {
        std::unique_lock lock(mutex);
        return changed.wait_for(lock, 10s, [&] { return events.size() >= count; });
    }

    void push(const chat::ai::AiAction &action)
    {
        for (int i = 0; i < 200; ++i)
        {
            {
                std::lock_guard lock(mutex);
                if (writer)
                {
                    writer->Write(action);
                    return;
                }
            }
            std::this_thread::sleep_for(10ms);
        }
    }

    std::mutex mutex;
    std::condition_variable changed;
    std::vector<int64_t> events;
    std::string token;
    bool ack{true};
    grpc::ServerReaderWriter<chat::ai::AiAction, chat::ai::GatewayEvent> *writer{nullptr};
};

std::unique_ptr<grpc::Server> serve(FakeService &service, int &port)
{
    grpc::ServerBuilder builder;
    builder.AddListeningPort("127.0.0.1:" + std::to_string(port),
                             grpc::InsecureServerCredentials(),
                             &port);
    builder.RegisterService(&service);
    return builder.BuildAndStart();
}

chat::ai::GatewayEvent deleted(int64_t roomId)
{
    chat::ai::GatewayEvent event;
    event.mutable_room_deleted()->set_room_id(roomId);
    return event;
}

template <typename Predicate>
bool eventually(Predicate predicate)
{
    for (int i = 0; i < 500; ++i)
    {
        if (predicate())
        {
            return true;
        }
        std::this_thread::sleep_for(10ms);
    }
    return false;
}
}  // namespace

DROGON_TEST(GatewayDeliversInOrderAndForgetsAcknowledgedEvents)
{
    FakeService service;
    int port = 0;
    auto server = serve(service, port);
    trantor::EventLoopThread loopThread;
    loopThread.run();

    chat::AiGateway gateway({"127.0.0.1:" + std::to_string(port), "secret", loopThread.getLoop(),
                             10000, 50ms, 200ms},
                            [](chat::ai::AiAction) {});
    gateway.start();
    for (int i = 1; i <= 50; ++i)
    {
        gateway.send(deleted(i));
    }
    REQUIRE(service.waitForEvents(50));
    {
        std::lock_guard lock(service.mutex);
        for (size_t i = 0; i < service.events.size(); ++i)
        {
            CHECK(service.events[i] == static_cast<int64_t>(i + 1));
        }
        CHECK(service.token == "Bearer secret");
    }
    CHECK(eventually([&] { return gateway.unackedCount() == 0; }));
    gateway.stop();
    server->Shutdown();
}

DROGON_TEST(GatewayResendsUnacknowledgedEventsAfterReconnecting)
{
    FakeService first;
    first.ack = false;
    int port = 0;
    auto server = serve(first, port);
    trantor::EventLoopThread loopThread;
    loopThread.run();

    chat::AiGateway gateway({"127.0.0.1:" + std::to_string(port), "t", loopThread.getLoop(),
                             10000, 50ms, 200ms},
                            [](chat::ai::AiAction) {});
    gateway.start();
    gateway.send(deleted(1));
    gateway.send(deleted(2));
    REQUIRE(first.waitForEvents(2));
    CHECK(gateway.unackedCount() == 2);

    // The AI service goes away; an event sent meanwhile waits.
    server->Shutdown(std::chrono::system_clock::now());
    server.reset();
    gateway.send(deleted(3));

    FakeService second;
    server = serve(second, port);
    REQUIRE(second.waitForEvents(3));
    {
        std::lock_guard lock(second.mutex);
        CHECK(std::vector<int64_t>(second.events.begin(), second.events.begin() + 3) ==
              std::vector<int64_t>({1, 2, 3}));
    }
    CHECK(eventually([&] { return gateway.unackedCount() == 0; }));
    gateway.stop();
    server->Shutdown();
}

DROGON_TEST(GatewayHandsActionsToTheLoop)
{
    FakeService service;
    int port = 0;
    auto server = serve(service, port);
    trantor::EventLoopThread loopThread;
    loopThread.run();
    auto *loop = loopThread.getLoop();

    std::atomic<int64_t> roomId{0};
    std::atomic<bool> onLoop{false};
    chat::AiGateway gateway({"127.0.0.1:" + std::to_string(port), "t", loop, 10000, 50ms, 200ms},
                            [&](chat::ai::AiAction action) {
                                onLoop = loop->isInLoopThread();
                                roomId = action.chunk().room_id();
                            });
    gateway.start();
    gateway.send(deleted(1));
    REQUIRE(service.waitForEvents(1));
    chat::ai::AiAction action;
    action.mutable_chunk()->set_room_id(42);
    service.push(action);
    CHECK(eventually([&] { return roomId == 42; }));
    CHECK(onLoop);
    gateway.stop();
    server->Shutdown();
}

DROGON_TEST(GatewayRetriesWithoutDisconnectingWhenAckIsMissing)
{
    FakeService service;
    service.ack = false;
    int port = 0;
    auto server = serve(service, port);
    trantor::EventLoopThread loopThread;
    loopThread.run();
    chat::AiGateway gateway({"127.0.0.1:" + std::to_string(port), "secret", loopThread.getLoop(),
                             10000, 50ms, 200ms, 100ms}, [](chat::ai::AiAction) {});
    gateway.start();
    gateway.send(deleted(99));
    REQUIRE(service.waitForEvents(3));
    {
        std::lock_guard lock(service.mutex);
        CHECK(service.events[0] == service.events[1]);
        CHECK(service.events[1] == service.events[2]);
        service.ack = true;
    }
    CHECK(eventually([&] { return gateway.unackedCount() == 0; }));
    gateway.stop();
    server->Shutdown();
}
