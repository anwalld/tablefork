#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <string>
#include <vector>

namespace chat
{
// "phone" for phones and tablets, "computer" otherwise, from a User-Agent.
std::string deviceKind(const std::string &userAgent);

// Whether a device that sent `candidate` messages should replace the trusted
// one that sent `current`.
bool shouldReplace(long long candidate, long long current);

// The last `days` UTC dates as YYYYMMDD, today first.
std::vector<std::string> recentDays(int days);

// Each user trusts at most one phone and one computer. Trusted devices log
// the user out everywhere and may extend their session. A device is the
// SHA-256 of its device token.
class DeviceTrust
{
  public:
    // Makes the device trusted if its kind has none yet, or if it sent more
    // than 1.5 times the messages of the trusted one in the last 30 days.
    // Returns whether it is trusted afterwards.
    static drogon::Task<bool> evaluate(int64_t userId, std::string device, std::string kind);

    static drogon::Task<bool> isTrusted(int64_t userId, std::string device, std::string kind);

    // Counts one message sent from the device. Failures are only logged.
    static drogon::Task<> countSend(std::string device);
};
}  // namespace chat
