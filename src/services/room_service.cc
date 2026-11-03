#include "services/room_service.h"

#include <algorithm>
#include <drogon/drogon.h>
#include <optional>

#include "common/util.h"

namespace chat
{
namespace
{
using TransactionPtr = std::shared_ptr<drogon::orm::Transaction>;

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
}  // namespace

drogon::Task<Json::Value> RoomService::create(Session session, std::string name)
{
    const auto length = utf8Length(name);
    if (!isPrintableText(name) || *length < 1 || *length > 64)
    {
        throw ApiError::invalidArgument(
            "name must be 1 to 64 printable characters");
    }
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    const auto room =
        co_await tx->execSqlCoro("INSERT INTO rooms (name) VALUES (?)", name);
    const auto roomId = static_cast<int64_t>(room.insertId());
    co_await tx->execSqlCoro(
        "INSERT INTO room_members (room_id, member_id, role) "
        "VALUES (?, ?, 'owner')",
        roomId,
        session.memberId);
    co_await commit(std::move(tx));

    Json::Value body;
    body["id"] = Json::Int64(roomId);
    body["name"] = name;
    body["member_count"] = 1;
    body["status"] = "joined";
    body["last_seq"] = 0;
    co_return body;
}

drogon::Task<Json::Value> RoomService::list(Session session,
                                            int64_t limit,
                                            int64_t offset)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT r.id, r.name, r.last_seq, m.member_id, "
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
        rooms.append(std::move(room));
    }
    Json::Value body;
    body["rooms"] = std::move(rooms);
    co_return body;
}

drogon::Task<> RoomService::invite(Session session,
                                   int64_t roomId,
                                   std::string username)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
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
        co_await tx->execSqlCoro(
            "INSERT IGNORE INTO room_invites (room_id, user_id, invited_by) "
            "VALUES (?, ?, ?)",
            roomId,
            userId,
            session.memberId);
    }
    co_await commit(std::move(tx));
}

drogon::Task<> RoomService::join(Session session, int64_t roomId)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
    co_await lockRoom(tx, roomId);
    if (!co_await isMember(tx, roomId, session.memberId))
    {
        const auto invite = co_await tx->execSqlCoro(
            "DELETE FROM room_invites WHERE room_id = ? AND user_id = ?",
            roomId,
            session.userId);
        if (invite.affectedRows() == 0)
        {
            throw ApiError::roomNotFound();
        }
        co_await tx->execSqlCoro(
            "INSERT INTO room_members (room_id, member_id) VALUES (?, ?)",
            roomId,
            session.memberId);
    }
    co_await commit(std::move(tx));
}

drogon::Task<bool> RoomService::leave(Session session,
                                      int64_t roomId,
                                      bool confirmDelete)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
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
        co_await tx->execSqlCoro(
            "DELETE FROM room_members WHERE room_id = ? AND member_id = ?",
            roomId,
            session.memberId);
    }
    co_await commit(std::move(tx));
    co_return deleted;
}

drogon::Task<Json::Value> RoomService::members(Session session, int64_t roomId)
{
    co_await requireMember(session.memberId, roomId);
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT m.id, m.type, m.display_name, rm.role, p.slug, p.provider "
        "FROM room_members rm "
        "JOIN members m ON m.id = rm.member_id "
        "LEFT JOIN ai_profiles p ON p.member_id = m.id "
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
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
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
    co_await commit(std::move(tx));
    co_return target;
}

drogon::Task<int64_t> RoomService::setAdmin(Session session,
                                            int64_t roomId,
                                            std::string username,
                                            bool admin)
{
    auto tx = co_await drogon::app().getDbClient()->newTransactionCoro();
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
    co_await commit(std::move(tx));
    co_return target;
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
