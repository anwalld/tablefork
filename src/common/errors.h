#pragma once

#include <drogon/HttpTypes.h>
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

  private:
    drogon::HttpStatusCode status_;
    std::string code_;
    bool retryable_;
};
}  // namespace chat
