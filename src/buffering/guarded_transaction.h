#pragma once
#include <drogon/orm/DbClient.h>
#include <drogon/utils/coroutine.h>
#include "buffering/request_context.h"
#include "common/util.h"
#include "common/trace.h"

namespace chat
{
// Context travels with the coroutine's transaction, never in thread_local
// storage. Drogon may resume database coroutines on another thread.
class GuardedTransaction
{
  public:
    GuardedTransaction(std::shared_ptr<drogon::orm::Transaction> tx, RequestContextPtr context)
        : tx_(std::move(tx)), context_(std::move(context)) {}
    ~GuardedTransaction() { if (tx_) tx_->rollback(); }
    template <typename... Args>
    drogon::Task<drogon::orm::Result> execSqlCoro(std::string sql, Args... args)
    {
        checkRequest(context_);
        TraceSpan trace(context_ ? context_->requestId : "", "transaction", "mysql");
        auto result = co_await tx_->execSqlCoro(sql, args...);
        trace.finish();
        co_return result;
    }
    std::shared_ptr<drogon::orm::Transaction> take()
    {
        checkRequest(context_);
        return std::move(tx_);
    }
  private:
    std::shared_ptr<drogon::orm::Transaction> tx_;
    RequestContextPtr context_;
};
using GuardedTransactionPtr = std::shared_ptr<GuardedTransaction>;
drogon::Task<GuardedTransactionPtr> newGuardedTransaction(RequestContextPtr context);
drogon::Task<> commit(GuardedTransactionPtr tx);
} // namespace chat
