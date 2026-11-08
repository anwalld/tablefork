#include "services/ai_service.h"
#include "services/table_templates.h"

#include <drogon/drogon.h>

#include "common/util.h"
#include "buffering/request_memo.h"
#include "buffering/payload_crypto.h"

namespace chat
{
namespace
{
using TransactionPtr = GuardedTransactionPtr;

ApiError aiNotFound()
{
    return {drogon::k404NotFound, "ai_not_found", "no such AI"};
}

struct Profile
{
    int64_t memberId;
    std::string provider;
    int64_t creator;
    bool legacy;
};

drogon::Task<Profile> profile(const TransactionPtr &tx, const std::string &slug)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT member_id, provider, creator_user_id, is_legacy FROM ai_profiles WHERE slug = ? OR CAST(member_id AS CHAR) = ?", slug, slug);
    if (rows.empty())
    {
        throw aiNotFound();
    }
    co_return Profile{rows[0]["member_id"].as<int64_t>(),
                      rows[0]["provider"].as<std::string>(),
                      rows[0]["creator_user_id"].isNull() ? 0 : rows[0]["creator_user_id"].as<int64_t>(),
                      rows[0]["is_legacy"].as<bool>()};
}

// Locks the room row, like every membership change, and returns the
// caller's role.
drogon::Task<std::string> lockedRole(const TransactionPtr &tx,
                                     int64_t roomId,
                                     int64_t memberId)
{
    const auto room = co_await tx->execSqlCoro(
        "SELECT id FROM rooms WHERE id = ? FOR UPDATE", roomId);
    if (room.empty())
    {
        throw ApiError::roomNotFound();
    }
    const auto rows = co_await tx->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    if (rows.empty())
    {
        throw ApiError::roomNotFound();
    }
    co_return rows[0]["role"].as<std::string>();
}

bool canManage(const std::string &role)
{
    return role == "owner" || role == "admin";
}

drogon::Task<bool> inRoom(const TransactionPtr &tx, int64_t roomId, int64_t memberId)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT 1 FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    co_return !rows.empty();
}
}  // namespace

bool AiService::isProvider(const std::string &provider)
{
    return provider == "deepseek" || provider == "openai" || provider == "anthropic" ||
           provider == "gemini";
}

namespace
{
Json::Value memoValue(const AiService::RequestOutcome &outcome)
{
    Json::Value value;
    value["joined"] = outcome.joined;
    value["member"] = Json::Int64(outcome.aiMemberId);
    value["reviewers"] = Json::arrayValue;
    for (auto reviewer : outcome.reviewers) value["reviewers"].append(Json::Int64(reviewer));
    return value;
}
AiService::RequestOutcome requestOutcome(const Json::Value &value)
{
    AiService::RequestOutcome outcome{value["joined"].asBool(), value["member"].asInt64(), {}};
    for (const auto &reviewer : value["reviewers"]) outcome.reviewers.push_back(reviewer.asInt64());
    return outcome;
}
} // namespace

drogon::Task<Json::Value> AiService::models(Session session)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT t.model_slug,t.display_name,t.provider,k.user_id IS NOT NULL AS has_key "
        "FROM ai_model_types t LEFT JOIN user_api_keys k ON k.user_id=? AND k.provider=t.provider ORDER BY t.model_slug", session.userId);
    Json::Value body; body["models"] = Json::arrayValue;
    for (const auto &row : rows) {
        Json::Value item;
        item["model_slug"]=row["model_slug"].as<std::string>();
        item["name"]=row["display_name"].as<std::string>();
        item["provider"]=row["provider"].as<std::string>();
        item["has_key"]=row["has_key"].as<bool>();
        body["models"].append(item);
    }
    co_return body;
}

