#include "ws/chat_socket.h"

#include <chrono>
#include <drogon/drogon.h>
#include <sstream>

#include "ai/ai_events.h"
#include "common/util.h"
#include "services/message_rate_limiter.h"
#include "services/message_service.h"
#include "services/room_service.h"
#include "services/room_worker_pool.h"
#include "services/session_store.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
struct SocketContext
{
    Session session;
    std::string token;
};

void sendJson(const drogon::WebSocketConnectionPtr &connection,
              const Json::Value &value)
{
    connection->send(toJsonString(value));
}

void sendError(const drogon::WebSocketConnectionPtr &connection,
               const ApiError &error,
               const Json::Value &request)
{
    Json::Value body = errorBody(error);
    body["type"] = "error";
    if (request.isMember("client_msg_id"))
    {
        body["client_msg_id"] = request["client_msg_id"];
    }
    if (request.isMember("room_id"))
    {
        body["room_id"] = request["room_id"];
    }
    sendJson(connection, body);
}

// Keeps any failure from escaping a coroutine nobody awaits.
drogon::Task<> reportingErrors(drogon::WebSocketConnectionPtr connection,
                               Json::Value request,
                               std::function<drogon::Task<>()> body)
{
    try
    {
        co_await body();
    }
    catch (const ApiError &error)
    {
        sendError(connection, error, request);
    }
    catch (const std::exception &e)
    {
        LOG_ERROR << "socket: " << e.what();
        sendError(connection, ApiError::unavailable(), request);
    }
}

void handleSend(const drogon::WebSocketConnectionPtr &connection,
                const std::shared_ptr<SocketContext> &context,
                const Json::Value &request)
{
    const auto roomId = request["room_id"].asInt64();
    auto job = [connection, context, request, roomId]() -> drogon::Task<> {
        co_await reportingErrors(
            connection, request, [=]() -> drogon::Task<> {
                // Checked per message: the session may have ended since the
                // socket opened.
                const auto session = co_await SessionStore::find(context->token);
                if (!session)
                {
                    throw ApiError::unauthenticated();
                }
                // Before saving, so a refused message can be resent later
                // with the same client_msg_id.
                co_await MessageRateLimiter::admit(session->userId);
                const auto message = co_await MessageService::save(
                    *session,
                    roomId,
                    stringField(request, "client_msg_id"),
                    stringField(request, "content"));

                Json::Value ack;
                ack["type"] = "ack";
                ack["room_id"] = Json::Int64(roomId);
                ack["client_msg_id"] = request["client_msg_id"];
                ack["seq"] = Json::Int64(message.seq);
                ack["created_at"] = message.json["created_at"];
                ack["duplicate"] = message.duplicate;
                sendJson(connection, ack);

                if (!message.duplicate)
                {
                    co_await MessageService::distribute(message);
                    co_await reportMessage(message);
                }
            });
    };
    if (!roomWorkers().submit(roomId, std::move(job)))
    {
        sendError(connection, ApiError::busy(), request);
    }
}

void handleSubscribe(const drogon::WebSocketConnectionPtr &connection,
                     const std::shared_ptr<SocketContext> &context,
                     const Json::Value &request)
{
    drogon::async_run([connection, context, request]() -> drogon::Task<> {
        co_await reportingErrors(
            connection, request, [=]() -> drogon::Task<> {
                const auto roomId = request["room_id"].asInt64();
                const auto lastSeq = co_await RoomService::requireMember(
                    context->session.memberId, roomId);
                co_await RoomHub::instance().subscribe(
                    connection, roomId, context->session.memberId);
                Json::Value reply;
                reply["type"] = "subscribed";
                reply["room_id"] = Json::Int64(roomId);
                reply["last_seq"] = Json::Int64(lastSeq);
                sendJson(connection, reply);
            });
    });
}
}  // namespace

void ChatSocket::handleNewConnection(
    const drogon::HttpRequestPtr &req,
    const drogon::WebSocketConnectionPtr &connection)
{
    connection->setContext(std::make_shared<SocketContext>(
        SocketContext{req->attributes()->get<Session>("session"),
                      req->getCookie(kSessionCookie)}));
    connection->setPingMessage("", std::chrono::seconds(30));
}

void ChatSocket::handleNewMessage(const drogon::WebSocketConnectionPtr &connection,
                                  std::string &&message,
                                  const drogon::WebSocketMessageType &type)
{
    if (type != drogon::WebSocketMessageType::Text)
    {
        return;
    }
    Json::CharReaderBuilder builder;
    std::istringstream stream(message);
    Json::Value request;
    std::string errors;
    if (!Json::parseFromStream(builder, stream, &request, &errors) ||
        !request.isObject() || !request["type"].isString() ||
        !request["room_id"].isInt64())
    {
        sendError(connection,
                  ApiError::invalidArgument("expected type and room_id"),
                  Json::objectValue);
        return;
    }
    const auto context = connection->getContext<SocketContext>();
    const auto action = request["type"].asString();
    if (action == "send")
    {
        handleSend(connection, context, request);
    }
    else if (action == "subscribe")
    {
        handleSubscribe(connection, context, request);
    }
    else if (action == "unsubscribe")
    {
        RoomHub::instance().unsubscribe(connection, request["room_id"].asInt64());
    }
    else
    {
        sendError(connection, ApiError::invalidArgument("unknown type"), request);
    }
}

void ChatSocket::handleConnectionClosed(
    const drogon::WebSocketConnectionPtr &connection)
{
    RoomHub::instance().removeConnection(connection);
}
}  // namespace chat
