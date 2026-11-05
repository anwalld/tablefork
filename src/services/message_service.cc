#include "services/message_service.h"

#include <algorithm>
#include <drogon/drogon.h>
#include <sstream>

#include "common/config.h"
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
                        int64_t createdAtMs)
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
    }
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
                       row["created_at_ms"].as<int64_t>());
}

void validate(const std::string &clientMsgId, const std::string &content)
{
    const auto idLength = utf8Length(clientMsgId);
    if (!idLength || *idLength < 1 || *idLength > 64)
    {
        throw ApiError::invalidArgument("client_msg_id must be 1 to 64 characters");
    }
    const auto length = utf8Length(content);
    if (!length || *length < 1 || *length > kMaxContentLength)
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
                                  std::string content, RequestContextPtr context = {}, int64_t rateUser = 0)
{
    validate(clientMsgId, content);
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
    const auto existing = co_await tx->execSqlCoro(
        "SELECT seq, content, created_at_ms FROM messages "
        "WHERE room_id = ? AND member_id = ? AND client_msg_id = ?",
        roomId,
        author.memberId,
        clientMsgId);
    if (!existing.empty())
    {
        if (existing[0]["content"].as<std::string>() != content)
            throw ApiError(drogon::k409Conflict,"request_conflict","message ID was already used for different content");
        const auto seq = existing[0]["seq"].as<int64_t>();
        co_return StoredMessage{
            messageJson(roomId,
                        seq,
                        author.memberId,
                        author.name,
                        author.type,
                        clientMsgId,
                        existing[0]["content"].as<std::string>(),
                        existing[0]["created_at_ms"].as<int64_t>()),
            roomId,
            seq,
            true};
    }
    if (rateUser) co_await MessageRateLimiter::admit(rateUser, context);
    const auto seq = rooms[0]["last_seq"].as<int64_t>() + 1;
    const auto createdAtMs = trantor::Date::now().microSecondsSinceEpoch() / 1000;
    co_await tx->execSqlCoro("UPDATE rooms SET last_seq = ? WHERE id = ?",
                             seq,
                             roomId);
    co_await tx->execSqlCoro(
        "INSERT INTO messages "
        "(room_id, seq, member_id, client_msg_id, content, created_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        roomId,
        seq,
        author.memberId,
        clientMsgId,
        content,
        createdAtMs);
    co_await commit(std::move(tx));
    co_return StoredMessage{messageJson(roomId,
                                        seq,
                                        author.memberId,
                                        author.name,
                                        author.type,
                                        clientMsgId,
                                        content,
                                        createdAtMs),
                            roomId,
                            seq,
                            false};
}
}  // namespace

drogon::Task<StoredMessage> MessageService::save(const Session &session,
                                                 int64_t roomId,
                                                 std::string clientMsgId,
                                                 std::string content)
{
    co_return co_await store(Author{session.memberId, session.username, "human"},
                             roomId,
                             std::move(clientMsgId),
                             std::move(content), session.request, session.userId);
}

drogon::Task<StoredMessage> MessageService::saveAiMessage(int64_t aiMemberId,
                                                          int64_t roomId,
                                                          std::string generationId,
                                                          std::string content)
{
    auto context = RequestBuffer::instance().backgroundContext();
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
        std::move(content), std::move(context));
}

drogon::Task<> MessageService::distribute(const StoredMessage &message, RequestContextPtr context)
{
    if (!context && RequestBuffer::instance().enabled())
    {
        try { context = RequestBuffer::instance().backgroundContext(); }
        catch (const ApiError &) { co_return; }
    }
    const auto payload = toJsonString(message.json);
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
        "       msg.created_at_ms, m.display_name, m.type AS member_type "
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
    try
    {
        co_await guardedRedis(context)->execCommandCoro(
            "DEL %s", cacheKey(roomId).c_str());
    }
    catch (const drogon::nosql::RedisException &e)
    {
        LOG_WARN << "hot cache drop: " << e.what();
    }
}
}  // namespace chat
