#include <drogon/drogon_test.h>

#include "common/errors.h"
#include "services/password_policy.h"

using chat::ApiError;
using chat::PasswordPolicy;

namespace
{
std::string rejection(const PasswordPolicy &policy,
                      const std::string &username,
                      const std::string &password)
{
    try
    {
        policy.check(username, password);
        return "";
    }
    catch (const ApiError &e)
    {
        return e.code();
    }
}
}  // namespace

DROGON_TEST(PolicyRejectsBlocklistedPasswordsIgnoringCase)
{
    const PasswordPolicy policy({"password123"});
    CHECK(rejection(policy, "alice", "password123") == "weak_password");
    CHECK(rejection(policy, "alice", "PassWord123") == "weak_password");
    CHECK(rejection(policy, "alice", "password1234").empty());
}

DROGON_TEST(PolicyRejectsTheUsernameIgnoringCase)
{
    const PasswordPolicy policy({});
    CHECK(rejection(policy, "alice_long", "alice_long") == "weak_password");
    CHECK(rejection(policy, "Alice_Long", "aLICE_lONG") == "weak_password");
    CHECK(rejection(policy, "alice_long", "alice_long!").empty());
}

DROGON_TEST(PolicyLoadsTheBundledList)
{
    const auto policy = PasswordPolicy::fromFile("data/common-passwords.txt");
    CHECK(rejection(policy, "alice", "qwertyuiop") == "weak_password");
    CHECK(rejection(policy, "alice", "1234567890") == "weak_password");
    CHECK(rejection(policy, "alice", "correct horse battery").empty());
}

DROGON_TEST(PolicyFailsOnAMissingFile)
{
    CHECK_THROWS(PasswordPolicy::fromFile("data/no-such-file.txt"));
}
