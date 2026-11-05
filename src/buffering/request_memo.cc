#include "buffering/request_memo.h"
#include "common/util.h"
#include <sstream>

namespace chat
{
drogon::Task<std::optional<Json::Value>> RequestMemo::begin(
    const Tx &tx, const RequestContextPtr &context, std::string actor, std::string operation)
{
    if (!context) co_return std::nullopt;
    context->check();
    if (context->requestId.empty() || context->requestId.size() > 128 ||
        context->fingerprint.size() != 64 || actor.empty() || actor.size() > 128 ||
        operation.empty() || operation.size() > 128)
        throw ApiError::invalidArgument("invalid business request identity");
    // Unique insertion waits on an earlier uncommitted operation; a negative
    // snapshot query alone would allow a duplicate business write.
    co_await tx->execSqlCoro(
        "INSERT INTO business_request_results (actor, request_id, operation, fingerprint) "
        "VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE request_id = request_id",
        actor, context->requestId, operation, context->fingerprint);
    context->check();
    const auto rows = co_await tx->execSqlCoro(
        "SELECT operation, fingerprint, result FROM business_request_results "
        "WHERE actor = ? AND request_id = ? FOR UPDATE", actor, context->requestId);
    if (rows.empty()) throw ApiError::unavailable();
    if (rows[0]["operation"].as<std::string>() != operation ||
        rows[0]["fingerprint"].as<std::string>() != context->fingerprint)
        throw ApiError(drogon::k409Conflict,"request_conflict","request ID was already used for different parameters");
    if (rows[0]["result"].isNull()) co_return std::nullopt;
    Json::Value value;
    std::istringstream stream(rows[0]["result"].as<std::string>());
    stream >> value;
    context->replayed = true;
    co_return value;
}

drogon::Task<> RequestMemo::finish(const Tx &tx, const RequestContextPtr &context,
                                  std::string actor, Json::Value result)
{
    if (!context) co_return;
    context->check();
    co_await tx->execSqlCoro(
        "UPDATE business_request_results SET result = ? WHERE actor = ? AND request_id = ?",
        toJsonString(result), actor, context->requestId);
}
} // namespace chat
