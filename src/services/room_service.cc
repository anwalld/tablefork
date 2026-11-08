#include "services/ai_service.h"
#include "services/room_service.h"

#include <algorithm>
#include <drogon/drogon.h>
#include <optional>

#include "common/util.h"
#include "buffering/request_memo.h"

namespace chat
{
namespace
{
using TransactionPtr = GuardedTransactionPtr;

// Every membership change locks the room row first, so that "is this the last
// human?" cannot race with a join or an invite.
drogon::Task<> lockRoom(const TransactionPtr &tx, int64_t roomId)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT id FROM rooms WHERE id = ? FOR UPDATE", roomId);
    if (rows.empty())
    {
        throw ApiError::roomNotFound();
    }
}

drogon::Task<bool> isMember(const TransactionPtr &tx,
                            int64_t roomId,
                            int64_t memberId)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT 1 FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    co_return !rows.empty();
}

// The member's role in the room, or nothing for a non-member.
drogon::Task<std::optional<std::string>> roleOf(const TransactionPtr &tx,
                                                int64_t roomId,
                                                int64_t memberId)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    if (rows.empty())
    {
        co_return std::nullopt;
    }
    co_return rows[0]["role"].as<std::string>();
}

// Locks the room and checks that the caller owns it.
drogon::Task<> requireOwner(const TransactionPtr &tx,
                            int64_t roomId,
                            int64_t memberId)
{
    co_await lockRoom(tx, roomId);
    const auto role = co_await roleOf(tx, roomId, memberId);
    if (!role)
    {
        throw ApiError::roomNotFound();
    }
    if (*role != "owner")
    {
        throw ApiError::forbidden();
    }
}

// A human member of the room, by user name.
drogon::Task<int64_t> humanMember(const TransactionPtr &tx,
                                  int64_t roomId,
                                  const std::string &username)
{
    const auto rows = co_await tx->execSqlCoro(
        "SELECT m.id FROM users u "
        "JOIN members m ON m.user_id = u.id "
        "JOIN room_members rm ON rm.member_id = m.id AND rm.room_id = ? "
        "WHERE u.username = ?",
        roomId,
        username);
    if (rows.empty())
    {
        throw ApiError::invalidArgument("not a human member of this room");
    }
    co_return rows[0]["id"].as<int64_t>();
}
struct ManagedHuman
{
    int64_t userId;
    int64_t memberId;
    bool inRoom;
};

drogon::Task<> requireManager(const TransactionPtr &tx,
                               int64_t roomId,
                               int64_t memberId)
{
    const auto actor = co_await roleOf(tx, roomId, memberId);
    if (!actor)
    {
        throw ApiError::roomNotFound();
    }
    if (!RoomService::canManage(*actor, "none"))
    {
        throw ApiError::forbidden();
    }
}

drogon::Task<ManagedHuman> managedHuman(const TransactionPtr &tx,
                                        const Session &session,
                                        int64_t roomId,
                                        const std::string &username,
                                        bool kicking)
{
    const auto actor = co_await roleOf(tx, roomId, session.memberId);
    if (!actor)
    {
        throw ApiError::roomNotFound();
    }
    const auto users = co_await tx->execSqlCoro(
        "SELECT u.id, m.id AS member_id, rm.role FROM users u "
        "JOIN members m ON m.user_id = u.id AND m.type = 'human' "
        "LEFT JOIN room_members rm ON rm.member_id = m.id AND rm.room_id = ? "
        "WHERE u.username = ?", roomId, username);
    if (users.empty())
    {
        if (kicking)
        {
            const auto ai = co_await tx->execSqlCoro(
                "SELECT rm.role FROM members m JOIN ai_profiles p ON p.member_id = m.id "
                "LEFT JOIN room_members rm ON rm.member_id = m.id AND rm.room_id = ? "
                "WHERE m.display_name = ? OR p.slug = ? LIMIT 1", roomId, username, username);
            if (!ai.empty())
            {
                const auto role = ai[0]["role"].isNull() ? "none" : ai[0]["role"].as<std::string>();
                if (!RoomService::canManage(*actor, role))
                {
                    throw ApiError::forbidden();
                }
                throw ApiError::invalidArgument("use AI removal for AI members");
            }
        }
        throw ApiError(drogon::k404NotFound, "user_not_found", "user not found");
    }
    const auto memberId = users[0]["member_id"].as<int64_t>();
    if (memberId == session.memberId)
    {
        throw ApiError::invalidArgument("use leave to leave the room yourself");
    }
    const auto role = users[0]["role"].isNull() ? "none" : users[0]["role"].as<std::string>();
    if (!RoomService::canManage(*actor, role))
    {
        throw ApiError::forbidden();
    }
    if (kicking && role == "none")
    {
        throw ApiError::invalidArgument("not a human member of this room");
    }
    co_return ManagedHuman{users[0]["id"].as<int64_t>(), memberId, role != "none"};
}

}  // namespace

