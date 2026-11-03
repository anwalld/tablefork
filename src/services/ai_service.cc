#include "services/ai_service.h"

#include <drogon/drogon.h>

#include "common/util.h"

namespace chat
{
namespace
{
using TransactionPtr = std::shared_ptr<drogon::orm::Transaction>;

ApiError aiNotFound()
{
    return {drogon::k404NotFound, "ai_not_found", "no such AI"};
}

struct Profile
{
    int64_t memberId;
    std::string provider;
};

drogon::Task<Profile> profile(const TransactionPtr &tx, const std::string &slug)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT member_id, provider FROM ai_profiles WHERE slug = ?", slug);
    if (rows.empty())
    {
        throw aiNotFound();
    }
    co_return Profile{rows[0]["member_id"].as<int64_t>(),
                      rows[0]["provider"].as<std::string>()};
}

// Locks the room row, like every membership change, and returns the
// caller's role.
drogon::Task<std::string> lockedRole(const TransactionPtr &tx,
                                     int64_t roomId,
                                     int64_t memberId)
{
    const auto room = co_await tx->execSqlCoro(
        "SELECT id FROM rooms WHERE id = ? FOR UPDATE", roomId);
    if (room.empty())
    {
        throw ApiError::roomNotFound();
    }
    const auto rows = co_await tx->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    if (rows.empty())
    {
        throw ApiError::roomNotFound();
    }
    co_return rows[0]["role"].as<std::string>();
}

bool canManage(const std::string &role)
{
    return role == "owner" || role == "admin";
}

drogon::Task<bool> inRoom(const TransactionPtr &tx, int64_t roomId, int64_t memberId)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT 1 FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    co_return !rows.empty();
}
}  // namespace

bool AiService::isProvider(const std::string &provider)
{
    return provider == "deepseek" || provider == "openai" || provider == "anthropic" ||
           provider == "gemini";
}

drogon::Task<Json::Value> AiService::catalog(Session session)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT p.slug, m.display_name, p.provider, k.user_id IS NOT NULL AS has_key "
        "FROM ai_profiles p JOIN members m ON m.id = p.member_id "
        "LEFT JOIN user_api_keys k ON k.user_id = ? AND k.provider = p.provider "
        "ORDER BY m.id",
        session.userId);
    Json::Value agents(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value agent;
        agent["slug"] = row["slug"].as<std::string>();
        agent["name"] = row["display_name"].as<std::string>();
        agent["provider"] = row["provider"].as<std::string>();
        agent["has_key"] = row["has_key"].as<int64_t>() != 0;
        agents.append(std::move(agent));
    }
    Json::Value body;
    body["agents"] = std::move(agents);
    co_return body;
}

drogon::Task<AiService::RequestOutcome> AiService::request(Session session,
                                                           int64_t roomId,
                                                           std::string slug)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    const auto ai = co_await profile(tx, slug);
    const auto key = co_await tx->execSqlCoro(
        "SELECT 1 FROM user_api_keys WHERE user_id = ? AND provider = ?",
        session.userId,
        ai.provider);
    if (key.empty())
    {
        throw ApiError::apiKeyRequired(ai.provider);
    }
    RequestOutcome outcome{true, ai.memberId, {}};
    if (co_await inRoom(tx, roomId, ai.memberId))
    {
        co_await commit(std::move(tx));
        outcome.joined = true;
        co_return outcome;
    }
    if (canManage(role))
    {
        co_await tx->execSqlCoro(
            "INSERT INTO room_members (room_id, member_id) VALUES (?, ?)",
            roomId,
            ai.memberId);
        co_await tx->execSqlCoro(
            "DELETE FROM ai_member_requests WHERE room_id = ? AND member_id = ?",
            roomId,
            ai.memberId);
    }
    else
    {
        outcome.joined = false;
        co_await tx->execSqlCoro(
            "INSERT IGNORE INTO ai_member_requests (room_id, member_id, requested_by) "
            "VALUES (?, ?, ?)",
            roomId,
            ai.memberId,
            session.memberId);
        const auto reviewers = co_await tx->execSqlCoro(
            "SELECT member_id FROM room_members "
            "WHERE room_id = ? AND role IN ('owner', 'admin')",
            roomId);
        for (const auto &row : reviewers)
        {
            outcome.reviewers.push_back(row["member_id"].as<int64_t>());
        }
    }
    co_await commit(std::move(tx));
    co_return outcome;
}

