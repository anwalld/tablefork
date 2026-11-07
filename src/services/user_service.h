#pragma once

#include <drogon/utils/coroutine.h>
#include <string>

#include "services/session_store.h"

namespace chat
{
class UserService
{
  public:
    // securityQuestion/answer and passwordConfirm are optional so existing
    // callers keep working; passwordConfirm must equal password when present.
    static drogon::Task<> registerUser(std::string username,
                                       std::string password,
                                       std::string securityQuestion = {},
                                       std::string securityAnswer = {},
                                       std::string passwordConfirm = {},
                                       RequestContextPtr context = {});
    // Throws an ApiError that does not reveal whether the username exists.
    static drogon::Task<Session> authenticate(std::string username,
                                              std::string password);
    // The security question set at registration, or nullopt when the account
    // has none or does not exist, so the response never reveals which.
    static drogon::Task<std::optional<std::string>> securityQuestion(
        std::string username);
    // Verifies the security answer and installs a new password, ending every
    // session of the user. Throws a uniform error on a bad answer.
    static drogon::Task<> resetPassword(std::string username,
                                        std::string answer,
                                        std::string password,
                                        std::string passwordConfirm = {},
                                        RequestContextPtr context = {});
};
}  // namespace chat