drogon::Task<Json::Value> RoomService::create(Session session, std::string name)
{
    const auto length = utf8Length(name);
    if (!isPrintableText(name) || *length < 1 || *length > 64)
    {
        throw ApiError::invalidArgument(
            "name must be 1 to 64 printable characters");
    }
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.create"))
    {
        co_return *prior;
    }
    const auto room =
        co_await tx->execSqlCoro("INSERT INTO rooms (name) VALUES (?)", name);
    const auto roomId = static_cast<int64_t>(room.insertId());
    co_await tx->execSqlCoro(
        "INSERT INTO room_members (room_id, member_id, role) "
        "VALUES (?, ?, 'owner')",
        roomId,
        session.memberId);

    Json::Value body;
    body["id"] = Json::Int64(roomId);
    body["name"] = name;
    body["member_count"] = 1;
    body["status"] = "joined";
    body["last_seq"] = 0;
    co_await RequestMemo::finish(tx, session.request, actor, body);
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return body;
}

drogon::Task<Json::Value> RoomService::createBreakout(Session session,
                                                       int64_t parentRoomId,
                                                       std::string topic,
                                                       std::vector<int64_t> memberIds)
{
    constexpr uint64_t kMaxDepth = 3;
    if (topic.empty() || !utf8Length(topic) || *utf8Length(topic) > 64)
        throw ApiError::invalidArgument("breakout topic must be 1 to 64 characters");
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.breakout"))
        co_return *prior;
    const auto parent = co_await tx->execSqlCoro(
        "SELECT depth, last_seq FROM rooms WHERE id = ? FOR UPDATE", parentRoomId);
    if (parent.empty()) throw ApiError::roomNotFound();
    const auto depth = parent[0]["depth"].as<uint64_t>();
    if (depth >= kMaxDepth) throw ApiError::invalidArgument("breakout depth limit reached");
    const auto forkSeq = parent[0]["last_seq"].as<int64_t>();
    // The caller must be a member of the parent, the breakout participants too.
    co_await requireMember(session.memberId, parentRoomId);
    std::vector<int64_t> participants;
    for (const auto memberId : memberIds)
    {
        if (memberId == session.memberId) continue;
        const auto rows = co_await tx->execSqlCoro(
            "SELECT 1 FROM room_members WHERE room_id = ? AND member_id = ?",
            parentRoomId, memberId);
        if (rows.empty()) throw ApiError::invalidArgument("breakout members must be in the parent room");
        if (std::find(participants.begin(), participants.end(), memberId) == participants.end())
            participants.push_back(memberId);
    }
    const auto room = co_await tx->execSqlCoro(
        "INSERT INTO rooms (name, parent_room_id, fork_seq, depth, topic, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        topic, parentRoomId, forkSeq, depth + 1, topic);
    const auto roomId = static_cast<int64_t>(room.insertId());
    co_await tx->execSqlCoro("INSERT INTO room_members (room_id, member_id, role) VALUES (?, ?, 'owner')",
                             roomId, session.memberId);
    for (const auto memberId : participants)
        co_await tx->execSqlCoro("INSERT INTO room_members (room_id, member_id) VALUES (?, ?)", roomId, memberId);
    Json::Value body;
    body["id"] = Json::Int64(roomId);
    body["name"] = topic;
    body["parent_room_id"] = Json::Int64(parentRoomId);
    body["fork_seq"] = Json::Int64(forkSeq);
    body["status"] = "active";
    co_await RequestMemo::finish(tx, session.request, actor, body);
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return body;
}

drogon::Task<Json::Value> RoomService::list(Session session,
                                            int64_t limit,
                                            int64_t offset)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT r.id, r.name, r.last_seq, m.member_id, "
        "COALESCE(m.read_seq, r.last_seq) AS read_seq, "
        "(SELECT COUNT(*) FROM messages msg WHERE msg.room_id = r.id "
        " AND msg.seq > m.read_seq AND msg.member_id <> m.member_id) AS unread_count, "
        "(SELECT MIN(mm.seq) FROM message_mentions mm WHERE mm.room_id = r.id "
        " AND mm.member_id = m.member_id AND mm.seq > m.read_seq) AS mention_seq, "
        "       (SELECT COUNT(*) FROM room_members c WHERE c.room_id = r.id) "
        "           AS member_count "
        "FROM rooms r "
        "LEFT JOIN room_members m ON m.room_id = r.id AND m.member_id = ? "
        "LEFT JOIN room_invites i ON i.room_id = r.id AND i.user_id = ? "
        "WHERE m.member_id IS NOT NULL OR i.user_id IS NOT NULL "
        "ORDER BY r.id DESC LIMIT ? OFFSET ?",
        session.memberId,
        session.userId,
        limit,
        offset);
    Json::Value rooms(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value room;
        room["id"] = Json::Int64(row["id"].as<int64_t>());
        room["name"] = row["name"].as<std::string>();
        room["member_count"] = Json::Int64(row["member_count"].as<int64_t>());
        room["status"] = row["member_id"].isNull() ? "invited" : "joined";
        room["last_seq"] = Json::Int64(row["last_seq"].as<int64_t>());
        room["read_seq"] = Json::Int64(row["read_seq"].as<int64_t>());
        room["unread_count"] = Json::Int64(row["unread_count"].as<int64_t>());
        room["mention_seq"] = row["mention_seq"].isNull() ? Json::Value() : Json::Value(Json::Int64(row["mention_seq"].as<int64_t>()));
        rooms.append(std::move(room));
    }
    Json::Value body;
    body["rooms"] = std::move(rooms);
    co_return body;
}

