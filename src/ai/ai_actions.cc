#include "ai/ai_actions.h"

#include <drogon/drogon.h>

#include "ai/ai_events.h"
#include "buffering/request_buffer.h"
#include "common/util.h"
#include "services/message_service.h"
#include "services/room_worker_pool.h"
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

Json::Value failedEvent(int64_t roomId, const std::string &generationId, int64_t aiMemberId)
{
    Json::Value event;
    event["type"] = "ai_failed";
    event["generation_id"] = generationId;
    event["member_id"] = Json::Int64(aiMemberId);
    return event;
}

Json::Value noticeEvent(int64_t memberId,
                        const std::string &code,
                        const std::string &provider,
                        const std::string &message)
{
    Json::Value event;
    event["type"] = "ai_notice";
    event["code"] = code;
    if (!provider.empty())
    {
        event["provider"] = provider;
    }
    event["message"] = message;
    event["only_member_ids"].append(Json::Int64(memberId));
    return event;
}

// Whether a transient event (chunk or failure) still belongs to the AI's
// current admission. One autocommit read: no row lock or pooled connection is
// held while the event is published, so a slow Redis cannot starve stop,
// pause, message storage or login of MySQL connections. Clients drop events
// whose admission_id no longer matches the member list they hold.
drogon::Task<bool> currentAdmission(int64_t roomId,
                                    int64_t memberId,
                                    const std::string &admission,
                                    const std::string &generation = {})
{
    const auto context = RequestBuffer::instance().backgroundContext();
    checkRequest(context);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT p.is_legacy, HEX(a.admission_id) AS admission, g.outcome "
        "FROM room_members rm JOIN ai_profiles p ON p.member_id=rm.member_id "
        "LEFT JOIN room_ai_personas a ON a.room_id=rm.room_id AND a.member_id=rm.member_id "
        "LEFT JOIN ai_generations g ON g.generation_id=? AND g.room_id=rm.room_id "
        "WHERE rm.room_id=? AND rm.member_id=?", generation, roomId, memberId);
    checkRequest(context);
    if (rows.empty()) co_return false;
    // Drop only chunks of a generation aborted by a stop/pause (stopped) or
    // member removal (cancelled). A completed generation streams its final
    // chunk while phase is still 'generating', and that chunk is processed
    // after the final snapshot flips phase to 'ready', so filtering on
    // phase != 'generating' here would drop the last chunk of a normal reply.
    if (!generation.empty() && !rows[0]["outcome"].isNull())
    {
        const auto outcome = rows[0]["outcome"].as<std::string>();
        if (outcome == "stopped" || outcome == "cancelled") co_return false;
    }
    if (rows[0]["is_legacy"].as<bool>()) co_return true;
    co_return !admission.empty() && !rows[0]["admission"].isNull() &&
              rows[0]["admission"].as<std::string>() == admission;
}

drogon::Task<> onChunk(ai::Chunk chunk)
{
    if (!co_await currentAdmission(chunk.room_id(), chunk.ai_member_id(),
                                   chunk.admission_id(), chunk.generation_id()))
        co_return;
    Json::Value event;
    event["type"] = "ai_chunk";
    event["generation_id"] = chunk.generation_id();
    event["member_id"] = Json::Int64(chunk.ai_member_id());
    event["text"] = chunk.text();
    event["admission_id"] = chunk.admission_id();
    event["attempt"] = chunk.attempt();
    event["sequence"] = chunk.sequence();
    event["cumulative"] = chunk.cumulative();
    if (chunk.cumulative()) event["snapshot"] = chunk.snapshot();
    co_await publish(chunk.room_id(), std::move(event));
}

void onFinal(ai::Final final)
{
    const auto roomId = final.room_id();
    // Through the room's worker, like a human's message, so seqs stay in
    // order with everything else sent to the room.
    roomWorkers().submitUnbounded(roomId, [final = std::move(final)]() -> drogon::Task<> {
        StoredMessage message;
        try
        {
            message = co_await MessageService::saveAiMessage(
                final.ai_member_id(), final.room_id(), final.generation_id(), final.content(),final.admission_id(),final.attempt());
        }
        catch (const ApiError &e)
        {
            if (e.code() != "room_not_found" && e.code() != "invalid_argument")
                co_return;
            reportGenerationRejected(final.room_id(), final.generation_id());
            co_return;
        }
        catch (const std::exception &e)
        {
            // The AI service retains the complete result and will redeliver it.
            LOG_WARN << "AI reply storage deferred: " << e.what();
            co_return;
        }
        if (!message.duplicate) co_await MessageService::distribute(message);
        // A repeated Final may mean its storage confirmation was lost.
        co_await reportMessage(message);
    });
}

drogon::Task<> onFailed(ai::Failed failed)
{
    if (!co_await currentAdmission(failed.room_id(), failed.ai_member_id(), failed.admission_id()))
        co_return;
    co_await publish(failed.room_id(),
                     failedEvent(failed.room_id(), failed.generation_id(), failed.ai_member_id()));
    co_await publish(failed.room_id(),
                     noticeEvent(failed.notify_member_id(), failed.code(), "", failed.message()));
}

drogon::Task<> onNotice(ai::Notice notice)
{
    co_await publish(notice.room_id(),
                     noticeEvent(notice.notify_member_id(), notice.code(), notice.provider(),
                                 notice.message()));
}
}  // namespace

void handleAiAction(ai::AiAction action)
{
    if (action.body_case() == ai::AiAction::kFinal)
    {
        onFinal(action.final());
        return;
    }
    drogon::async_run([action = std::move(action)]() -> drogon::Task<> {
        try
        {
            switch (action.body_case())
            {
                case ai::AiAction::kChunk:
                    co_await onChunk(action.chunk());
                    break;
                case ai::AiAction::kFailed:
                    co_await onFailed(action.failed());
                    break;
                case ai::AiAction::kNotice:
                    co_await onNotice(action.notice());
                    break;
                default:
                    break;
            }
        }
        catch (const std::exception &)
        {
            // Transient events may be dropped during failover. Final delivery
            // has its own durable retry/confirmation path above.
            LOG_WARN << "AI transient action skipped, kind="
                     << static_cast<int>(action.body_case());
        }
    });
}
}  // namespace chat