drogon::Task<Json::Value> AiService::pendingRequests(Session session, int64_t roomId)
{
    auto db = drogon::app().getDbClient();
    const auto role = co_await db->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        session.memberId);
    if (role.empty())
    {
        throw ApiError::roomNotFound();
    }
    if (!canManage(role[0]["role"].as<std::string>()))
    {
        throw ApiError::forbidden();
    }
    const auto rows = co_await db->execSqlCoro(
        "SELECT p.slug, ai.display_name AS ai_name, r.requested_by, "
        "       requester.display_name AS requester_name, "
        "       UNIX_TIMESTAMP(r.created_at) * 1000 AS created_at_ms "
        "FROM ai_member_requests r "
        "JOIN ai_profiles p ON p.member_id = r.member_id "
        "JOIN members ai ON ai.id = r.member_id "
        "JOIN members requester ON requester.id = r.requested_by "
        "WHERE r.room_id = ? ORDER BY r.created_at",
        roomId);
    Json::Value requests(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value request;
        request["slug"] = row["slug"].as<std::string>();
        request["name"] = row["ai_name"].as<std::string>();
        request["requested_by"]["id"] = Json::Int64(row["requested_by"].as<int64_t>());
        request["requested_by"]["name"] = row["requester_name"].as<std::string>();
        request["created_at"] = Json::Int64(row["created_at_ms"].as<double>());
        requests.append(std::move(request));
    }
    Json::Value body;
    body["requests"] = std::move(requests);
    co_return body;
}

drogon::Task<AiService::Review> AiService::review(Session session,
                                                  int64_t roomId,
                                                  std::string slug,
                                                  bool approve)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    if (!canManage(role))
    {
        throw ApiError::forbidden();
    }
    const auto ai = co_await profile(tx, slug);
    const auto request = co_await tx->execSqlCoro(
        "SELECT requested_by FROM ai_member_requests WHERE room_id = ? AND member_id = ?",
        roomId,
        ai.memberId);
    if (request.empty())
    {
        throw ApiError(drogon::k404NotFound, "request_not_found", "no such request");
    }
    co_await tx->execSqlCoro(
        "DELETE FROM ai_member_requests WHERE room_id = ? AND member_id = ?",
        roomId,
        ai.memberId);
    if (approve)
    {
        co_await tx->execSqlCoro(
            "INSERT IGNORE INTO room_members (room_id, member_id) VALUES (?, ?)",
            roomId,
            ai.memberId);
    }
    co_await commit(std::move(tx));
    co_return Review{ai.memberId, request[0]["requested_by"].as<int64_t>()};
}

drogon::Task<std::optional<int64_t>> AiService::remove(Session session,
                                                       int64_t roomId,
                                                       std::string slug)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    if (!canManage(role))
    {
        throw ApiError::forbidden();
    }
    const auto ai = co_await profile(tx, slug);
    const auto removed = co_await tx->execSqlCoro(
        "DELETE FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        ai.memberId);
    co_await commit(std::move(tx));
    if (removed.affectedRows() == 0)
    {
        co_return std::nullopt;
    }
    co_return ai.memberId;
}

drogon::Task<Json::Value> AiService::keys(Session session)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT provider, last4, UNIX_TIMESTAMP(updated_at) * 1000 AS updated_at_ms "
        "FROM user_api_keys WHERE user_id = ? ORDER BY provider",
        session.userId);
    Json::Value keys(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value key;
        key["provider"] = row["provider"].as<std::string>();
        key["last4"] = row["last4"].as<std::string>();
        key["updated_at"] = Json::Int64(row["updated_at_ms"].as<double>());
        keys.append(std::move(key));
    }
    Json::Value body;
    body["keys"] = std::move(keys);
    co_return body;
}

drogon::Task<> AiService::deleteKey(Session session, std::string provider)
{
    if (!isProvider(provider))
    {
        throw ApiError::invalidArgument("unknown provider");
    }
    co_await drogon::app().getDbClient()->execSqlCoro(
        "DELETE FROM user_api_keys WHERE user_id = ? AND provider = ?",
        session.userId,
        provider);
}
}  // namespace chat
