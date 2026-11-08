#include "controllers/ai_control_controller.h"
#include <drogon/drogon.h>
#include <algorithm>
#include <cctype>
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

AiControlController::Response AiControlController::collaboration(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::collaboration", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto &body = jsonBody(req);
    if (!body["enabled"].isBool()) throw ApiError::invalidArgument("enabled required");
    const auto enabled = body["enabled"].asBool();
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    const auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.collaboration");
    const auto role = co_await lockActor(tx, id, session.memberId);
    if (role != "owner" && role != "admin") throw ApiError::forbidden();
    std::vector<int64_t> closedBreakouts;
    if (!prior)
    {
        co_await tx->execSqlCoro("UPDATE rooms SET collaboration_enabled = ? WHERE id = ?", enabled, id);
        if (!enabled)
        {
            // Closing collaboration ends every active breakout below the room,
            // at any depth. Only the ones closed now are reported to the AI.
            const auto tree = co_await tx->execSqlCoro(
                "WITH RECURSIVE tree AS ("
                "  SELECT id FROM rooms WHERE parent_room_id = ?"
                "  UNION ALL SELECT r.id FROM rooms r JOIN tree t ON r.parent_room_id = t.id"
                ") SELECT r.id FROM rooms r JOIN tree t ON t.id = r.id "
                "WHERE r.status <> 'closed' FOR UPDATE", id);
            for (const auto &row : tree)
            {
                const auto child = row["id"].as<int64_t>();
                co_await tx->execSqlCoro("UPDATE rooms SET status = 'closed' WHERE id = ?", child);
                closedBreakouts.push_back(child);
            }
        }
        co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    }
    co_await commit(std::move(tx));
    co_await reportControlChanged(id, session.request);
    // Each breakout closed above wraps up with the conclusion it has so far.
    for (const auto child : closedBreakouts)
        reportBreakoutClosed(child, session.memberId, session.userId);
    Json::Value event;
    event["type"] = "collaboration_changed";
    event["room_id"] = Json::Int64(id);
    event["enabled"] = enabled;
    co_await RoomHub::instance().publish(id, toJsonString(event), session.request);
    co_return jsonResponse(Json::Value(Json::objectValue));
}

AiControlController::Response AiControlController::council(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::council", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto &body = jsonBody(req);
    if (!body["members"].isArray()) throw ApiError::invalidArgument("members required");
    std::vector<int64_t> members;
    for (const auto &item : body["members"])
    {
        if (!item.isInt64()) throw ApiError::invalidArgument("members must be member ids");
        members.push_back(item.asInt64());
    }
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    const auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.council");
    const auto role = co_await lockActor(tx, id, session.memberId);
    if (role != "owner" && role != "admin") throw ApiError::forbidden();
    if (!prior)
    {
        co_await tx->execSqlCoro("DELETE FROM room_council WHERE room_id = ?", id);
        for (const auto memberId : members)
        {
            const auto rows = co_await tx->execSqlCoro(
                "SELECT 1 FROM room_members rm JOIN members m ON m.id = rm.member_id "
                "WHERE rm.room_id = ? AND rm.member_id = ? AND m.type = 'ai'", id, memberId);
            if (rows.empty()) throw ApiError::invalidArgument("council members must be AIs in the room");
            co_await tx->execSqlCoro("INSERT INTO room_council (room_id, member_id) VALUES (?, ?)", id, memberId);
        }
        co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    }
    co_await commit(std::move(tx));
    co_await reportControlChanged(id, session.request);
    co_return jsonResponse(Json::Value(Json::objectValue));
}


namespace
{
int64_t parseMemoryId(const std::string &text)
{
    try
    {
        std::size_t used = 0;
        const auto id = std::stoll(text, &used);
        if (used == text.size() && id > 0) return id;
    }
    catch (const std::exception &) {}
    throw ApiError(drogon::k404NotFound, "not_found", "memory not found");
}

Json::Value memoryRows(const drogon::orm::Result &rows)
{
    Json::Value list(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value item;
        item["id"] = Json::Int64(row["id"].as<int64_t>());
        item["layer"] = row["layer"].as<std::string>();
        item["content"] = row["content"].as<std::string>();
        item["created_at_ms"] = Json::Int64(row["created_at_ms"].as<int64_t>());
        list.append(item);
    }
    Json::Value body;
    body["memories"] = list;
    return body;
}
}  // namespace

AiControlController::Response AiControlController::roomMemories(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::roomMemories", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    co_await RoomService::requireVisible(session.memberId, id);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT id, layer, content, created_at_ms FROM room_memories WHERE room_id = ? "
        "ORDER BY id DESC LIMIT 200", id);
    co_return jsonResponse(memoryRows(rows));
}

AiControlController::Response AiControlController::deleteRoomMemory(drogon::HttpRequestPtr req, std::string room,
                                                                    std::string memory)
{
    std::vector<std::string> args{room, memory};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::deleteRoomMemory", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto memoryId = parseMemoryId(memory);
    auto tx = co_await newGuardedTransaction(session.request);
    const auto role = co_await lockActor(tx, id, session.memberId);
    if (role != "owner" && role != "admin") throw ApiError::forbidden();
    // Deleting twice is fine: the second delete finds nothing.
    co_await tx->execSqlCoro("DELETE FROM room_memories WHERE id = ? AND room_id = ?", memoryId, id);
    co_await commit(std::move(tx));
    co_return jsonResponse(Json::Value(Json::objectValue));
}

AiControlController::Response AiControlController::myMemories(drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::myMemories", {})) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT id, layer, content, created_at_ms FROM user_memories WHERE user_id = ? "
        "ORDER BY id DESC LIMIT 200", session.userId);
    co_return jsonResponse(memoryRows(rows));
}

