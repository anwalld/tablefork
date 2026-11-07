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
void reportRoomDeleted(int64_t roomId);
void reportGenerationRejected(int64_t roomId, const std::string &generationId);
}  // namespace chat
