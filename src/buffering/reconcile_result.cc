#include "buffering/reconcile_result.h"
#include "ai/ai_events.h"
#include "common/util.h"
#include "ws/room_hub.h"
#include <drogon/drogon.h>
namespace chat {
drogon::Task<> reconcileResult(int64_t roomId, RequestContextPtr context)
{
    RoomHub::instance().markRedisTrouble();
    const auto rows=co_await drogon::app().getDbClient()->execSqlCoro("SELECT 1 FROM rooms WHERE id = ?",roomId);
    checkRequest(context);
    if(rows.empty()) { reportRoomDeleted(roomId); co_return; }
    Json::Value event;
    event["type"]="members_changed"; event["room_id"]=Json::Int64(roomId);
    co_await RoomHub::instance().publish(roomId,toJsonString(event),context);
    co_await reportMembersChanged(roomId,context);
}
}
