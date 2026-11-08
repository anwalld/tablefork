#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <string>
#include <string_view>
#include <vector>

#include "services/session_store.h"

namespace chat
{
class RoomService
{
  public:
    static drogon::Task<Json::Value> create(Session session, std::string name);
    // Splits a breakout off `parentRoomId`. The caller and each member in
    // `memberIds` join; `memberIds` must name humans or AIs already in the
    // parent, and nesting is bounded. The breakout inherits the parent's
    // history only up to the fork point.
    static drogon::Task<Json::Value> createBreakout(Session session,
                                                    int64_t parentRoomId,
                                                    std::string topic,
                                                    std::vector<int64_t> memberIds);
    static drogon::Task<Json::Value> list(Session session,
                                          int64_t limit,
                                          int64_t offset);
    static drogon::Task<> markRead(Session session, int64_t roomId, int64_t seq);
    static drogon::Task<> invite(Session session,
                                 int64_t roomId,
                                 std::string username);
    static drogon::Task<> join(Session session, int64_t roomId);
    // Returns true when the room was deleted because no human member is left.
    // Deleting needs confirmDelete, since the client's view of who is left may
    // be out of date; without it the call fails and nothing changes.
    static drogon::Task<bool> leave(Session session,
                                    int64_t roomId,
                                    bool confirmDelete);
    static drogon::Task<Json::Value> members(Session session, int64_t roomId);

    // Makes another human member the owner; the caller, who must be the
    // owner, becomes an ordinary member. Returns the new owner's member id.
    static drogon::Task<int64_t> transferOwner(Session session,
                                               int64_t roomId,
                                               std::string username);
    // Appoints or dismisses an admin; only the owner may. Returns the
    // member's id.
    static drogon::Task<int64_t> setAdmin(Session session,
                                          int64_t roomId,
                                          std::string username,
                                          bool admin);
    // targetRole == "none" means a human who is outside the room.
    static bool canManage(std::string_view actorRole, std::string_view targetRole);
    static drogon::Task<int64_t> kick(Session session, int64_t roomId, std::string username);
    // Returns the removed member id, or zero if the user was not a member.
    static drogon::Task<int64_t> ban(Session session, int64_t roomId, std::string username);
    static drogon::Task<> unban(Session session, int64_t roomId, std::string username);
    static drogon::Task<Json::Value> bans(Session session, int64_t roomId);

    // The caller's role, or ApiError::roomNotFound() for a non-member and
    // ApiError::forbidden() when the role is not one of those allowed.
    static drogon::Task<std::string> requireRole(
        int64_t memberId,
        int64_t roomId,
        std::vector<std::string> allowed);
    // The room's last_seq, or ApiError::roomNotFound() for a non-member.
    static drogon::Task<int64_t> requireMember(int64_t memberId, int64_t roomId);
    // The room's last_seq, or ApiError::roomNotFound() when the member cannot
    // see the room: a caller sees a room they joined, or any breakout below a
    // room they can see, so a root room's members can read every child.
    static drogon::Task<int64_t> requireVisible(int64_t memberId, int64_t roomId);
};
}  // namespace chat