drogon::Task<> RoomService::markRead(Session session, int64_t roomId, int64_t seq)
{
    if (seq < 0) throw ApiError::invalidArgument("invalid read sequence");
    auto tx = co_await newGuardedTransaction(session.request);
    co_await lockRoom(tx, roomId);
    if (!co_await isMember(tx, roomId, session.memberId)) throw ApiError::roomNotFound();
    co_await tx->execSqlCoro(
        "UPDATE room_members rm JOIN rooms r ON r.id = rm.room_id "
        "SET rm.read_seq = GREATEST(rm.read_seq, LEAST(?, r.last_seq)) "
        "WHERE rm.room_id = ? AND rm.member_id = ?", seq, roomId, session.memberId);
    co_await commit(std::move(tx));
}

drogon::Task<> RoomService::invite(Session session,
                                   int64_t roomId,
                                   std::string username)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.invite"))
    {
        co_return;
    }
    co_await lockRoom(tx, roomId);
    if (!co_await isMember(tx, roomId, session.memberId))
    {
        throw ApiError::roomNotFound();
    }
    const auto users = co_await tx->execSqlCoro(
        "SELECT u.id, m.id AS member_id FROM users u "
        "JOIN members m ON m.user_id = u.id WHERE u.username = ?",
        username);
    if (users.empty())
    {
        throw ApiError(drogon::k404NotFound, "user_not_found", "user not found");
    }
    const auto userId = users[0]["id"].as<int64_t>();
    if (!co_await isMember(tx, roomId, users[0]["member_id"].as<int64_t>()))
    {
        const auto banned = co_await tx->execSqlCoro(
            "SELECT 1 FROM room_bans WHERE room_id = ? AND user_id = ?", roomId, userId);
        if (!banned.empty())
        {
            throw ApiError(drogon::k409Conflict, "user_banned", "user is banned from this room");
        }
        co_await tx->execSqlCoro(
            "INSERT IGNORE INTO room_invites (room_id, user_id, invited_by) "
            "VALUES (?, ?, ?)",
            roomId,
            userId,
            session.memberId);
    }
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    checkRequest(session.request);
    co_await commit(std::move(tx));
}

