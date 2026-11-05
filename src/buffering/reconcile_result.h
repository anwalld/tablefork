#pragma once
#include "buffering/request_context.h"
#include <cstdint>
#include <drogon/utils/coroutine.h>
namespace chat {
// Restoring a committed SQL receipt must reconcile today's state, not repeat
// an old kick/leave notification against a member who has since rejoined.
drogon::Task<> reconcileResult(int64_t roomId, RequestContextPtr context);
}
