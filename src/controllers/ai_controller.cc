#include "controllers/ai_controller.h"

#include "ai/ai_events.h"
#include "ai/ai_gateway.h"
#include "common/util.h"
#include "buffering/request_buffer.h"
#include "buffering/reconcile_result.h"
#include "buffering/external_request.h"
#include "services/ai_service.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
drogon::Task<> publish(int64_t roomId, Json::Value event, RequestContextPtr context)
{
    event["room_id"] = Json::Int64(roomId);
    co_await RoomHub::instance().publish(roomId, toJsonString(event), context);
}

// Clients refresh their member list; the AI service updates its snapshot.
drogon::Task<> membersChanged(int64_t roomId, RequestContextPtr context)
{
    Json::Value event;
    event["type"] = "members_changed";
    co_await publish(roomId, std::move(event), context);
    co_await reportMembersChanged(roomId, context);
}

drogon::Task<> requestDone(int64_t roomId,
                           const std::string &slug,
                           int64_t requester,
                           bool approved, RequestContextPtr context)
{
    Json::Value event;
    event["type"] = "ai_request_done";
    event["slug"] = slug;
    event["approved"] = approved;
    event["only_member_ids"].append(Json::Int64(requester));
    co_await publish(roomId, std::move(event), context);
}
}  // namespace

AiController::Response AiController::models(drogon::HttpRequestPtr req)
{
    if(auto queued=co_await RequestBuffer::instance().admit(req,"AiController::models")) co_return queued;
    co_return jsonResponse(co_await AiService::models(co_await SessionStore::require(req)));
}
AiController::Response AiController::createRole(drogon::HttpRequestPtr req)
{
    if(auto queued=co_await RequestBuffer::instance().admit(req,"AiController::createRole")) co_return queued;
    const auto session=co_await SessionStore::require(req);const auto input=jsonBody(req);
    co_return jsonResponse(co_await AiService::createRole(session,stringField(input,"model_slug"),stringField(input,"name")),drogon::k201Created);
}
AiController::Response AiController::editPersona(drogon::HttpRequestPtr req,std::string roomId,std::string memberId)
{
    std::vector<std::string> args{roomId,memberId};
    if(auto queued=co_await RequestBuffer::instance().admit(req,"AiController::editPersona",std::move(args))) co_return queued;
    const auto session=co_await SessionStore::require(req);const auto input=jsonBody(req);
    if(!input["persona_version"].isInt64()) throw ApiError::invalidArgument("persona_version required");
    co_return jsonResponse(co_await AiService::editPersona(session,parseRoomId(roomId),parseRoomId(memberId),stringField(input,"persona"),input["persona_version"].asInt64()));
}

AiController::Response AiController::catalog(drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::catalog")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::catalog(session));
}

AiController::Response AiController::request(drogon::HttpRequestPtr req, std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::request", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto input=jsonBody(req);
    if(input.isMember("member_id") && (!input["member_id"].isInt64() || input["member_id"].asInt64()<=0))
        throw ApiError::invalidArgument("positive member_id required");
    const auto slug=input.isMember("member_id") ? std::to_string(input["member_id"].asInt64()) : stringField(input,"slug");
    const auto persona=input.isMember("persona")?stringField(input,"persona"):"";
    const auto outcome = co_await AiService::request(session,id,slug,persona);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        Json::Value body;
        body["status"] = outcome.joined ? "joined" : "pending";
        co_return jsonResponse(body);
    }
    if (outcome.joined)
    {
        co_await membersChanged(id, session.request);
    }
    else
    {
        Json::Value event;
        event["type"] = "ai_request";
        event["slug"] = "role_"+std::to_string(outcome.aiMemberId);
        event["member_id"] = Json::Int64(outcome.aiMemberId);
        event["requested_by"] = Json::Int64(session.memberId);
        for (const auto reviewer : outcome.reviewers)
        {
            event["only_member_ids"].append(Json::Int64(reviewer));
        }
        co_await publish(id, std::move(event), session.request);
    }
    Json::Value body;
    body["status"] = outcome.joined ? "joined" : "pending";
    co_return jsonResponse(body);
}

AiController::Response AiController::remove(drogon::HttpRequestPtr req,
                                            std::string roomId,
                                            std::string slug)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(slug);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::remove", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    if (co_await AiService::remove(session, id, slug))
    {
        co_await membersChanged(id, session.request);
    }
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::pending(drogon::HttpRequestPtr req, std::string roomId)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::pending", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::pendingRequests(session, parseRoomId(roomId)));
}

AiController::Response AiController::approve(drogon::HttpRequestPtr req,
                                             std::string roomId,
                                             std::string slug)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(slug);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::approve", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto review = co_await AiService::review(session, id, slug, true);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await membersChanged(id, session.request);
    co_await requestDone(id, slug, review.requestedBy, true, session.request);
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::reject(drogon::HttpRequestPtr req,
                                            std::string roomId,
                                            std::string slug)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(roomId);
    bufferArgs.push_back(slug);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::reject", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    const auto id = parseRoomId(roomId);
    const auto review = co_await AiService::review(session, id, slug, false);
    if (session.request && session.request->replayed)
    {
        co_await reconcileResult(id, session.request);
        co_return jsonResponse(Json::objectValue);
    }
    co_await requestDone(id, slug, review.requestedBy, false, session.request);
    co_return jsonResponse(Json::objectValue);
}

AiController::Response AiController::keys(drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::keys")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_return jsonResponse(co_await AiService::keys(session));
}

AiController::Response AiController::storeKey(drogon::HttpRequestPtr req, std::string provider)
{
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(provider);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::storeKey", std::move(bufferArgs))) co_return queued;
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
    co_await beginExternalRequest(session.request, "user:" + std::to_string(session.userId), "key.store");
    checkRequest(session.request);
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
    std::vector<std::string> bufferArgs;
    bufferArgs.push_back(provider);
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AiController::deleteKey", std::move(bufferArgs))) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await AiService::deleteKey(session, provider);
    co_return jsonResponse(Json::objectValue);
}
}  // namespace chat
