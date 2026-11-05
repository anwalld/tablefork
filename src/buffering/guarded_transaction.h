#pragma once
#include <drogon/orm/DbClient.h>
#include <drogon/utils/coroutine.h>
#include "buffering/request_context.h"
#include "common/util.h"

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
    auto execSqlCoro(const std::string &sql, Args &&...args)
    {
        checkRequest(context_);
        return tx_->execSqlCoro(sql, std::forward<Args>(args)...);
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