AiControlController::Response AiControlController::deleteMyMemory(drogon::HttpRequestPtr req, std::string memory)
{
    std::vector<std::string> args{memory};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::deleteMyMemory", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await drogon::app().getDbClient()->execSqlCoro(
        "DELETE FROM user_memories WHERE id = ? AND user_id = ?", parseMemoryId(memory), session.userId);
    co_return jsonResponse(Json::Value(Json::objectValue));
}

namespace
{
constexpr std::size_t kKnowledgeMaxBytes = 5 * 1024 * 1024;

// The document kind from its file name, or empty when unsupported.
std::string knowledgeKind(const std::string &name)
{
    const auto dot = name.rfind('.');
    if (dot == std::string::npos) return {};
    std::string ext = name.substr(dot + 1);
    std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char c) { return std::tolower(c); });
    if (ext == "txt" || ext == "text") return "text";
    if (ext == "md" || ext == "markdown") return "markdown";
    if (ext == "log") return "log";
    return {};
}
}  // namespace

AiControlController::Response AiControlController::importKnowledge(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::importKnowledge", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto &body = jsonBody(req);
    if (!body["name"].isString() || !body["content"].isString())
        throw ApiError::invalidArgument("name and content required");
    const auto name = body["name"].asString();
    const auto content = body["content"].asString();
    if (name.empty() || !utf8Length(name) || *utf8Length(name) > 255)
        throw ApiError::invalidArgument("name must be 1 to 255 characters");
    const auto kind = knowledgeKind(name);
    if (kind.empty())
        throw ApiError(drogon::k415UnsupportedMediaType, "unsupported_file_type",
                       "only .txt, .md and .log files can be imported");
    if (content.size() > kKnowledgeMaxBytes)
        throw ApiError(drogon::k413RequestEntityTooLarge, "file_too_large",
                       "files may be at most 5 MB");
    if (content.empty() || !utf8Length(content))
        throw ApiError::invalidArgument("content must be non-empty UTF-8 text");
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.knowledge.import"))
        co_return jsonResponse(*prior, drogon::k201Created);
    co_await lockActor(tx, id, session.memberId);  // members only, room locked
    const auto now = trantor::Date::now().microSecondsSinceEpoch() / 1000;
    const auto inserted = co_await tx->execSqlCoro(
        "INSERT INTO knowledge_documents (room_id, uploader_member_id, uploader_user_id, name, kind, "
        "size_bytes, content, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        id, session.memberId, session.userId, name, kind, static_cast<uint64_t>(content.size()), content, now);
    Json::Value result;
    result["id"] = Json::Int64(static_cast<int64_t>(inserted.insertId()));
    result["name"] = name;
    result["kind"] = kind;
    result["status"] = "pending";
    co_await RequestMemo::finish(tx, session.request, actor, result);
    co_await commit(std::move(tx));
    co_return jsonResponse(result, drogon::k201Created);
}

AiControlController::Response AiControlController::listKnowledge(drogon::HttpRequestPtr req, std::string room)
{
    std::vector<std::string> args{room};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::listKnowledge", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    co_await RoomService::requireVisible(session.memberId, id);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT d.id, d.name, d.kind, d.size_bytes, d.status, d.status_reason, d.chunk_count, "
        "d.created_at_ms, m.display_name FROM knowledge_documents d JOIN members m ON m.id = d.uploader_member_id "
        "WHERE d.room_id = ? AND d.status <> 'deleted' ORDER BY d.id DESC LIMIT 200", id);
    Json::Value list(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value item;
        item["id"] = Json::Int64(row["id"].as<int64_t>());
        item["name"] = row["name"].as<std::string>();
        item["kind"] = row["kind"].as<std::string>();
        item["size_bytes"] = Json::Int64(row["size_bytes"].as<int64_t>());
        item["status"] = row["status"].as<std::string>();
        item["status_reason"] = row["status_reason"].isNull() ? Json::Value() : Json::Value(row["status_reason"].as<std::string>());
        item["chunk_count"] = Json::Int64(row["chunk_count"].as<int64_t>());
        item["created_at_ms"] = Json::Int64(row["created_at_ms"].as<int64_t>());
        item["uploader"] = row["display_name"].as<std::string>();
        list.append(item);
    }
    Json::Value body;
    body["documents"] = list;
    co_return jsonResponse(body);
}

AiControlController::Response AiControlController::deleteKnowledge(drogon::HttpRequestPtr req, std::string room,
                                                                   std::string document)
{
    std::vector<std::string> args{room, document};
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiControlController::deleteKnowledge", std::move(args))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(room);
    const auto documentId = parseMemoryId(document);
    auto tx = co_await newGuardedTransaction(session.request);
    const auto role = co_await lockActor(tx, id, session.memberId);
    const auto rows = co_await tx->execSqlCoro(
        "SELECT uploader_member_id FROM knowledge_documents WHERE id = ? AND room_id = ? FOR UPDATE",
        documentId, id);
    if (!rows.empty())
    {
        if (role != "owner" && role != "admin" && rows[0]["uploader_member_id"].as<int64_t>() != session.memberId)
            throw ApiError::forbidden();
        // Hidden from search at once; chunks go now, and the AI service drops
        // the vectors and then the row.
        co_await tx->execSqlCoro(
            "UPDATE knowledge_documents SET status = 'deleted', content = '' WHERE id = ?", documentId);
        co_await tx->execSqlCoro("DELETE FROM knowledge_chunks WHERE document_id = ?", documentId);
    }
    co_await commit(std::move(tx));
    co_return jsonResponse(Json::Value(Json::objectValue));
}
}
