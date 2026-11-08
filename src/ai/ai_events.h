#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>

#include "services/message_service.h"

namespace chat
{
// Tell the AI service what happened in a room. Each call reads the room's
// current members for the snapshot. Failures are logged, never thrown: the
// chat works without the AI service.
drogon::Task<> reportMessage(StoredMessage message, RequestContextPtr context = {});
drogon::Task<> reportMembersChanged(int64_t roomId, RequestContextPtr context = {});
drogon::Task<> reportControlChanged(int64_t roomId, RequestContextPtr context = {});
drogon::Task<> reportConfirmationInput(int64_t roomId, int64_t memberId,
                                       const std::string &response, bool privileged);
drogon::Task<> reportCouncilStart(int64_t roomId, const std::string &topic, int64_t proposerMemberId);
drogon::Task<> reportCouncilOverride(int64_t roomId, const std::string &verdict,
                                     int64_t memberId, bool privileged);
void reportBreakoutClosed(int64_t roomId);
void reportRoomDeleted(int64_t roomId);
void reportGenerationRejected(int64_t roomId, const std::string &generationId);
}  // namespace chat
