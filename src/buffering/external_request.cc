#include "buffering/external_request.h"
#include "common/util.h"
#include <drogon/drogon.h>
namespace chat {
drogon::Task<> beginExternalRequest(RequestContextPtr context,std::string actor,std::string operation)
{
    if(!context) co_return;
    context->check();
    const auto inserted=co_await drogon::app().getDbClient()->execSqlCoro(
        "INSERT IGNORE INTO business_request_results (actor, request_id, operation, fingerprint, kind) VALUES (?, ?, ?, ?, 'external')",
        actor,context->requestId,operation,context->fingerprint);
    context->check();
    if(inserted.affectedRows()) co_return;
    const auto rows=co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT operation, fingerprint FROM business_request_results WHERE actor = ? AND request_id = ?",
        actor,context->requestId);
    if(rows.empty()) throw ApiError::unavailable();
    if(rows[0]["operation"].as<std::string>()!=operation || rows[0]["fingerprint"].as<std::string>()!=context->fingerprint)
        throw ApiError(drogon::k409Conflict,"request_conflict","request ID was reused for different parameters");
    throw ApiError(drogon::k503ServiceUnavailable,"result_unknown","an earlier attempt may already have executed; do not repeat it");
}
}