drogon::Task<> RoomService::join(Session session, int64_t roomId)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.join"))
    {
        co_return;
    }
    co_await lockRoom(tx, roomId);
    if (!co_await isMember(tx, roomId, session.memberId))
    {
        const auto banned = co_await tx->execSqlCoro(
            "SELECT 1 FROM room_bans WHERE room_id = ? AND user_id = ?", roomId, session.userId);
        if (!banned.empty())
        {
            throw ApiError::roomNotFound();
        }
        const auto invite = co_await tx->execSqlCoro(
            "DELETE FROM room_invites WHERE room_id = ? AND user_id = ?",
            roomId,
            session.userId);
        if (invite.affectedRows() == 0)
        {
            throw ApiError::roomNotFound();
        }
        co_await tx->execSqlCoro(
            "INSERT INTO room_members (room_id, member_id, read_seq) "
            "SELECT id, ?, last_seq FROM rooms WHERE id = ?",
            session.memberId,
            roomId);
    }
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    checkRequest(session.request);
    co_await commit(std::move(tx));
}

drogon::Task<bool> RoomService::leave(Session session,
                                      int64_t roomId,
                                      bool confirmDelete)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.leave"))
    {
        co_return prior->asBool();
    }
    co_await lockRoom(tx, roomId);
    const auto role = co_await roleOf(tx, roomId, session.memberId);
    if (!role)
    {
        throw ApiError::roomNotFound();
    }
    const auto otherHumans = co_await tx->execSqlCoro(
        "SELECT COUNT(*) AS n FROM room_members rm "
        "JOIN members m ON m.id = rm.member_id "
        "WHERE rm.room_id = ? AND m.type = 'human' AND rm.member_id <> ?",
        roomId,
        session.memberId);
    const auto deleted = otherHumans[0]["n"].as<int64_t>() == 0;
    if (!deleted && *role == "owner")
    {
        throw ApiError(drogon::k409Conflict,
                       "owner_must_transfer",
                       "transfer ownership before leaving");
    }
    if (deleted && !confirmDelete)
    {
        throw ApiError(drogon::k409Conflict,
                       "confirmation_required",
                       "leaving would delete the room");
    }
    if (deleted)
    {
        co_await tx->execSqlCoro("DELETE FROM rooms WHERE id = ?", roomId);
    }
    else
    {
        co_await AiService::removeOwned(tx,roomId,session.userId);
        co_await tx->execSqlCoro(
            "DELETE FROM room_members WHERE room_id = ? AND member_id = ?",
            roomId,
            session.memberId);
    }
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(deleted));
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return deleted;
}

