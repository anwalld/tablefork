#include "buffering/guarded_transaction.h"
#include <drogon/drogon.h>
namespace chat
{
drogon::Task<GuardedTransactionPtr> newGuardedTransaction(RequestContextPtr context)
{
    checkRequest(context);
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    checkRequest(context);
    co_return std::make_shared<GuardedTransaction>(std::move(tx), std::move(context));
}
drogon::Task<> commit(GuardedTransactionPtr guarded)
{
    auto tx = guarded->take();
    guarded.reset();
    co_await commit(std::move(tx));
}
} // namespace chat
