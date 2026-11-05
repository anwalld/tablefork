#pragma once
#include <drogon/drogon.h>
#include "buffering/request_context.h"
namespace chat {
class GuardedRedis
{
 public:
  explicit GuardedRedis(RequestContextPtr context)
      : client_(drogon::app().getRedisClient()),context_(std::move(context)) {}
  GuardedRedis *operator->() { return this; }
  template<typename... Args>
  auto execCommandCoro(const std::string &command,Args &&...args)
  {
      checkRequest(context_);
      return client_->execCommandCoro(command,std::forward<Args>(args)...);
  }
 private:
  drogon::nosql::RedisClientPtr client_;
  RequestContextPtr context_;
};
inline GuardedRedis guardedRedis(RequestContextPtr context) { return GuardedRedis(std::move(context)); }
}