drogon::Task<Json::Value> AiService::catalog(Session session)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT p.member_id,p.slug,p.model_slug,m.display_name,p.provider,p.creator_user_id,u.username,k.user_id IS NOT NULL AS has_key "
        "FROM ai_profiles p JOIN members m ON m.id=p.member_id JOIN users u ON u.id=p.creator_user_id "
        "LEFT JOIN user_api_keys k ON k.user_id=? AND k.provider=p.provider "
        "WHERE p.creator_user_id=? AND p.is_legacy=FALSE ORDER BY m.id", session.userId, session.userId);
    Json::Value body; body["agents"]=Json::arrayValue;
    for (const auto &row:rows) {
        Json::Value item;
        item["id"]=Json::Int64(row["member_id"].as<int64_t>());
        item["slug"]=row["slug"].as<std::string>();
        item["model_slug"]=row["model_slug"].as<std::string>();
        item["name"]=row["display_name"].as<std::string>();
        item["provider"]=row["provider"].as<std::string>();
        item["creator_user_id"]=Json::Int64(row["creator_user_id"].as<int64_t>());
        item["creator_name"]=row["username"].as<std::string>();
        item["has_key"]=row["has_key"].as<bool>();
        body["agents"].append(item);
    }
    co_return body;
}

drogon::Task<Json::Value> AiService::createRole(Session session, std::string model, std::string name)
{
    const auto length=utf8Length(name);
    if (!length || *length==0 || *length>64 || name.find_first_not_of(" ")==std::string::npos || !isPrintableText(name))
        throw ApiError::invalidArgument("name must be 1 to 64 printable characters");
    auto tx=co_await newGuardedTransaction(session.request);
    const auto actor="user:"+std::to_string(session.userId);
    if (auto prior=co_await RequestMemo::begin(tx,session.request,actor,"ai.createRole")) co_return *prior;
    const auto types=co_await tx->execSqlCoro("SELECT provider FROM ai_model_types WHERE model_slug=?",model);
    if (types.empty()) throw ApiError::invalidArgument("unknown model type");
    const auto inserted=co_await tx->execSqlCoro("INSERT INTO members (type,display_name) VALUES ('ai',?)",name);
    const auto member=static_cast<int64_t>(inserted.insertId());
    const auto slug="role_"+std::to_string(member);
    co_await tx->execSqlCoro("INSERT INTO ai_profiles (member_id,slug,provider,model_slug,creator_user_id,is_legacy) VALUES (?,?,?,?,?,FALSE)",
        member,slug,types[0]["provider"].as<std::string>(),model,session.userId);
    Json::Value result;
    result["id"]=Json::Int64(member); result["slug"]=slug; result["name"]=name;
    result["model_slug"]=model; result["creator_user_id"]=Json::Int64(session.userId);
    result["creator_name"]=session.username;
    co_await RequestMemo::finish(tx,session.request,actor,result);
    co_await commit(std::move(tx)); co_return result;
}

drogon::Task<Json::Value> AiService::editPersona(Session session,int64_t roomId,int64_t memberId,std::string persona,int64_t version)
{
    if (persona.size()>16384 || !utf8Length(persona) || version<1)
        throw ApiError::invalidArgument("invalid persona or version");
    auto tx=co_await newGuardedTransaction(session.request);
    const auto actor="user:"+std::to_string(session.userId);
    if(auto prior=co_await RequestMemo::begin(tx,session.request,actor,"ai.persona")) co_return *prior;
    co_await lockedRole(tx,roomId,session.memberId);
    const auto ai=co_await profile(tx,std::to_string(memberId));
    if(ai.legacy || ai.creator!=session.userId) throw ApiError::forbidden();
    const auto changed=co_await tx->execSqlCoro(
        "UPDATE room_ai_personas SET persona=?,persona_version=persona_version+1 WHERE room_id=? AND member_id=? AND persona_version=?",
        persona,roomId,memberId,version);
    if(changed.affectedRows()==0) throw ApiError(drogon::k409Conflict,"persona_conflict","persona changed or AI left the room");
    Json::Value result;result["persona_version"]=Json::Int64(version+1);
    co_await RequestMemo::finish(tx,session.request,actor,result);co_await commit(std::move(tx));co_return result;
}

