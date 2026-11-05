#include "buffering/shared_queue.h"
#include "buffering/queue_script.h"
#include "common/util.h"
#include <sstream>

namespace chat
{
SharedQueue::SharedQueue(drogon::nosql::RedisClientPtr client, PayloadCrypto crypto,
                         std::string name, Options options)
    : client_(std::move(client)), crypto_(std::move(crypto)), name_(std::move(name)), options_(options)
{
    if (!client_ || name_.empty() || options_.maxWaiting <= 0 || options_.maxBodyBytes <= 0 ||
        options_.maxMemory <= 2048 || options_.maxResultBytes <= 0 || options_.waitMs <= 0 ||
        options_.resultMs <= 0 || options_.claimMs <= 0)
        throw std::invalid_argument("invalid shared queue configuration");
}

drogon::Task<Json::Value> SharedQueue::call(std::string action, Json::Value args)
{
    const auto input = toJsonString(args);
    // A timeout here is not proof that a mutating operation did not execute.
    const auto result = co_await client_->execCommandCoro(
        "EVAL %b 1 %s %s %b", kQueueScript, sizeof(kQueueScript) - 1,
        name_.c_str(), action.c_str(), input.data(), input.size());
    Json::Value value;
    std::istringstream stream(result.asString());
    stream >> value;
    co_return value;
}

drogon::Task<Json::Value> SharedQueue::initialize()
{
    Json::Value args, config;
    args["incarnation"] = PayloadCrypto::randomToken();
    config["max_waiting"] = options_.maxWaiting;
    config["max_body_bytes"] = options_.maxBodyBytes;
    config["max_memory"] = options_.maxMemory;
    config["max_result_bytes"] = options_.maxResultBytes;
    config["wait_ms"] = options_.waitMs;
    config["result_ms"] = options_.resultMs;
    config["claim_ms"] = options_.claimMs;
    args["config"] = config;
    co_return co_await call("init", std::move(args));
}

drogon::Task<Json::Value> SharedQueue::status()
{
    co_return co_await call("status", Json::objectValue);
}

drogon::Task<Json::Value> SharedQueue::control(std::string expectedEpoch,
                                              std::string epoch, uint64_t revision, bool paused)
{
    Json::Value args;
    args["expected_epoch"] = std::move(expectedEpoch);
    args["epoch"] = std::move(epoch);
    args["revision"] = Json::UInt64(revision);
    args["mode"] = paused ? "paused" : "draining";
    co_return co_await call("control", std::move(args));
}

drogon::Task<Json::Value> SharedQueue::admit(std::string capability, std::string requestId,
                                            Json::Value descriptor, size_t bodyBytes, bool recoverable, bool forceQueue)
{
    if (capability.size() != 64 || requestId.empty() || requestId.size() > 128)
        throw ApiError::invalidArgument("queue capability or request ID is invalid");
    Json::Value args;
    const auto id = crypto_.digest("task", capability + ":" + requestId);
    const auto payload = toJsonString(descriptor);
    args["id"] = id;
    args["owner"] = crypto_.digest("owner", capability);
    args["binding"] = crypto_.digest("parameters", payload);
    args["payload"] = crypto_.seal(payload, id + ":request");
    args["body_bytes"] = Json::UInt64(bodyBytes);
    args["recoverable"] = recoverable;
    args["force_queue"] = forceQueue;
    co_return co_await call("admit", std::move(args));
}

drogon::Task<Json::Value> SharedQueue::query(std::string taskId, std::string capability)
{
    Json::Value args;
    args["id"] = taskId;
    args["owner"] = crypto_.digest("owner", capability);
    auto result = co_await call("query", std::move(args));
    if (result["result"].isString())
    {
        std::istringstream stream(crypto_.open(result["result"].asString(), taskId + ":result"));
        Json::Value body;
        stream >> body;
        result["result"] = body;
    }
    co_return result;
}

drogon::Task<Json::Value> SharedQueue::claim(std::string epoch, std::string worker,
                                            std::string claimToken)
{
    Json::Value args;
    args["epoch"] = std::move(epoch);
    args["worker"] = std::move(worker);
    args["claim"] = std::move(claimToken);
    auto result = co_await call("claim", std::move(args));
    if (result["payload"].isString())
    {
        std::istringstream stream(crypto_.open(result["payload"].asString(), result["id"].asString() + ":request"));
        Json::Value descriptor;
        stream >> descriptor;
        result["payload"] = descriptor;
    }
    co_return result;
}

drogon::Task<Json::Value> SharedQueue::finish(std::string id, std::string worker,
                                             std::string claimToken, std::string state, Json::Value result)
{
    Json::Value args;
    args["id"] = id;
    args["worker"] = std::move(worker);
    args["claim"] = std::move(claimToken);
    args["state"] = std::move(state);
    args["result"] = crypto_.seal(toJsonString(result), id + ":result");
    co_return co_await call("finish", std::move(args));
}

drogon::Task<Json::Value> SharedQueue::renew(std::string id, std::string worker, std::string claimToken)
{
    Json::Value args;
    args["id"] = std::move(id);
    args["worker"] = std::move(worker);
    args["claim"] = std::move(claimToken);
    co_return co_await call("renew", std::move(args));
}
}  // namespace chat
