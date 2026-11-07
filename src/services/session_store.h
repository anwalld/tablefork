#pragma once

#include <cstdint>
#include "buffering/request_context.h"
#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>
#include <drogon/utils/coroutine.h>
#include <optional>
#include <string>
#include <vector>

namespace chat
{
struct Session
{
    int64_t userId;
    int64_t memberId;
    std::string username;
    // The device the session was created on (SHA-256 of its device token)
    // and its kind; empty for sessions from before devices were recorded.
    std::string device{};
    std::string deviceKind{};
    RequestContextPtr request{}; // not serialized into a session
};

inline constexpr const char *kSessionCookie = "chat_session";

// Redis-backed sessions. Only the SHA-256 of a token is stored. Redis failures
// surface as ApiError::unavailable(), never as "not logged in".
class SessionStore
{
  public:
    static drogon::Task<std::string> create(const Session &session);
    static drogon::Task<std::optional<Session>> find(const std::string &token, RequestContextPtr context = {});
    static drogon::Task<> remove(const std::string &token, RequestContextPtr context = {});

    // The Redis key of a token's session; connections are tracked by it.
    static std::string keyFor(const std::string &token);

    // Ends the session behind token, or every session of its user when
    // everywhere is set, and announces each ended session key on the
    // sessions:ended channel so that every instance drops its connections.
    static drogon::Task<std::vector<std::string>> end(const Session &session,
                                                      const std::string &token,
                                                      bool everywhere);

    // Ends and announces every session of a user, keyed by their id alone.
    static drogon::Task<> endAllForUser(int64_t userId, RequestContextPtr context = {});

    // Restarts the session's lifetime without changing its token. False when
    // the session has already ended.
    static drogon::Task<bool> extend(const Session &session, const std::string &token);

    static constexpr const char *kEndedChannel = "sessions:ended";

    // The session behind the request's cookie, or ApiError::unauthenticated().
    static drogon::Task<Session> require(const drogon::HttpRequestPtr &req);

    static void setCookie(const drogon::HttpResponsePtr &resp,
                          const std::string &token);
    static void clearCookie(const drogon::HttpResponsePtr &resp);
};
}  // namespace chat