drogon::Task<> AiService::removeOwned(const GuardedTransactionPtr &tx,int64_t roomId,int64_t userId)
{
    co_await tx->execSqlCoro("DELETE r FROM ai_member_requests r JOIN ai_profiles p ON p.member_id=r.member_id WHERE r.room_id=? AND p.creator_user_id=?",roomId,userId);
    co_await tx->execSqlCoro("DELETE rm FROM room_members rm JOIN ai_profiles p ON p.member_id=rm.member_id WHERE rm.room_id=? AND p.creator_user_id=?",roomId,userId);
    co_await tx->execSqlCoro("UPDATE ai_generations g JOIN ai_profiles p ON p.member_id=g.member_id SET g.content='',g.last_nonempty_content=NULL,g.outcome='cancelled',g.phase='finished' WHERE g.room_id=? AND p.creator_user_id=? AND g.phase<>'finished'",roomId,userId);
}

drogon::Task<AiService::RequestOutcome> AiService::request(Session session,
                                                           int64_t roomId,
                                                           std::string slug, std::string persona)
{
    if(persona.size()>16384 || !utf8Length(persona)) throw ApiError::invalidArgument("invalid persona");
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.request"))
    {
        co_return requestOutcome(*prior);
    }
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    const auto ai = co_await profile(tx, slug);
    if(ai.legacy || ai.creator!=session.userId) throw aiNotFound();
    const auto key = co_await tx->execSqlCoro(
        "SELECT 1 FROM user_api_keys WHERE user_id = ? AND provider = ?",
        session.userId,
        ai.provider);
    if (key.empty())
    {
        throw ApiError::apiKeyRequired(ai.provider);
    }
    RequestOutcome outcome{true, ai.memberId, {}};
    if (co_await inRoom(tx, roomId, ai.memberId))
    {
        co_await RequestMemo::finish(tx, session.request, actor, memoValue(outcome));
        co_await commit(std::move(tx));
        outcome.joined = true;
        co_return outcome;
    }
    if (canManage(role))
    {
        co_await tx->execSqlCoro(
            "INSERT INTO room_members (room_id, member_id) VALUES (?, ?)",
            roomId,
            ai.memberId);
        co_await tx->execSqlCoro("INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (?,?,UNHEX(?),?)",roomId,ai.memberId,PayloadCrypto::randomToken().substr(0,32),persona);
        co_await tx->execSqlCoro("DELETE FROM ai_member_requests WHERE room_id=? AND member_id=?",roomId,ai.memberId);
    }
    else
    {
        outcome.joined = false;
        const auto pending=co_await tx->execSqlCoro("SELECT persona FROM ai_member_requests WHERE room_id=? AND member_id=?",roomId,ai.memberId);
        if(!pending.empty() && pending[0]["persona"].as<std::string>()!=persona)
            throw ApiError(drogon::k409Conflict,"request_conflict","pending persona differs");
        co_await tx->execSqlCoro("INSERT IGNORE INTO ai_member_requests (room_id,member_id,requested_by,persona) VALUES (?,?,?,?)",roomId,ai.memberId,session.memberId,persona);
        const auto reviewers = co_await tx->execSqlCoro(
            "SELECT member_id FROM room_members "
            "WHERE room_id = ? AND role IN ('owner', 'admin')",
            roomId);
        for (const auto &row : reviewers)
        {
            outcome.reviewers.push_back(row["member_id"].as<int64_t>());
        }
    }
    co_await RequestMemo::finish(tx, session.request, actor, memoValue(outcome));
    co_await commit(std::move(tx));
    co_return outcome;
}

drogon::Task<Json::Value> AiService::pendingRequests(Session session, int64_t roomId)
{
    auto db = drogon::app().getDbClient();
    const auto role = co_await db->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        session.memberId);
    if (role.empty())
    {
        throw ApiError::roomNotFound();
    }
    if (!canManage(role[0]["role"].as<std::string>()))
    {
        throw ApiError::forbidden();
    }
    const auto rows = co_await db->execSqlCoro(
        "SELECT p.member_id,p.model_slug,p.is_legacy,p.slug, ai.display_name AS ai_name, r.requested_by, "
        "       requester.display_name AS requester_name, "
        "       UNIX_TIMESTAMP(r.created_at) * 1000 AS created_at_ms "
        "FROM ai_member_requests r "
        "JOIN ai_profiles p ON p.member_id = r.member_id "
        "JOIN members ai ON ai.id = r.member_id "
        "JOIN members requester ON requester.id = r.requested_by "
        "WHERE r.room_id = ? ORDER BY r.created_at",
        roomId);
    Json::Value requests(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value request;
        request["id"] = Json::Int64(row["member_id"].as<int64_t>());
        request["is_legacy"] = row["is_legacy"].as<bool>();
        request["slug"] = row["slug"].as<std::string>();
        request["name"] = row["ai_name"].as<std::string>();
        request["requested_by"]["id"] = Json::Int64(row["requested_by"].as<int64_t>());
        request["requested_by"]["name"] = row["requester_name"].as<std::string>();
        request["created_at"] = Json::Int64(row["created_at_ms"].as<double>());
        requests.append(std::move(request));
    }
    Json::Value body;
    body["requests"] = std::move(requests);
    co_return body;
}

