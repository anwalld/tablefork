#pragma once

#include <drogon/HttpTypes.h>
#include <optional>
#include <stdexcept>
#include <string>

namespace chat
{
// An error the API reports to the client as is.
class ApiError : public std::runtime_error
{
  public:
    ApiError(drogon::HttpStatusCode status,
             std::string code,
             const std::string &message,
             bool retryable = false)
        : std::runtime_error(message),
          status_(status),
          code_(std::move(code)),
          retryable_(retryable)
    {
    }

    drogon::HttpStatusCode status() const
    {
        return status_;
    }

    const std::string &code() const
    {
        return code_;
    }

    bool retryable() const
    {
        return retryable_;
    }

    // Seconds the client should wait before retrying, when known.
    std::optional<int> retryAfter() const
    {
        return retryAfter_;
    }

    // The model provider an error is about, when there is one.
    const std::optional<std::string> &provider() const
    {
        return provider_;
    }

    static ApiError apiKeyRequired(const std::string &provider)
    {
        ApiError error{drogon::k400BadRequest,
                       "api_key_required",
                       "add an API key for " + provider + " first"};
        error.provider_ = provider;
        return error;
    }

    static ApiError invalidArgument(const std::string &message)
    {
        return {drogon::k400BadRequest, "invalid_argument", message};
    }

    static ApiError unauthenticated()
    {
        return {drogon::k401Unauthorized, "unauthenticated", "login required"};
    }

    // Also used for rooms the caller may not see, so that their existence
    // does not leak.
    static ApiError roomNotFound()
    {
        return {drogon::k404NotFound, "room_not_found", "room not found"};
    }

    static ApiError forbidden()
    {
        return {drogon::k403Forbidden,
                "forbidden",
                "only the room owner or an admin may do this"};
    }

    static ApiError unavailable()
    {
        return {drogon::k503ServiceUnavailable,
                "unavailable",
                "service temporarily unavailable",
                true};
    }

    static ApiError busy()
    {
        return {drogon::k503ServiceUnavailable,
                "busy",
                "too many messages, retry later",
                true};
    }

    static ApiError tooManyAttempts(int retryAfterSeconds)
    {
        ApiError error{drogon::k429TooManyRequests,
                       "too_many_attempts",
                       "too many login attempts, retry later",
                       true};
        error.retryAfter_ = retryAfterSeconds;
        return error;
    }

    static ApiError rateLimited(int retryAfterSeconds)
    {
        ApiError error{drogon::k429TooManyRequests,
                       "rate_limited",
                       "sending too fast, retry later",
                       true};
        error.retryAfter_ = retryAfterSeconds;
        return error;
    }

    static ApiError weakPassword(const std::string &message)
    {
        return {drogon::k400BadRequest, "weak_password", message};
    }

  private:
    drogon::HttpStatusCode status_;
    std::string code_;
    bool retryable_;
    std::optional<int> retryAfter_;
    std::optional<std::string> provider_;
};
}  // namespace chat
