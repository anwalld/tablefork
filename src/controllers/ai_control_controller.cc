#include "controllers/ai_control_controller.h"
#include <drogon/drogon.h>
#include <optional>
#include "ai/ai_events.h"
#include "buffering/request_buffer.h"
#include "buffering/request_memo.h"
#include "common/util.h"
#include "services/room_service.h"
#include "services/message_service.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
// All paths use room -> membership -> generation lock order, matching storage.
drogon::Task<std::string> lockActor(const GuardedTransactionPtr &tx, int64_t room, int64_t member)
{
    const auto rooms = co_await tx->execSqlCoro("SELECT id FROM rooms WHERE id = ? FOR UPDATE", room);
    if (rooms.empty()) throw ApiError::roomNotFound();
    const auto rows = co_await tx->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?", room, member);
    if (rows.empty()) throw ApiError::roomNotFound();
    co_return rows[0]["role"].as<std::string>();
}

// Retry-safe materialization of the snapshot whose stop has already committed.
drogon::Task<> deliverStopped(int64_t room, RequestContextPtr context)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT generation_id,member_id,content,HEX(admission_id) AS admission FROM ai_generations "
        "WHERE room_id=? AND phase='ready' AND outcome='stopped'", room);
    for (const auto &row : rows)
    {
        checkRequest(context);
        const auto generation = row["generation_id"].as<std::string>();
        const auto content = row["content"].as<std::string>();
        // Python skips whitespace-only snapshots; store nothing for them either.
        bool discard = content.find_first_not_of(" \t\r\n") == std::string::npos;
        if (!discard)
        {
            std::optional<StoredMessage> message;
            try
            {
                message = co_await MessageService::saveAiMessage(row["member_id"].as<int64_t>(), room, generation, content,
                    row["admission"].isNull() ? "" : row["admission"].as<std::string>());
            }
            catch (const ApiError &e)
            {
                // The AI left or was re-admitted, so this snapshot can never be
                // stored. Retire it rather than make every later stop or pause
                // in the room fail on it.
                if (e.code() != "room_not_found" && e.code() != "invalid_argument") throw;
                discard = true;
            }
            if (message)
            {
                if (!message->duplicate) co_await MessageService::distribute(*message, context);
                co_await reportMessage(*message, context);
            }
        }
        if (discard)
        {
            auto tx = co_await newGuardedTransaction(context);
            co_await tx->execSqlCoro("UPDATE ai_generations SET phase='finished' WHERE generation_id=? AND phase='ready' AND outcome='stopped'", generation);
            co_await commit(std::move(tx));
        }
        Json::Value event;
        event["type"] = "ai_failed";
        event["room_id"] = Json::Int64(room);
        event["generation_id"] = generation;
        co_await RoomHub::instance().publish(room, toJsonString(event), context);
    }
}
}

AiControlController::Response AiControlController::state(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::state", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    co_await RoomService::requireMember(session.memberId, id);
    const auto control = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT paused FROM room_ai_controls WHERE room_id = ?", id);
    const auto active = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT generation_id, member_id, payer_member_id FROM ai_generations "
        "WHERE room_id=? AND (phase='generating' OR (phase='ready' AND outcome IN ('completed','stopped')))", id);
    Json::Value body;
    body["paused"] = !control.empty() && control[0]["paused"].as<bool>();
    body["generations"] = Json::arrayValue;
    for (const auto &row : active)
    {
        Json::Value item;
        item["id"] = row["generation_id"].as<std::string>();
        item["member_id"] = Json::Int64(row["member_id"].as<int64_t>());
        item["payer_member_id"] = Json::Int64(row["payer_member_id"].as<int64_t>());
        body["generations"].append(std::move(item));
    }
    co_return jsonResponse(body);
}

AiControlController::Response AiControlController::pause(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::pause", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto &body = jsonBody(req);
    if (!body["paused"].isBool()) throw ApiError::invalidArgument("paused required");
    const auto paused = body["paused"].asBool();
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    const auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.pause");
    const auto role = co_await lockActor(tx, id, session.memberId);
    if (role != "owner" && role != "admin") throw ApiError::forbidden();
    if (!prior)
    {
        co_await tx->execSqlCoro("INSERT IGNORE INTO room_ai_controls (room_id) VALUES (?)", id);
        co_await tx->execSqlCoro(
            "UPDATE room_ai_controls c JOIN rooms r ON r.id = c.room_id "
            "SET c.ignore_through_seq=r.last_seq,c.paused=?,c.revision=c.revision+1 "
            "WHERE c.room_id = ? AND c.paused <> ?", paused, id, paused);
        if (paused)
            co_await tx->execSqlCoro(
                "UPDATE ai_generations SET phase='ready',outcome='stopped' "
                "WHERE room_id = ? AND (phase='generating' OR (phase='ready' AND outcome IN ('completed','stopped')))", id);
        co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    }
    co_await commit(std::move(tx));
    co_await reportControlChanged(id, session.request);
    co_await deliverStopped(id, session.request);
    Json::Value event;
    event["type"] = "ai_state_changed";
    event["room_id"] = Json::Int64(id);
    co_await RoomHub::instance().publish(id, toJsonString(event), session.request);
    co_return jsonResponse(Json::Value(Json::objectValue));
}

AiControlController::Response AiControlController::stop(drogon::HttpRequestPtr req, std::string room, std::string generation)
{
    std::vector<std::string> args{room, generation};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::stop", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    auto tx = co_await newGuardedTransaction(session.request);
    const auto role = co_await lockActor(tx, id, session.memberId);
    const auto rows = co_await tx->execSqlCoro(
        "SELECT payer_member_id FROM ai_generations WHERE room_id = ? AND generation_id = ? FOR UPDATE", id, generation);
    if (rows.empty()) throw ApiError::invalidArgument("generation not found");
    if (role != "owner" && role != "admin" && rows[0]["payer_member_id"].as<int64_t>() != session.memberId)
        throw ApiError::forbidden();
    co_await tx->execSqlCoro(
        "UPDATE ai_generations SET phase='ready',outcome='stopped' "
        "WHERE room_id = ? AND generation_id = ? AND (phase='generating' OR (phase='ready' AND outcome IN ('completed','stopped')))", id, generation);
    co_await tx->execSqlCoro("INSERT IGNORE INTO room_ai_controls (room_id) VALUES (?)", id);
    co_await tx->execSqlCoro("UPDATE room_ai_controls SET revision=revision+1 WHERE room_id=?", id);
    co_await commit(std::move(tx));
    co_await reportControlChanged(id, session.request);
    co_await deliverStopped(id, session.request);
    co_return jsonResponse(Json::Value(Json::objectValue));
}
}