drogon::Task<AiService::Review> AiService::review(Session session,
                                                  int64_t roomId,
                                                  std::string slug,
                                                  bool approve)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.review"))
    {
        co_return Review{(*prior)["member"].asInt64(), (*prior)["requester"].asInt64()};
    }
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    if (!canManage(role))
    {
        throw ApiError::forbidden();
    }
    const auto ai = co_await profile(tx, slug);
    const auto request = co_await tx->execSqlCoro(
        "SELECT r.requested_by,r.persona,m.user_id FROM ai_member_requests r JOIN members m ON m.id=r.requested_by WHERE r.room_id = ? AND r.member_id = ?",
        roomId,
        ai.memberId);
    if (request.empty())
    {
        throw ApiError(drogon::k404NotFound, "request_not_found", "no such request");
    }
    co_await tx->execSqlCoro(
        "DELETE FROM ai_member_requests WHERE room_id = ? AND member_id = ?",
        roomId,
        ai.memberId);
    if (approve)
    {
        if(ai.legacy) throw ApiError(drogon::k409Conflict,"legacy_ai","legacy AI cannot be invited");
        const auto creatorPresent=co_await inRoom(tx,roomId,request[0]["requested_by"].as<int64_t>());
        if(request[0]["user_id"].as<int64_t>()!=ai.creator || !creatorPresent)
            throw ApiError(drogon::k409Conflict,"request_expired","creator left the room");
        co_await tx->execSqlCoro(
            "INSERT IGNORE INTO room_members (room_id, member_id) VALUES (?, ?)",
            roomId,
            ai.memberId);
        co_await tx->execSqlCoro("INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (?,?,UNHEX(?),?)",roomId,ai.memberId,PayloadCrypto::randomToken().substr(0,32),request[0]["persona"].as<std::string>());
    }
    Json::Value value;
    value["member"] = Json::Int64(ai.memberId);
    value["requester"] = Json::Int64(request[0]["requested_by"].as<int64_t>());
    co_await RequestMemo::finish(tx, session.request, actor, value);
    co_await commit(std::move(tx));
    co_return Review{ai.memberId, request[0]["requested_by"].as<int64_t>()};
}

drogon::Task<std::optional<int64_t>> AiService::remove(Session session,
                                                       int64_t roomId,
                                                       std::string slug)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.remove"))
    {
        co_return prior->isNull() ? std::optional<int64_t>{} : std::optional<int64_t>{prior->asInt64()};
    }
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    if (!canManage(role))
    {
        throw ApiError::forbidden();
    }
    const auto ai = co_await profile(tx, slug);
    const auto present=co_await inRoom(tx,roomId,ai.memberId);
    if(!present && !ai.legacy && ai.creator!=session.userId) throw aiNotFound();
    const auto removed = co_await tx->execSqlCoro(
        "DELETE FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        ai.memberId);
    co_await tx->execSqlCoro("UPDATE ai_generations SET content='',last_nonempty_content=NULL,outcome='cancelled',phase='finished' WHERE room_id=? AND member_id=? AND phase<>'finished'",roomId,ai.memberId);
    co_await RequestMemo::finish(tx, session.request, actor,
        removed.affectedRows() ? Json::Value(Json::Int64(ai.memberId)) : Json::Value());
    co_await commit(std::move(tx));
    if (removed.affectedRows() == 0)
    {
        co_return std::nullopt;
    }
    co_return ai.memberId;
}

