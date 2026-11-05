#include "controllers/room_controller.h"

#include <algorithm>

#include "ai/ai_events.h"
#include "common/util.h"
#include "buffering/request_buffer.h"
#include "buffering/reconcile_result.h"
#include "services/message_service.h"
#include "services/room_service.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
int64_t pageLimit(const drogon::HttpRequestPtr &req)
{
    const auto limit = intParameter(req, "limit").value_or(50);
    if (limit < 1 || limit > 100)
    {
        throw ApiError::invalidArgument("limit must be 1 to 100");
    }
    return limit;
}

drogon::Task<> announceRole(int64_t roomId, int64_t memberId, const char *role, RequestContextPtr context)
{
    Json::Value event;
    event["type"] = "role_changed";
    event["room_id"] = Json::Int64(roomId);
    event["member_id"] = Json::Int64(memberId);
    event["role"] = role;
    co_await RoomHub::instance().publish(roomId, toJsonString(event), context);
}
}  // namespace

RoomController::Response RoomController::create(drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::create")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(
        co_await RoomService::create(session, stringField(jsonBody(req), "name")),
        drogon::k201Created);
}

RoomController::Response RoomController::list(drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::list")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await RoomService::list(
        session, pageLimit(req), intParameter(req, "offset").value_or(0)));
}

RoomController::Response RoomController::invite(drogon::HttpRequestPtr req,
                                                std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::invite", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await RoomService::invite(session,
                                 parseRoomId(roomId),
                                 stringField(jsonBody(req), "username"));
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::join(drogon::HttpRequestPtr req,
                                              std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::join", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    co_await RoomService::join(session, id);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await reportMembersChanged(id, session.request);
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::leave(drogon::HttpRequestPtr req,
                                               std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::leave", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto &json = req->getJsonObject();
    const auto confirmDelete = json && (*json)["confirm_delete"].asBool();
    const auto deleted = co_await RoomService::leave(session, id, confirmDelete);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        Json::Value body;
        body["room_deleted"] = deleted;
        co_return jsonResponse(body);
    }
    if (deleted)
    {
        // Distribution holds a shared SQL room lock through its cache write.
        // Deletion has now committed, so no backend can repopulate this key.
        // Wait for cleanup instead of reporting success before a queued DEL.
        co_await MessageService::dropCache(id, session.request);
        checkRequest(session.request);
        reportRoomDeleted(id);
    }
    else
    {
        co_await reportMembersChanged(id, session.request);
    }
    co_await RoomHub::instance().removeMember(id, session.memberId, "left", session.request);
    Json::Value body;
    body["room_deleted"] = deleted;
    co_return jsonResponse(body);
}

RoomController::Response RoomController::members(drogon::HttpRequestPtr req,
                                                 std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::members", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(
        co_await RoomService::members(session, parseRoomId(roomId)));
}

RoomController::Response RoomController::transferOwner(drogon::HttpRequestPtr req,
                                                       std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::transferOwner", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto owner = co_await RoomService::transferOwner(
        session, id, stringField(jsonBody(req), "username"));
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    if (owner != session.memberId)
    {
        co_await announceRole(id, session.memberId, "member", session.request);
        co_await announceRole(id, owner, "owner", session.request);
    }
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::appointAdmin(drogon::HttpRequestPtr req,
                                                      std::string roomId,
                                                      std::string username)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(username);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::appointAdmin", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::setAdmin(session, id, username, true);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await announceRole(id, member, "admin", session.request);
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::dismissAdmin(drogon::HttpRequestPtr req,
                                                      std::string roomId,
                                                      std::string username)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(username);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::dismissAdmin", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::setAdmin(session, id, username, false);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await announceRole(id, member, "member", session.request);
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::kick(drogon::HttpRequestPtr req, std::string roomId, std::string username)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(username);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::kick", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::kick(session, id, std::move(username));
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await reportMembersChanged(id, session.request);
    co_await RoomHub::instance().removeMember(id, member, "kicked", session.request);
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::ban(drogon::HttpRequestPtr req, std::string roomId, std::string username)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(username);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::ban", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::ban(session, id, std::move(username));
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    if (member != 0)
    {
        co_await reportMembersChanged(id, session.request);
        co_await RoomHub::instance().removeMember(id, member, "banned", session.request);
    }
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::unban(drogon::HttpRequestPtr req, std::string roomId, std::string username)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(username);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::unban", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await RoomService::unban(session, parseRoomId(roomId), std::move(username));
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::bans(drogon::HttpRequestPtr req, std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::bans", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await RoomService::bans(session, parseRoomId(roomId)));
}

RoomController::Response RoomController::messages(drogon::HttpRequestPtr req,
                                                  std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "RoomController::messages", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await MessageService::history(
        session,
        parseRoomId(roomId),
        {intParameter(req, "before"), intParameter(req, "after"), pageLimit(req)}));
}
}  // namespace chat