drogon::Task<Json::Value> RoomService::members(Session session, int64_t roomId)
{
    co_await requireMember(session.memberId, roomId);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT m.id,m.type,m.display_name,rm.role,p.slug,p.provider,p.model_slug,p.creator_user_id,p.is_legacy,u.username,a.persona,a.persona_version,HEX(a.admission_id) AS admission "
        "FROM room_members rm "
        "JOIN members m ON m.id = rm.member_id "
        "LEFT JOIN ai_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=p.creator_user_id "
        "LEFT JOIN room_ai_personas a ON a.room_id=rm.room_id AND a.member_id=m.id "
        "WHERE rm.room_id = ? ORDER BY rm.joined_at, m.id",
        roomId);
    Json::Value members(Json::arrayValue);
    for (const auto &row : rows)
    {
        Json::Value member;
        member["id"] = Json::Int64(row["id"].as<int64_t>());
        member["type"] = row["type"].as<std::string>();
        member["name"] = row["display_name"].as<std::string>();
        member["role"] = row["role"].as<std::string>();
        if (!row["slug"].isNull())
        {
            member["slug"] = row["slug"].as<std::string>();
            member["provider"] = row["provider"].as<std::string>();
            member["model_slug"]=row["model_slug"].as<std::string>();
            if(!row["admission"].isNull()) member["admission_id"]=row["admission"].as<std::string>();
            member["is_legacy"]=row["is_legacy"].as<bool>();
            member["creator_user_id"]=row["creator_user_id"].isNull()?Json::Value():Json::Value(Json::Int64(row["creator_user_id"].as<int64_t>()));
            member["creator_name"]=row["username"].isNull()?"":row["username"].as<std::string>();
            if(!row["persona_version"].isNull()) member["persona_version"]=Json::Int64(row["persona_version"].as<int64_t>());
            if(!row["creator_user_id"].isNull() && row["creator_user_id"].as<int64_t>()==session.userId)
                member["persona"]=row["persona"].isNull()?"":row["persona"].as<std::string>();
        }
        members.append(std::move(member));
    }
    Json::Value body;
    body["members"] = std::move(members);
    co_return body;
}

drogon::Task<int64_t> RoomService::transferOwner(Session session,
                                                 int64_t roomId,
                                                 std::string username)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.transferOwner"))
    {
        co_return prior->asInt64();
    }
    co_await requireOwner(tx, roomId, session.memberId);
    const auto target = co_await humanMember(tx, roomId, username);
    if (target != session.memberId)
    {
        co_await tx->execSqlCoro(
            "UPDATE room_members SET role = 'member', role_changed_at = NOW(3) "
            "WHERE room_id = ? AND member_id = ?",
            roomId,
            session.memberId);
        co_await tx->execSqlCoro(
            "UPDATE room_members SET role = 'owner', role_changed_at = NOW(3) "
            "WHERE room_id = ? AND member_id = ?",
            roomId,
            target);
    }
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::Int64(target)));
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return target;
}

drogon::Task<int64_t> RoomService::setAdmin(Session session,
                                            int64_t roomId,
                                            std::string username,
                                            bool admin)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.setAdmin"))
    {
        co_return prior->asInt64();
    }
    co_await requireOwner(tx, roomId, session.memberId);
    const auto target = co_await humanMember(tx, roomId, username);
    if (target == session.memberId)
    {
        throw ApiError::invalidArgument("the owner cannot be an admin");
    }
    co_await tx->execSqlCoro(
        "UPDATE room_members SET role = ?, role_changed_at = NOW(3) "
        "WHERE room_id = ? AND member_id = ?",
        std::string(admin ? "admin" : "member"),
        roomId,
        target);
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::Int64(target)));
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return target;
}

bool RoomService::canManage(std::string_view actorRole, std::string_view targetRole)
{
    return (actorRole == "owner" && (targetRole == "admin" || targetRole == "member" || targetRole == "none")) ||
           (actorRole == "admin" && (targetRole == "member" || targetRole == "none"));
}

drogon::Task<int64_t> RoomService::kick(Session session, int64_t roomId, std::string username)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.kick"))
    {
        co_return prior->asInt64();
    }
    co_await lockRoom(tx, roomId);
    const auto target = co_await managedHuman(tx, session, roomId, username, true);
    co_await AiService::removeOwned(tx,roomId,target.userId);
    co_await tx->execSqlCoro("DELETE FROM room_members WHERE room_id = ? AND member_id = ?", roomId, target.memberId);
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::Int64(target.memberId)));
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return target.memberId;
}

