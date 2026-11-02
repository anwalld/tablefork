#pragma once

#include <drogon/utils/coroutine.h>
#include <string>

#include "services/session_store.h"

namespace chat
{
class UserService
{
  public:
    static drogon::Task<> registerUser(std::string username,
                                       std::string password);
    // Throws an ApiError that does not reveal whether the username exists.
    static drogon::Task<Session> authenticate(std::string username,
                                              std::string password);
};
}  // namespace chat
