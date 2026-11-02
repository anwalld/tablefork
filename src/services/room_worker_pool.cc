#include "services/room_worker_pool.h"

#include <trantor/utils/Logger.h>

#include "common/config.h"

namespace chat
{
RoomWorkerPool::RoomWorkerPool(size_t workers, size_t queueLimit)
    : queueLimit_(queueLimit)
{
    workers_.reserve(workers);
    for (size_t i = 0; i < workers; ++i)
    {
        workers_.push_back(std::make_unique<Worker>());
    }
}

RoomWorkerPool::Worker &RoomWorkerPool::workerFor(int64_t roomId)
{
    return *workers_[static_cast<uint64_t>(roomId) % workers_.size()];
}

bool RoomWorkerPool::submit(int64_t roomId, Job job)
{
    auto &worker = workerFor(roomId);
    {
        std::lock_guard lock(worker.mutex);
        if (worker.queue.size() >= queueLimit_)
        {
            return false;
        }
    }
    enqueue(worker, std::move(job));
    return true;
}

void RoomWorkerPool::submitUnbounded(int64_t roomId, Job job)
{
    enqueue(workerFor(roomId), std::move(job));
}

void RoomWorkerPool::enqueue(Worker &worker, Job job)
{
    {
        std::lock_guard lock(worker.mutex);
        worker.queue.push_back(std::move(job));
        if (worker.running)
        {
            return;
        }
        worker.running = true;
    }
    drain(worker);
}

drogon::AsyncTask RoomWorkerPool::drain(Worker &worker)
{
    while (true)
    {
        Job job;
        {
            std::lock_guard lock(worker.mutex);
            if (worker.queue.empty())
            {
                worker.running = false;
                co_return;
            }
            job = std::move(worker.queue.front());
            worker.queue.pop_front();
        }
        try
        {
            co_await job();
        }
        catch (const std::exception &e)
        {
            LOG_ERROR << "room worker job: " << e.what();
        }
    }
}

RoomWorkerPool &roomWorkers()
{
    static RoomWorkerPool pool(Config::get().workerCount,
                               Config::get().workerQueueLimit);
    return pool;
}
}  // namespace chat
