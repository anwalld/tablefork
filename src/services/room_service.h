#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <string>

#include "services/session_store.h"

namespace chat
{
class RoomService
{
  public:
    static drogon::Task<Json::Value> create(Session session, std::string name);
    static drogon::Task<Json::Value> list(Session session,
                                          int64_t limit,
                                          int64_t offset);
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
    // The room's last_seq, or ApiError::roomNotFound() for a non-member.
    static drogon::Task<int64_t> requireMember(int64_t memberId, int64_t roomId);
};
}  // namespace chat