drogon::Task<Json::Value> AiService::keys(Session session)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT provider, last4, UNIX_TIMESTAMP(updated_at) * 1000 AS updated_at_ms "
        "FROM user_api_keys WHERE user_id = ? ORDER BY provider",
        session.userId);
    Json::Value keys(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value key;
        key["provider"] = row["provider"].as<std::string>();
        key["last4"] = row["last4"].as<std::string>();
        key["updated_at"] = Json::Int64(row["updated_at_ms"].as<double>());
        keys.append(std::move(key));
    }
    Json::Value body;
    body["keys"] = std::move(keys);
    co_return body;
}

drogon::Task<> AiService::deleteKey(Session session, std::string provider)
{
    if (!isProvider(provider))
    {
        throw ApiError::invalidArgument("unknown provider");
    }
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.deleteKey")) co_return;
    co_await tx->execSqlCoro(
        "DELETE FROM user_api_keys WHERE user_id = ? AND provider = ?",
        session.userId,
        provider);
    co_await RequestMemo::finish(tx, session.request, actor, Json::objectValue);
    co_await commit(std::move(tx));
}
drogon::Task<Json::Value> AiService::launchTemplate(Session session, int64_t roomId, std::string templateId)
{
    const auto table = TableTemplates::find(templateId);
    if (!table) throw ApiError::invalidArgument("unknown table template");
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "ai.template")) co_return *prior;
    const auto role = co_await lockedRole(tx, roomId, session.memberId);
    if (!canManage(role)) throw ApiError::forbidden();
    Json::Value result;
    result["template"] = table->id;
    result["joined"] = Json::arrayValue;
    result["skipped"] = Json::arrayValue;
    std::vector<int64_t> chairs;
    for (const auto &ai : table->ais)
    {
        const auto types = co_await tx->execSqlCoro("SELECT provider FROM ai_model_types WHERE model_slug=?", ai.model);
        if (types.empty()) throw ApiError::invalidArgument("template uses an unknown model: " + ai.model);
        const auto provider = types[0]["provider"].as<std::string>();
        const auto key = co_await tx->execSqlCoro(
            "SELECT 1 FROM user_api_keys WHERE user_id = ? AND provider = ?", session.userId, provider);
        if (key.empty())
        {
            Json::Value skipped;
            skipped["name"] = ai.name;
            skipped["provider"] = provider;
            result["skipped"].append(skipped);
            continue;
        }
        const auto inserted = co_await tx->execSqlCoro("INSERT INTO members (type,display_name) VALUES ('ai',?)", ai.name);
        const auto member = static_cast<int64_t>(inserted.insertId());
        co_await tx->execSqlCoro(
            "INSERT INTO ai_profiles (member_id,slug,provider,model_slug,creator_user_id,is_legacy) VALUES (?,?,?,?,?,FALSE)",
            member, "role_" + std::to_string(member), provider, ai.model, session.userId);
        co_await tx->execSqlCoro("INSERT INTO room_members (room_id, member_id) VALUES (?, ?)", roomId, member);
        co_await tx->execSqlCoro(
            "INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (?,?,UNHEX(?),?)",
            roomId, member, PayloadCrypto::randomToken().substr(0, 32), ai.persona);
        if (ai.chair) chairs.push_back(member);
        Json::Value joined;
        joined["id"] = Json::Int64(member);
        joined["name"] = ai.name;
        joined["model_slug"] = ai.model;
        joined["chair"] = ai.chair;
        result["joined"].append(joined);
    }
    // The council only makes sense with enough chairs to reach 3 valid votes.
    result["council"] = table->council && chairs.size() >= 3;
    if (result["council"].asBool())
    {
        co_await tx->execSqlCoro("DELETE FROM room_council WHERE room_id = ?", roomId);
        for (const auto chair : chairs)
            co_await tx->execSqlCoro("INSERT INTO room_council (room_id, member_id) VALUES (?, ?)", roomId, chair);
    }
    if (!result["joined"].empty())
        co_await tx->execSqlCoro("UPDATE rooms SET collaboration_enabled = TRUE WHERE id = ?", roomId);
    result["collaboration_enabled"] = !result["joined"].empty();
    co_await RequestMemo::finish(tx, session.request, actor, result);
    co_await commit(std::move(tx));
    co_return result;
}
}  // namespace chat
