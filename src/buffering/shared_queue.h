#pragma once

#include <drogon/nosql/RedisClient.h>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include "buffering/payload_crypto.h"

namespace chat
{
class SharedQueue
{
  public:
    struct Options
    {
        int maxWaiting = 1024;
        int maxBodyBytes = 16 * 1024 * 1024;
        int maxMemory = 64 * 1024 * 1024;
        int maxResultBytes = 16 * 1024 * 1024; // encrypted envelope budget
        int waitMs = 45000;
        int resultMs = 300000;
        int claimMs = 10000;
    };
    SharedQueue(drogon::nosql::RedisClientPtr client, PayloadCrypto crypto,
                std::string name, Options options);
    drogon::Task<Json::Value> initialize();
    drogon::Task<Json::Value> status();
    drogon::Task<Json::Value> control(std::string expectedEpoch, std::string epoch,
                                     uint64_t revision, bool paused);
    drogon::Task<Json::Value> admit(std::string capability, std::string requestId,
                                   Json::Value descriptor, size_t bodyBytes, bool recoverable = false, bool forceQueue = false);
    drogon::Task<Json::Value> query(std::string taskId, std::string capability);
    drogon::Task<Json::Value> claim(std::string epoch, std::string worker,
                                   std::string claimToken);
    drogon::Task<Json::Value> finish(std::string id, std::string worker,
                                    std::string claimToken, std::string state,
                                    Json::Value result);
    drogon::Task<Json::Value> renew(std::string id, std::string worker,
                                   std::string claimToken);

  private:
    drogon::Task<Json::Value> call(std::string action, Json::Value args);
    const drogon::nosql::RedisClientPtr client_;
    const PayloadCrypto crypto_;
    const std::string name_;
    const Options options_;
};
}  // namespace chat
