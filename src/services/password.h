#pragma once

#include <drogon/utils/coroutine.h>
#include <string>

namespace chat
{
// scrypt is slow on purpose, so both calls run on dedicated threads to keep the
// IO threads responsive.
drogon::Task<std::string> hashPassword(std::string password);
drogon::Task<bool> verifyPassword(std::string password, std::string stored);
// Spends the same time as a failed verify, for logins with an unknown user.
drogon::Task<> burnPasswordCheck(std::string password);
}  // namespace chat
