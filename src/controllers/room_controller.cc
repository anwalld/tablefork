#include "controllers/room_controller.h"

#include <algorithm>

#include "ai/ai_events.h"
#include "common/util.h"
#include "services/message_service.h"
#include "services/room_service.h"
#include "services/room_worker_pool.h"
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

drogon::Task<> announceRole(int64_t roomId, int64_t memberId, const char *role)
{
    Json::Value event;
    event["type"] = "role_changed";
    event["room_id"] = Json::Int64(roomId);
    event["member_id"] = Json::Int64(memberId);
    event["role"] = role;
    co_await RoomHub::instance().publish(roomId, toJsonString(event));
}
}  // namespace

RoomController::Response RoomController::create(drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(
        co_await RoomService::create(session, stringField(jsonBody(req), "name")),
        drogon::k201Created);
}

RoomController::Response RoomController::list(drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await RoomService::list(
        session, pageLimit(req), intParameter(req, "offset").value_or(0)));
}

RoomController::Response RoomController::invite(drogon::HttpRequestPtr req,
                                                std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    co_await RoomService::invite(session,
                                 parseRoomId(roomId),
                                 stringField(jsonBody(req), "username"));
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::join(drogon::HttpRequestPtr req,
                                              std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    co_await RoomService::join(session, id);
    co_await reportMembersChanged(id);
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::leave(drogon::HttpRequestPtr req,
                                               std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto &json = req->getJsonObject();
    const auto confirmDelete = json && (*json)["confirm_delete"].asBool();
    const auto deleted = co_await RoomService::leave(session, id, confirmDelete);
    if (deleted)
    {
        // Through the room's worker, so that it runs after any message still
        // being distributed and that message cannot repopulate the cache.
        roomWorkers().submitUnbounded(id, [id]() -> drogon::Task<> {
            co_await MessageService::dropCache(id);
        });
        reportRoomDeleted(id);
    }
    else
    {
        co_await reportMembersChanged(id);
    }
    co_await RoomHub::instance().removeMember(id, session.memberId);
    Json::Value body;
    body["room_deleted"] = deleted;
    co_return jsonResponse(body);
}

RoomController::Response RoomController::members(drogon::HttpRequestPtr req,
                                                 std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(
        co_await RoomService::members(session, parseRoomId(roomId)));
}

RoomController::Response RoomController::transferOwner(drogon::HttpRequestPtr req,
                                                       std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto owner = co_await RoomService::transferOwner(
        session, id, stringField(jsonBody(req), "username"));
    if (owner != session.memberId)
    {
        co_await announceRole(id, session.memberId, "member");
        co_await announceRole(id, owner, "owner");
    }
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::appointAdmin(drogon::HttpRequestPtr req,
                                                      std::string roomId,
                                                      std::string username)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::setAdmin(session, id, username, true);
    co_await announceRole(id, member, "admin");
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::dismissAdmin(drogon::HttpRequestPtr req,
                                                      std::string roomId,
                                                      std::string username)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto member = co_await RoomService::setAdmin(session, id, username, false);
    co_await announceRole(id, member, "member");
    co_return jsonResponse(Json::objectValue);
}

RoomController::Response RoomController::messages(drogon::HttpRequestPtr req,
                                                  std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await MessageService::history(
        session,
        parseRoomId(roomId),
        {intParameter(req, "before"), intParameter(req, "after"), pageLimit(req)}));
}
}  // namespace chat
