#include "services/message_service.h"

#include <algorithm>
#include <drogon/drogon.h>
#include <sstream>
#include <set>

#include "common/config.h"
#include "common/trace.h"
#include "buffering/guarded_transaction.h"
#include "buffering/request_buffer.h"
#include "buffering/guarded_redis.h"
#include "common/util.h"
#include "services/room_service.h"
#include "services/message_rate_limiter.h"
#include "ws/room_hub.h"

namespace chat
{
namespace
{
constexpr size_t kMaxContentLength = 4000;
constexpr int kCacheTtlSeconds = 86400;

// Restrict candidates to basic CJK ideographs. Consecutive characters
// form a phrase whose ngram tokens exist whenever the exact substring is present.
std::string searchNgram(const std::string &keyword)
{
    if (keyword.size() < 6 || keyword.size() % 3 != 0) return {};
    for (size_t offset = 0; offset < keyword.size(); offset += 3)
    {
        const auto first = static_cast<unsigned char>(keyword[offset]);
        const auto second = static_cast<unsigned char>(keyword[offset + 1]);
        const auto third = static_cast<unsigned char>(keyword[offset + 2]);
        if ((first & 0xf0) != 0xe0 || (second & 0xc0) != 0x80 || (third & 0xc0) != 0x80)
            return {};
        const auto point = ((first & 0x0f) << 12) | ((second & 0x3f) << 6) | (third & 0x3f);
        if (point < 0x4e00 || point > 0x9fff) return {};
    }
    return "\"" + keyword + "\"";
}

std::string cacheKey(int64_t roomId)
{
    return "room:" + std::to_string(roomId) + ":recent";
}

Json::Value messageJson(int64_t roomId,
                        int64_t seq,
                        int64_t memberId,
                        const std::string &memberName,
                        const std::string &memberType,
                        const std::string &clientMsgId,
                        const std::string &content,
                        int64_t createdAtMs, bool stopped = false,
                        std::string outcome = "completed", uint32_t attempt = 0)
{
    Json::Value json;
    json["type"] = "message";
    json["room_id"] = Json::Int64(roomId);
    json["seq"] = Json::Int64(seq);
    json["member"]["id"] = Json::Int64(memberId);
    json["member"]["name"] = memberName;
    json["member"]["type"] = memberType;
    json["client_msg_id"] = clientMsgId;
    if (memberType == "ai")
    {
        // Lets clients replace the text they were shown while it streamed.
        json["generation_id"] = clientMsgId;
        json["generation_outcome"] = outcome;
        json["generation_attempt"] = attempt;
    }
    if (stopped) json["stopped"] = true;
    json["content"] = content;
    json["created_at"] = Json::Int64(createdAtMs);
    return json;
}

Json::Value rowJson(int64_t roomId, const drogon::orm::Row &row)
{
    return messageJson(roomId,
                       row["seq"].as<int64_t>(),
                       row["member_id"].as<int64_t>(),
                       row["display_name"].as<std::string>(),
                       row["member_type"].as<std::string>(),
                       row["client_msg_id"].as<std::string>(),
                       row["content"].as<std::string>(),
                       row["created_at_ms"].as<int64_t>(), row["stopped"].as<bool>(),
                       row["generation_outcome"].as<std::string>(), row["generation_attempt"].as<uint32_t>());
}

void validate(const std::string &clientMsgId, const std::string &content, bool allowEmpty = false)
{
    const auto idLength = utf8Length(clientMsgId);
    if (!idLength || *idLength < 1 || *idLength > 64)
    {
        throw ApiError::invalidArgument("client_msg_id must be 1 to 64 characters");
    }
    const auto length = utf8Length(content);
    if (!length || (!allowEmpty && *length < 1) || *length > kMaxContentLength)
    {
        throw ApiError::invalidArgument("content must be 1 to 4000 characters");
    }
}

// The [low, high] window a query asks for; empty when low > high.
std::pair<int64_t, int64_t> resolveRange(const HistoryQuery &query,
                                         int64_t lastSeq)
{
    if (query.after)
    {
        if (*query.after >= lastSeq)
        {
            return {1, 0};
        }
        return {*query.after + 1,
                *query.after + std::min(query.limit, lastSeq - *query.after)};
    }
    const auto high =
        query.before ? std::min(*query.before - 1, lastSeq) : lastSeq;
    return {std::max<int64_t>(1, high - query.limit + 1), high};
}

// The range from the cache, or nullopt unless every message is there.
drogon::Task<std::optional<Json::Value>> readCache(int64_t roomId,
                                                   int64_t low,
                                                   int64_t high)
{
    try
    {
        const auto result =
            co_await drogon::app().getRedisClient()->execCommandCoro(
                "ZRANGEBYSCORE %s %lld %lld",
                cacheKey(roomId).c_str(),
                static_cast<long long>(low),
                static_cast<long long>(high));
        const auto items = result.asArray();
        if (static_cast<int64_t>(items.size()) != high - low + 1)
        {
            co_return std::nullopt;
        }
        Json::Value messages(Json::arrayValue);
        Json::CharReaderBuilder builder;
        for (const auto &item : items)
        {
            std::istringstream stream(item.asString());
            Json::Value message;
            std::string errors;
            if (!Json::parseFromStream(builder, stream, &message, &errors))
            {
                co_return std::nullopt;
            }
            messages.append(std::move(message));
        }
        co_return messages;
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "history cache read: " << e.what();
        co_return std::nullopt;
    }
}
}  // namespace

namespace
{
struct Author
{
    int64_t memberId;
    std::string name;
    std::string type;
};

// Stores a message from any member: locks the room row, checks membership,
// returns the stored copy for a repeated client_msg_id, otherwise assigns the
// next seq.
drogon::Task<StoredMessage> store(Author author,
                                  int64_t roomId,
                                  std::string clientMsgId,
                                  std::string content, RequestContextPtr context = {}, int64_t rateUser = 0, Json::Value mentionIds = Json::nullValue, std::string admissionId = "", uint32_t attempt = 0)
{
    TraceSpan storage(safeTraceId(std::to_string(roomId) + ":" + clientMsgId), author.type == "ai" ? "ai.save" : "ws.send", "message_store");
    validate(clientMsgId, content, author.type == "ai");
    bool stopped = false;
    std::string outcome = "completed";
    uint32_t contentAttempt = attempt;
    auto tx = co_await newGuardedTransaction(context);
    const auto rooms = co_await tx->execSqlCoro(
        "SELECT last_seq FROM rooms WHERE id = ? FOR UPDATE", roomId);
    if (rooms.empty())
    {
        throw ApiError::roomNotFound();
    }
    const auto membership = co_await tx->execSqlCoro(
        "SELECT 1 FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        author.memberId);
    if (membership.empty())
    {
        throw ApiError::roomNotFound();
    }
    if (author.type == "ai")
    {
        const auto profile=co_await tx->execSqlCoro("SELECT p.is_legacy,HEX(a.admission_id) AS admission FROM ai_profiles p LEFT JOIN room_ai_personas a ON a.member_id=p.member_id AND a.room_id=? WHERE p.member_id=?",roomId,author.memberId);
        if(profile.empty() || (!profile[0]["is_legacy"].as<bool>() &&
            (admissionId.empty() || profile[0]["admission"].isNull() || profile[0]["admission"].as<std::string>()!=admissionId)))
            throw ApiError::invalidArgument("AI admission is stale");
        const auto generation = co_await tx->execSqlCoro(
            "SELECT content,phase,outcome,attempt,content_attempt,member_id,HEX(admission_id) AS admission FROM ai_generations "
            "WHERE generation_id = ? AND room_id = ? FOR UPDATE", clientMsgId, roomId);
        if (!generation.empty())
        {
            if (generation[0]["member_id"].as<int64_t>() != author.memberId)
                throw ApiError::invalidArgument("generation member mismatch");
            if(!profile[0]["is_legacy"].as<bool>() && (generation[0]["admission"].isNull() || generation[0]["admission"].as<std::string>()!=admissionId))
                throw ApiError::invalidArgument("generation admission mismatch");
            const auto phase = generation[0]["phase"].as<std::string>();
            if (phase == "generating") throw ApiError::unavailable();
            outcome = generation[0]["outcome"].as<std::string>();
            if (outcome == "cancelled" || outcome == "legacy_unknown")
                throw ApiError::invalidArgument("generation is retired");
            if (attempt && attempt != generation[0]["attempt"].as<uint32_t>())
                throw ApiError::invalidArgument("generation attempt is stale");
            stopped = outcome == "stopped";
            content = generation[0]["content"].as<std::string>();
            contentAttempt = outcome == "failed" ? generation[0]["content_attempt"].as<uint32_t>()
                                                 : generation[0]["attempt"].as<uint32_t>();
            validate(clientMsgId, content, outcome == "failed");
            co_await tx->execSqlCoro("UPDATE ai_generations SET phase='finished' WHERE generation_id=?", clientMsgId);
        }
        else if(!profile[0]["is_legacy"].as<bool>()) throw ApiError::invalidArgument("unregistered AI generation");
    }
    validate(clientMsgId, content, author.type == "ai" && outcome == "failed");
    if(!mentionIds.isNull() && (!mentionIds.isArray() || mentionIds.size()>64)) throw ApiError::invalidArgument("invalid mentions");
    std::set<int64_t> selected;
    for(const auto &target:mentionIds) {
        if(!target.isInt64() || target.asInt64()<=0) throw ApiError::invalidArgument("invalid mention target");
        selected.insert(target.asInt64());
    }
    const auto existing = co_await tx->execSqlCoro(
        "SELECT seq,content,created_at_ms,stopped,generation_outcome,generation_attempt FROM messages "
        "WHERE room_id = ? AND member_id = ? AND client_msg_id = ?",
        roomId,
        author.memberId,
        clientMsgId);
    if (!existing.empty())
    {
        if (existing[0]["content"].as<std::string>() != content)
            throw ApiError(drogon::k409Conflict,"request_conflict","message ID was already used for different content");
        const auto seq = existing[0]["seq"].as<int64_t>();
        if(author.type=="human") {
            const auto savedMentions=co_await tx->execSqlCoro("SELECT mm.member_id FROM message_mentions mm JOIN members m ON m.id=mm.member_id WHERE mm.room_id=? AND mm.seq=? AND m.type='ai'",roomId,seq);
            std::set<int64_t> prior;
            for(const auto &mention:savedMentions) prior.insert(mention["member_id"].as<int64_t>());
            if(prior!=selected) throw ApiError(drogon::k409Conflict,"request_conflict","message mention targets changed");
        }
        storage.finish("duplicate");
        co_return StoredMessage{
            messageJson(roomId,
                        seq,
                        author.memberId,
                        author.name,
                        author.type,
                        clientMsgId,
                        existing[0]["content"].as<std::string>(),
                        existing[0]["created_at_ms"].as<int64_t>(), existing[0]["stopped"].as<bool>(),
                        existing[0]["generation_outcome"].as<std::string>(), existing[0]["generation_attempt"].as<uint32_t>()),
            roomId,
            seq,
            true};
    }
    // An accepted message retains its original receipt after a mentioned AI
    // leaves. Current membership is checked only before storing new messages.
    for (const auto target : selected)
    {
        const auto current = co_await tx->execSqlCoro(
            "SELECT m.id FROM room_members rm JOIN members m ON m.id=rm.member_id "
            "WHERE rm.room_id=? AND m.id=? AND m.type='ai'", roomId, target);
        if (current.empty()) throw ApiError::invalidArgument("AI mention target left the room");
    }
    if (rateUser) co_await MessageRateLimiter::admit(rateUser, context);
    const auto seq = rooms[0]["last_seq"].as<int64_t>() + 1;
    const auto createdAtMs = trantor::Date::now().microSecondsSinceEpoch() / 1000;
    co_await tx->execSqlCoro("UPDATE rooms SET last_seq = ? WHERE id = ?",
                             seq,
                             roomId);
    co_await tx->execSqlCoro(
        "INSERT INTO messages "
        "(room_id,seq,member_id,client_msg_id,content,created_at_ms,stopped,generation_outcome,generation_attempt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        roomId,
        seq,
        author.memberId,
        clientMsgId,
        content,
        createdAtMs, stopped, outcome, contentAttempt);
    for(const auto &target:mentionIds) {
        if(!target.isInt64() || target.asInt64()<=0) throw ApiError::invalidArgument("invalid mention target");
        co_await tx->execSqlCoro("INSERT IGNORE INTO message_mentions (room_id,seq,member_id) SELECT ?,?,m.id FROM room_members rm JOIN members m ON m.id=rm.member_id WHERE rm.room_id=? AND m.id=? AND m.type='ai'",roomId,seq,roomId,target.asInt64());
    }
    const auto targets = co_await tx->execSqlCoro(
        "SELECT m.id, m.display_name FROM room_members rm JOIN members m ON m.id = rm.member_id "
        "WHERE rm.room_id = ? AND m.type = 'human' AND m.id <> ?", roomId, author.memberId);
    for (const auto &target : targets)
    {
        const auto token = "@" + target["display_name"].as<std::string>();
        auto position = content.find(token);
        while (position != std::string::npos)
        {
            const auto end = position + token.size();
            const auto boundary = [](unsigned char c) { return c == ' ' || c == '\n' || c == '\t' || c == '\r'; };
            if ((position == 0 || boundary(content[position - 1])) &&
                (end == content.size() || boundary(content[end])))
            {
                co_await tx->execSqlCoro("INSERT IGNORE INTO message_mentions (room_id, seq, member_id) VALUES (?, ?, ?)",
                    roomId, seq, target["id"].as<int64_t>());
                break;
            }
            position = content.find(token, position + 1);
        }
    }
    co_await commit(std::move(tx));
    storage.finish();
    co_return StoredMessage{messageJson(roomId,
                                        seq,
                                        author.memberId,
                                        author.name,
                                        author.type,
                                        clientMsgId,
                                        content,
                                        createdAtMs, stopped, outcome, contentAttempt),
                            roomId,
                            seq,
                            false};
}
}  // namespace

drogon::Task<StoredMessage> MessageService::save(const Session &session,
                                                 int64_t roomId,
                                                 std::string clientMsgId,
                                                 std::string content, Json::Value mentionIds)
{
    co_return co_await store(Author{session.memberId, session.username, "human"},
                             roomId,
                             std::move(clientMsgId),
                             std::move(content), session.request, session.userId, std::move(mentionIds));
}

drogon::Task<StoredMessage> MessageService::saveAiMessage(int64_t aiMemberId,
                                                          int64_t roomId,
                                                          std::string generationId,
                                                          std::string content, std::string admissionId, uint32_t attempt)
{
    auto context = RequestBuffer::instance().backgroundContext();
    if (context) context->requestId = safeTraceId(std::to_string(roomId) + ":" + generationId);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT display_name FROM members WHERE id = ? AND type = 'ai'", aiMemberId);
    if (rows.empty())
    {
        throw ApiError::invalidArgument("not an AI member");
    }
    // An AI's reply may be cut to fit, rather than rejected.
    if (const auto length = utf8Length(content); length && *length > kMaxContentLength)
    {
        content = utf8Prefix(content, kMaxContentLength);
    }
    co_return co_await store(
        Author{aiMemberId, rows[0]["display_name"].as<std::string>(), "ai"},
        roomId,
        std::move(generationId),
        std::move(content), std::move(context),0,Json::nullValue,std::move(admissionId),attempt);
}

drogon::Task<> MessageService::distribute(const StoredMessage &message, RequestContextPtr context)
{
    if (!context && RequestBuffer::instance().enabled())
    {
        try { context = RequestBuffer::instance().backgroundContext(); }
        catch (const ApiError &) { co_return; }
    }
    const auto payload = toJsonString(message.json);
    // Distribution may start after its durable ACK, even on another backend.
    // Serialize the cache write with SQL room deletion: a late job sees no
    // row, and an already-running writer must finish before DELETE commits.
    auto roomGuard = co_await newGuardedTransaction(context);
    const auto rooms = co_await roomGuard->execSqlCoro(
        "SELECT id FROM rooms WHERE id = ? FOR SHARE", message.roomId);
    if (rooms.empty()) co_return;
    try
    {
        auto redis = guardedRedis(context);
        const auto key = cacheKey(message.roomId);
        co_await redis->execCommandCoro("ZADD %s %lld %s",
                                        key.c_str(),
                                        static_cast<long long>(message.seq),
                                        payload.c_str());
        co_await redis->execCommandCoro(
            "ZREMRANGEBYRANK %s 0 %lld",
            key.c_str(),
            -static_cast<long long>(Config::get().hotCacheSize) - 1);
        co_await redis->execCommandCoro("EXPIRE %s %d",
                                        key.c_str(),
                                        kCacheTtlSeconds);
    }
    catch (const ApiError &) { co_return; }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "hot cache write: " << e.what();
    }
    roomGuard.reset(); // read-only rollback releases the room's shared lock
    co_await RoomHub::instance().publish(message.roomId, payload, context);
}

drogon::Task<Json::Value> MessageService::history(Session session,
                                                  int64_t roomId,
                                                  HistoryQuery query)
{
    if (query.before && query.after)
    {
        throw ApiError::invalidArgument("use either before or after");
    }
    const auto lastSeq =
        co_await RoomService::requireMember(session.memberId, roomId);
    const auto [low, high] = resolveRange(query, lastSeq);

    Json::Value body;
    body["last_seq"] = Json::Int64(lastSeq);
    body["messages"] = Json::arrayValue;
    if (low > high)
    {
        co_return body;
    }
    if (auto cached = co_await readCache(roomId, low, high))
    {
        body["messages"] = std::move(*cached);
        co_return body;
    }
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT msg.seq, msg.member_id, msg.client_msg_id, msg.content, "
        "       msg.created_at_ms, msg.stopped, msg.generation_outcome, msg.generation_attempt, m.display_name, m.type AS member_type "
        "FROM messages msg JOIN members m ON m.id = msg.member_id "
        "WHERE msg.room_id = ? AND msg.seq BETWEEN ? AND ? ORDER BY msg.seq",
        roomId,
        low,
        high);
    for (const auto &row : rows)
    {
        body["messages"].append(rowJson(roomId, row));
    }
    co_return body;
}

