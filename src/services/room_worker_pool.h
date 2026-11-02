#pragma once

#include <cstdint>
#include <deque>
#include <drogon/utils/coroutine.h>
#include <functional>
#include <memory>
#include <mutex>
#include <vector>

namespace chat
{
// Routes jobs to a fixed worker by room. A worker runs one job at a time and
// awaits it before the next, so it holds at most one database connection and a
// busy room cannot drain the pool. Jobs are coroutines; no thread is dedicated
// to a worker.
class RoomWorkerPool
{
  public:
    using Job = std::function<drogon::Task<>()>;

    RoomWorkerPool(size_t workers, size_t queueLimit);

    // Returns false, without queueing, when the worker's queue is full.
    bool submit(int64_t roomId, Job job);

    // Queues regardless of the limit, for work that must not be dropped.
    void submitUnbounded(int64_t roomId, Job job);

  private:
    struct Worker
    {
        std::mutex mutex;
        std::deque<Job> queue;
        bool running{false};
    };

    Worker &workerFor(int64_t roomId);
    void enqueue(Worker &worker, Job job);
    drogon::AsyncTask drain(Worker &worker);

    std::vector<std::unique_ptr<Worker>> workers_;
    size_t queueLimit_;
};

// The pool every room's messages go through.
RoomWorkerPool &roomWorkers();
}  // namespace chat