drogon::Task<int64_t> RoomService::ban(Session session, int64_t roomId, std::string username)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.ban"))
    {
        co_return prior->asInt64();
    }
    co_await lockRoom(tx, roomId);
    const auto target = co_await managedHuman(tx, session, roomId, username, false);
    co_await tx->execSqlCoro(
        "INSERT IGNORE INTO room_bans (room_id, user_id, banned_by) VALUES (?, ?, ?)",
        roomId, target.userId, session.memberId);
    co_await tx->execSqlCoro("DELETE FROM room_invites WHERE room_id = ? AND user_id = ?", roomId, target.userId);
    if (target.inRoom)
    {
        co_await AiService::removeOwned(tx,roomId,target.userId);
    co_await tx->execSqlCoro("DELETE FROM room_members WHERE room_id = ? AND member_id = ?", roomId, target.memberId);
    }
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::Int64(target.inRoom ? target.memberId : 0)));
    checkRequest(session.request);
    co_await commit(std::move(tx));
    co_return target.inRoom ? target.memberId : 0;
}

drogon::Task<> RoomService::unban(Session session, int64_t roomId, std::string username)
{
    auto tx = co_await newGuardedTransaction(session.request);
    const auto actor = "user:" + std::to_string(session.userId);
    if (auto prior = co_await RequestMemo::begin(tx, session.request, actor, "room.unban"))
    {
        co_return;
    }
    co_await lockRoom(tx, roomId);
    co_await requireManager(tx, roomId, session.memberId);
    const auto users = co_await tx->execSqlCoro("SELECT id FROM users WHERE username = ?", username);
    if (users.empty())
    {
        throw ApiError(drogon::k404NotFound, "user_not_found", "user not found");
    }
    co_await tx->execSqlCoro("DELETE FROM room_bans WHERE room_id = ? AND user_id = ?", roomId, users[0]["id"].as<int64_t>());
    co_await RequestMemo::finish(tx, session.request, actor, Json::Value(Json::objectValue));
    checkRequest(session.request);
    co_await commit(std::move(tx));
}

drogon::Task<Json::Value> RoomService::bans(Session session, int64_t roomId)
{
    auto tx = co_await newGuardedTransaction(session.request);
    co_await requireManager(tx, roomId, session.memberId);
    const auto rows = co_await tx->execSqlCoro(
        "SELECT u.username, bu.username AS banned_by, UNIX_TIMESTAMP(b.created_at) * 1000 AS created_at_ms "
        "FROM room_bans b JOIN users u ON u.id = b.user_id "
        "LEFT JOIN members bm ON bm.id = b.banned_by LEFT JOIN users bu ON bu.id = bm.user_id "
        "WHERE b.room_id = ? ORDER BY b.created_at, b.user_id", roomId);
    Json::Value body;
    body["bans"] = Json::arrayValue;
    for (const auto &row : rows)
    {
        Json::Value ban;
        ban["username"] = row["username"].as<std::string>();
        ban["banned_by"] = row["banned_by"].isNull() ? Json::Value() : Json::Value(row["banned_by"].as<std::string>());
        ban["created_at"] = Json::Int64(row["created_at_ms"].as<double>());
        body["bans"].append(std::move(ban));
    }
    co_await commit(std::move(tx));
    co_return body;
}

drogon::Task<std::string> RoomService::requireRole(
    int64_t memberId,
    int64_t roomId,
    std::vector<std::string> allowed)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT role FROM room_members WHERE room_id = ? AND member_id = ?",
        roomId,
        memberId);
    if (rows.empty())
    {
        throw ApiError::roomNotFound();
    }
    auto role = rows[0]["role"].as<std::string>();
    if (std::find(allowed.begin(), allowed.end(), role) == allowed.end())
    {
        throw ApiError::forbidden();
    }
    co_return role;
}

drogon::Task<int64_t> RoomService::requireMember(int64_t memberId, int64_t roomId)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT r.last_seq FROM rooms r "
        "JOIN room_members m ON m.room_id = r.id "
        "WHERE r.id = ? AND m.member_id = ?",
        roomId,
        memberId);
    if (rows.empty())
    {
        throw ApiError::roomNotFound();
    }
    co_return rows[0]["last_seq"].as<int64_t>();
}
}  // namespace chat
