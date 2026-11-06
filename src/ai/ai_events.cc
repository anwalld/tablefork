#include "ai/ai_events.h"

#include <drogon/drogon.h>

#include "ai/ai_gateway.h"

namespace chat
{
namespace
{
drogon::Task<> fillSnapshot(int64_t roomId, ai::RoomSnapshot *snapshot)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT m.id,m.type,m.user_id,m.display_name,p.slug,p.provider,p.model_slug,p.is_legacy,u.username,a.persona,a.persona_version,HEX(a.admission_id) AS admission "
        "FROM room_members rm "
        "JOIN members m ON m.id = rm.member_id "
        "LEFT JOIN ai_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=p.creator_user_id "
        "LEFT JOIN room_ai_personas a ON a.room_id=rm.room_id AND a.member_id=m.id "
        "WHERE rm.room_id = ? ORDER BY rm.joined_at, m.id",
        roomId);
    snapshot->set_room_id(roomId);
    for (const auto &row : rows)
    {
        if (row["type"].as<std::string>() == "human")
        {
            auto *human = snapshot->add_humans();
            human->set_member_id(row["id"].as<int64_t>());
            human->set_user_id(row["user_id"].as<int64_t>());
        }
        else if (!row["slug"].isNull())
        {
            auto *ai = snapshot->add_ais();
            ai->set_member_id(row["id"].as<int64_t>());
            ai->set_slug(row["slug"].as<std::string>());
            ai->set_provider(row["provider"].as<std::string>());
            ai->set_name(row["display_name"].as<std::string>());
            ai->set_model_slug(row["model_slug"].as<std::string>());
            ai->set_is_legacy(row["is_legacy"].as<bool>());
            if(!row["username"].isNull()) ai->set_creator_name(row["username"].as<std::string>());
            if(!row["admission"].isNull()) {
                ai->set_admission_id(row["admission"].as<std::string>());
                ai->set_persona(row["persona"].as<std::string>());
                ai->set_persona_version(row["persona_version"].as<uint64_t>());
            }
        }
    }
}
}  // namespace

drogon::Task<> reportMessage(StoredMessage message, RequestContextPtr context)
{
    auto *gateway = aiGateway();
    if (!gateway)
    {
        co_return;
    }
    try
    {
        ai::GatewayEvent event;
        auto *saved = event.mutable_message_saved();
        co_await fillSnapshot(message.roomId, saved->mutable_room());
        const auto &json = message.json;
        const auto memberId = json["member"]["id"].asInt64();
        auto *sender = saved->mutable_sender();
        sender->set_member_id(memberId);
        sender->set_name(json["member"]["name"].asString());
        if (json["member"]["type"].asString() == "ai")
        {
            sender->set_is_ai(true);
            for (const auto &ai : saved->room().ais())
            {
                if (ai.member_id() == memberId)
                {
                    sender->set_slug(ai.slug());
                }
            }
            saved->set_generation_id(json["generation_id"].asString());
        }
        else
        {
            for (const auto &human : saved->room().humans())
            {
                if (human.member_id() == memberId)
                {
                    sender->set_user_id(human.user_id());
                }
            }
        }
        const auto mentions=co_await drogon::app().getDbClient()->execSqlCoro("SELECT mm.member_id FROM message_mentions mm JOIN members m ON m.id=mm.member_id WHERE mm.room_id=? AND mm.seq=? AND m.type='ai' ORDER BY mm.member_id",message.roomId,message.seq);
        for(const auto &mention:mentions) saved->add_mention_ids(mention["member_id"].as<int64_t>());
        saved->set_seq(message.seq);
        saved->set_content(json["content"].asString());
        saved->set_stopped(json["stopped"].asBool());
        saved->set_client_msg_id(json["client_msg_id"].asString());
        saved->set_created_at_ms(json["created_at"].asInt64());
        checkRequest(context);
        gateway->send(std::move(event));
    }
    catch (const std::exception &e)
    {
        LOG_ERROR << "report message to AI service: " << e.what();
    }
}

drogon::Task<> reportMembersChanged(int64_t roomId, RequestContextPtr context)
{
    auto *gateway = aiGateway();
    if (!gateway)
    {
        co_return;
    }
    try
    {
        ai::GatewayEvent event;
        co_await fillSnapshot(roomId, event.mutable_members_changed()->mutable_room());
        checkRequest(context);
        gateway->send(std::move(event));
    }
    catch (const std::exception &e)
    {
        LOG_ERROR << "report members to AI service: " << e.what();
    }
}

void reportRoomDeleted(int64_t roomId)
{
    if (auto *gateway = aiGateway())
    {
        ai::GatewayEvent event;
        event.mutable_room_deleted()->set_room_id(roomId);
        gateway->send(std::move(event));
    }
}
void reportGenerationRejected(int64_t roomId, const std::string &generationId)
{
    if (auto *gateway = aiGateway())
    {
        ai::GatewayEvent event;
        event.mutable_generation_rejected()->set_room_id(roomId);
        event.mutable_generation_rejected()->set_generation_id(generationId);
        gateway->send(std::move(event));
    }
}
}  // namespace chat
