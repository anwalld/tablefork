#pragma once
#include <drogon/drogon.h>
#include "buffering/request_context.h"
#include "common/trace.h"
namespace chat {
class GuardedRedis
{
 public:
  explicit GuardedRedis(RequestContextPtr context)
      : client_(drogon::app().getRedisClient()),context_(std::move(context)) {}
  GuardedRedis *operator->() { return this; }
  template<typename... Args>
  drogon::Task<drogon::nosql::RedisResult> execCommandCoro(std::string command, Args... args)
  {
      checkRequest(context_);
      TraceSpan trace(context_ ? context_->requestId : "", "business", "redis");
      auto result = co_await client_->execCommandCoro(command, args...);
      trace.finish();
      co_return result;
  }
 private:
  drogon::nosql::RedisClientPtr client_;
  RequestContextPtr context_;
};
inline GuardedRedis guardedRedis(RequestContextPtr context) { return GuardedRedis(std::move(context)); }
}
