#pragma once

#include <cstdint>
#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>
#include <drogon/utils/coroutine.h>
#include <optional>
#include <string>

namespace chat
{
struct Session
{
    int64_t userId;
    int64_t memberId;
    std::string username;
};

inline constexpr const char *kSessionCookie = "chat_session";

// Redis-backed sessions. Only the SHA-256 of a token is stored. Redis failures
// surface as ApiError::unavailable(), never as "not logged in".
class SessionStore
{
  public:
    static drogon::Task<std::string> create(const Session &session);
    static drogon::Task<std::optional<Session>> find(const std::string &token);
    static drogon::Task<> remove(const std::string &token);

    // The session behind the request's cookie, or ApiError::unauthenticated().
    static drogon::Task<Session> require(const drogon::HttpRequestPtr &req);

    static void setCookie(const drogon::HttpResponsePtr &resp,
                          const std::string &token);
    static void clearCookie(const drogon::HttpResponsePtr &resp);
};
}  // namespace chat
