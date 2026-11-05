#pragma once
#include "buffering/request_context.h"

#include <drogon/utils/coroutine.h>
#include <string>
#include <vector>

namespace chat
{
// Waits, in seconds, after each attempt past the free ones: the first entry
// applies once the free attempts are used up, and the last one repeats.
std::vector<int> backoffSchedule(int baseSeconds, int maxSeconds);

// Delays repeated login attempts per user name, or per device that has logged
// in before. The count lives in Redis, so it holds across instances and does
// not depend on the client's address.
class LoginLimiter
{
  public:
    static std::string nameKey(const std::string &username);
    static std::string deviceKey(const std::string &deviceToken);

    // Counts an attempt before the password is checked, so that concurrent
    // attempts cannot all slip through. Throws ApiError::tooManyAttempts()
    // while the key must wait.
    static drogon::Task<> admit(std::string key, RequestContextPtr context = {});

    // Forgets the attempts after a successful login.
    static drogon::Task<> clear(std::string key, RequestContextPtr context = {});
};
}  // namespace chat
