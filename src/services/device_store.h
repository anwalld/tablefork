#pragma once
#include "buffering/request_context.h"

#include <drogon/HttpResponse.h>
#include <drogon/utils/coroutine.h>
#include <optional>
#include <string>

namespace chat
{
inline constexpr const char *kDeviceCookie = "chat_device";

// Long-lived tokens for browsers that have logged in before. A login from such
// a browser counts its attempts apart from everyone else trying the same name.
// Redis stores only the SHA-256 of a token, mapped to the user name.
class DeviceStore
{
  public:
    // The user name the token was issued to, if it is still valid.
    static drogon::Task<std::optional<std::string>> owner(std::string token, RequestContextPtr context = {});

    // A new token for username.
    static drogon::Task<std::string> issue(std::string username, RequestContextPtr context = {});

    // Restarts the token's lifetime.
    static drogon::Task<> refresh(std::string token, RequestContextPtr context = {});

    static void setCookie(const drogon::HttpResponsePtr &resp,
                          const std::string &token);
};
}  // namespace chat
