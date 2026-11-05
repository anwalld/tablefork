#pragma once
#include "buffering/request_context.h"

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <string>

namespace chat
{
// The Redis key counting a user's messages in the fixed window holding
// nowSeconds.
std::string messageRateKey(int64_t userId, int64_t nowSeconds, int windowSeconds);

// Seconds until the window holding nowSeconds ends, at least 1.
int secondsLeftInWindow(int64_t nowSeconds, int windowSeconds);

// Limits how many messages one user sends per window, across all connections
// and rooms. The count lives in Redis, so it holds across instances.
class MessageRateLimiter
{
  public:
    // Counts the message, or throws ApiError::rateLimited() when the user has
    // sent too many in this window.
    static drogon::Task<> admit(int64_t userId, RequestContextPtr context = {});
};
}  // namespace chat
