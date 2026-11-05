#include "buffering/request_buffer.h"
#include <drogon/drogon.h>
#include "ai/ai_events.h"
#include "common/util.h"
#include "services/session_store.h"
#include "services/message_service.h"
#include "services/device_trust.h"
namespace chat {
drogon::Task<Json::Value> RequestBuffer::sendChat(Json::Value value,RequestContextPtr context)
{
    auto session=co_await SessionStore::find(value["token"].asString(),context);
    if(!session) throw ApiError::unauthenticated();
    session->request=context;
    auto message=co_await MessageService::save(*session,value["room_id"].asInt64(),
        value["client_msg_id"].asString(),value["content"].asString());
    Json::Value ack;
    ack["type"]="ack"; ack["room_id"]=value["room_id"]; ack["client_msg_id"]=value["client_msg_id"];
    ack["seq"]=Json::Int64(message.seq); ack["created_at"]=message.json["created_at"]; ack["duplicate"]=message.duplicate;
    if(!message.duplicate)
    {
        // Persistence is the ACK boundary. Slow cache/notification work must
        // not consume the client's five-second initial response budget.
        drogon::async_run([message,session=*session,context]() -> drogon::Task<> {
            try
            {
                co_await MessageService::distribute(message,context);
                checkRequest(context);
                co_await reportMessage(message,context);
                co_await DeviceTrust::countSend(session.device,context);
            }
            catch(const std::exception &) { /* saved message is recovered by seq */ }
        });
    }
    co_return ack;
}
} // namespace chat
