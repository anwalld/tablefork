#pragma once
#include <drogon/orm/DbClient.h>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <optional>
#include "buffering/guarded_transaction.h"

namespace chat
{
// Does not retain a transaction reference: commit must still release the last
// reference before waiting for Drogon's commit callback.
class RequestMemo
{
  public:
    using Tx = GuardedTransactionPtr;
    static drogon::Task<std::optional<Json::Value>> begin(
        const Tx &tx, const RequestContextPtr &context,
        std::string actor, std::string operation);
    static drogon::Task<> finish(const Tx &tx, const RequestContextPtr &context,
                                std::string actor, Json::Value result);
};
} // namespace chat
