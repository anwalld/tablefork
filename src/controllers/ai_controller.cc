#include "controllers/ai_controller.h"

#include "ai/ai_events.h"
#include "ai/ai_gateway.h"
#include "common/util.h"
#include "services/ai_service.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
drogon::Task<> publish(int64_t roomId, Json::Value event)
{
    event["room_id"] = Json::Int64(roomId);
    co_await RoomHub::instance().publish(roomId, toJsonString(event));
}

// Clients refresh their member list; the AI service updates its snapshot.
drogon::Task<> membersChanged(int64_t roomId)
{
    Json::Value event;
    event["type"] = "members_changed";
    co_await publish(roomId, std::move(event));
    co_await reportMembersChanged(roomId);
}

drogon::Task<> requestDone(int64_t roomId,
                           const std::string &slug,
                           int64_t requester,
                           bool approved)
{
    Json::Value event;
    event["type"] = "ai_request_done";
    event["slug"] = slug;
    event["approved"] = approved;
    event["only_member_ids"].append(Json::Int64(requester));
    co_await publish(roomId, std::move(event));
}
}  // namespace

AiController::Response AiController::catalog(drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::catalog(session));
}

AiController::Response AiController::request(drogon::HttpRequestPtr req, std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto slug = stringField(jsonBody(req), "slug");
    const auto outcome = co_await AiService::request(session, id, slug);
    if (outcome.joined)
    {
        co_await membersChanged(id);
    }
    else
    {
        Json::Value event;
        event["type"] = "ai_request";
        event["slug"] = slug;
        event["requested_by"] = Json::Int64(session.memberId);
        for (const auto reviewer : outcome.reviewers)
        {
            event["only_member_ids"].append(Json::Int64(reviewer));
        }
        co_await publish(id, std::move(event));
    }
    Json::Value body;
    body["status"] = outcome.joined ? "joined" : "pending";
    co_return jsonResponse(body);
}

AiController::Response AiController::remove(drogon::HttpRequestPtr req,
                                            std::string roomId,
                                            std::string slug)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    if (co_await AiService::remove(session, id, slug))
    {
        co_await membersChanged(id);
    }
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::pending(drogon::HttpRequestPtr req, std::string roomId)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::pendingRequests(session, parseRoomId(roomId)));
}

AiController::Response AiController::approve(drogon::HttpRequestPtr req,
                                             std::string roomId,
                                             std::string slug)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto review = co_await AiService::review(session, id, slug, true);
    co_await membersChanged(id);
    co_await requestDone(id, slug, review.requestedBy, true);
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::reject(drogon::HttpRequestPtr req,
                                            std::string roomId,
                                            std::string slug)
{
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto review = co_await AiService::review(session, id, slug, false);
    co_await requestDone(id, slug, review.requestedBy, false);
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::keys(drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::keys(session));
}

AiController::Response AiController::storeKey(drogon::HttpRequestPtr req, std::string provider)
{
    const auto session = co_await SessionStore::require(req);
    if (!AiService::isProvider(provider))
    {
        throw ApiError::invalidArgument("unknown provider");
    }
    auto key = stringField(jsonBody(req), "key");
    if (key.empty() || key.size() > 1024 || !isPrintableText(key))
    {
        throw ApiError::invalidArgument("key must be 1 to 1024 printable characters");
    }
    auto *gateway = aiGateway();
    if (!gateway)
    {
        throw ApiError::unavailable();
    }
    const auto result = co_await gateway->storeKey(session.userId, provider, std::move(key));
    if (!result.reachable || result.reply.error_code() == "unavailable")
    {
        throw ApiError::unavailable();
    }
    if (result.reply.error_code() == "api_key_invalid")
    {
        throw ApiError(drogon::k400BadRequest,
                       "api_key_invalid",
                       "the provider rejected this key");
    }
    if (!result.reply.ok())
    {
        throw ApiError::invalidArgument("invalid key");
    }
    Json::Value body;
    body["provider"] = provider;
    body["last4"] = result.reply.last4();
    co_return jsonResponse(body);
}

AiController::Response AiController::deleteKey(drogon::HttpRequestPtr req, std::string provider)
{
    const auto session = co_await SessionStore::require(req);
    co_await AiService::deleteKey(session, provider);
    co_return jsonResponse(Json::objectValue);
}
}  // namespace chat
