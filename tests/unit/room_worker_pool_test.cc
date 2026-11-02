#include <atomic>
#include <chrono>
#include <drogon/drogon_test.h>
#include <future>
#include <mutex>
#include <trantor/net/EventLoopThread.h>
#include <vector>

#include "services/room_worker_pool.h"

using chat::RoomWorkerPool;

namespace
{
trantor::EventLoop *testLoop()
{
    static auto *thread = [] {
        auto *t = new trantor::EventLoopThread("TestLoop");
        t->run();
        return t;
    }();
    return thread->getLoop();
}

// A job that yields to the event loop, so that jobs really overlap when the
// pool fails to serialize them.
RoomWorkerPool::Job sleepingJob(std::atomic<int> &running,
                                std::atomic<int> &peak,
                                std::atomic<int> &done)
{
    return [&]() -> drogon::Task<> {
        const auto now = ++running;
        auto seen = peak.load();
        while (now > seen && !peak.compare_exchange_weak(seen, now))
        {
        }
        co_await drogon::sleepCoro(testLoop(), 0.005);
        --running;
        ++done;
    };
}

void waitFor(const std::atomic<int> &counter, int expected)
{
    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (counter.load() < expected &&
           std::chrono::steady_clock::now() < deadline)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}
}  // namespace

DROGON_TEST(SameRoomRunsSeriallyInOrder)
{
    RoomWorkerPool pool(4, 100);
    std::mutex mutex;
    std::vector<int> order;
    std::atomic<int> done{0};
    for (int i = 0; i < 20; ++i)
    {
        pool.submit(7, [&, i]() -> drogon::Task<> {
            co_await drogon::sleepCoro(testLoop(), 0.001);
            std::lock_guard lock(mutex);
            order.push_back(i);
            ++done;
        });
    }
    waitFor(done, 20);
    REQUIRE(order.size() == 20U);
    for (int i = 0; i < 20; ++i)
    {
        CHECK(order[i] == i);
    }
}

DROGON_TEST(WorkerHoldsOneJobAtATime)
{
    RoomWorkerPool pool(1, 100);
    std::atomic<int> running{0}, peak{0}, done{0};
    for (int room = 0; room < 20; ++room)
    {
        pool.submit(room, sleepingJob(running, peak, done));
    }
    waitFor(done, 20);
    CHECK(done == 20);
    CHECK(peak == 1);
}

DROGON_TEST(DifferentWorkersRunConcurrently)
{
    RoomWorkerPool pool(2, 100);
    std::atomic<int> running{0}, peak{0}, done{0};
    pool.submit(0, sleepingJob(running, peak, done));
    pool.submit(1, sleepingJob(running, peak, done));
    waitFor(done, 2);
    CHECK(peak == 2);
}

DROGON_TEST(FullQueueRejects)
{
    RoomWorkerPool pool(1, 2);
    std::promise<void> release;
    auto released = release.get_future().share();
    std::atomic<int> done{0};
    auto blocking = [&, released]() -> drogon::Task<> {
        // Keeps the worker busy until the test has filled the queue.
        while (released.wait_for(std::chrono::seconds(0)) !=
               std::future_status::ready)
        {
            co_await drogon::sleepCoro(testLoop(), 0.001);
        }
        ++done;
    };
    CHECK(pool.submit(1, blocking));  // running
    CHECK(pool.submit(1, blocking));  // queued
    CHECK(pool.submit(1, blocking));  // queued
    CHECK(pool.submit(1, blocking) == false);
    release.set_value();
    waitFor(done, 3);
    CHECK(done == 3);
    CHECK(pool.submit(1, blocking));
    waitFor(done, 4);
}

DROGON_TEST(UnboundedJobIgnoresTheLimitAndKeepsItsPlace)
{
    RoomWorkerPool pool(1, 1);
    std::mutex mutex;
    std::vector<int> order;
    std::atomic<int> done{0};
    auto record = [&](int id) -> RoomWorkerPool::Job {
        return [&, id]() -> drogon::Task<> {
            co_await drogon::sleepCoro(testLoop(), 0.002);
            std::lock_guard lock(mutex);
            order.push_back(id);
            ++done;
        };
    };
    CHECK(pool.submit(1, record(0)));  // running
    CHECK(pool.submit(1, record(1)));  // fills the queue
    CHECK(pool.submit(1, record(2)) == false);
    pool.submitUnbounded(1, record(3));
    waitFor(done, 3);
    REQUIRE(order.size() == 3U);
    CHECK(order[0] == 0);
    CHECK(order[1] == 1);
    CHECK(order[2] == 3);
}

DROGON_TEST(FailingJobDoesNotStopTheWorker)
{
    RoomWorkerPool pool(1, 10);
    std::atomic<int> done{0};
    pool.submit(1, []() -> drogon::Task<> {
        co_await drogon::sleepCoro(testLoop(), 0.001);
        throw std::runtime_error("boom");
    });
    pool.submit(1, [&]() -> drogon::Task<> {
        ++done;
        co_return;
    });
    waitFor(done, 1);
    CHECK(done == 1);
}