drogon::Task<> MessageService::dropCache(int64_t roomId, RequestContextPtr context)
{
    const auto unknown = [] {
        return ApiError(drogon::k503ServiceUnavailable, "result_unknown",
                        "room deletion committed; cache cleanup is unconfirmed");
    };
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "DEL %s", cacheKey(roomId).c_str());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "hot cache drop: " << e.what();
        throw unknown();
    }
    catch (const ApiError &) { throw unknown(); }
}
drogon::Task<Json::Value> MessageService::search(Session session, int64_t roomId,
    std::string keyword, std::optional<int64_t> sender, std::optional<int64_t> from,
    std::optional<int64_t> to, std::optional<int64_t> before, int64_t limit)
{
    co_await RoomService::requireMember(session.memberId, roomId);
    const auto length = utf8Length(keyword);
    if (!length || *length > 200 || (from && to && *from > *to))
        throw ApiError::invalidArgument("invalid search filter");
    auto candidate = searchNgram(keyword);
    if (!candidate.empty())
    {
        try
        {
            const auto config = co_await drogon::app().getDbClient()->execSqlCoro(
                "SELECT @@ngram_token_size AS token_size, "
                "(SELECT COUNT(*) FROM message_search_stopwords) AS stopwords");
            if (config[0]["token_size"].as<int>() != 2 || config[0]["stopwords"].as<int>() != 0)
                candidate.clear();
        }
        catch (const drogon::orm::DrogonDbException &)
        {
            candidate.clear();
        }
    }
    const auto query = [&](bool indexed) -> drogon::Task<drogon::orm::Result> {
        const std::string predicate = indexed
            ? "AND MATCH(msg.content) AGAINST (? IN BOOLEAN MODE) "
            : "AND ? = '' ";
        co_return co_await drogon::app().getDbClient()->execSqlCoro(
            "SELECT msg.seq, msg.member_id, msg.client_msg_id, msg.content,msg.stopped,msg.generation_outcome,msg.generation_attempt, "
            "msg.created_at_ms, m.display_name, m.type AS member_type "
            "FROM messages msg JOIN members m ON m.id = msg.member_id "
            "JOIN room_members rm ON rm.room_id = msg.room_id AND rm.member_id = ? "
            "WHERE msg.room_id = ? " + predicate +
            "AND (? = '' OR LOCATE(?, msg.content) > 0) "
            "AND (? = 0 OR msg.member_id = ?) AND msg.created_at_ms >= ? AND msg.created_at_ms <= ? "
            "AND msg.seq < ? ORDER BY msg.seq DESC LIMIT ?",
            session.memberId, roomId, indexed ? candidate : std::string(), keyword, keyword,
            sender.value_or(0), sender.value_or(0), from.value_or(0), to.value_or(INT64_MAX),
            before.value_or(INT64_MAX), limit);
    };
    std::optional<drogon::orm::Result> loaded;
    try
    {
        loaded.emplace(co_await query(!candidate.empty()));
    }
    catch (const drogon::orm::DrogonDbException &)
    {
        // A missing/rebuilding index must preserve the literal result set.
        if (candidate.empty()) throw;
    }
    if (!loaded) loaded.emplace(co_await query(false));
    const auto &rows = *loaded;
    Json::Value result;
    result["messages"] = Json::arrayValue;
    for (const auto &row : rows) result["messages"].append(rowJson(roomId, row));
    result["next_before"] = rows.empty() ? Json::Value() : Json::Value(Json::Int64(rows[rows.size()-1]["seq"].as<int64_t>()));
    co_return result;
}
}  // namespace chat
