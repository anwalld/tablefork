#include "services/user_service.h"

#include <cstring>
#include <drogon/drogon.h>

#include "common/util.h"
#include "services/password.h"

namespace chat
{
namespace
{
ApiError invalidCredentials()
{
    return {drogon::k401Unauthorized,
            "invalid_credentials",
            "wrong username or password"};
}

ApiError usernameTaken()
{
    return {drogon::k409Conflict, "username_taken", "username already taken"};
}

void validate(const std::string &username, const std::string &password)
{
    const auto nameLength = utf8Length(username);
    if (!isPrintableText(username) || *nameLength < 3 || *nameLength > 32)
    {
        throw ApiError::invalidArgument(
            "username must be 3 to 32 printable characters");
    }
    const auto passwordLength = utf8Length(password);
    if (!passwordLength || *passwordLength < 8 || password.size() > 1024)
    {
        throw ApiError::invalidArgument("password must be at least 8 characters");
    }
}
}  // namespace

drogon::Task<> UserService::registerUser(std::string username,
                                         std::string password)
{
    validate(username, password);
    auto db = drogon::app().getDbClient();
    const auto existing = co_await db->execSqlCoro(
        "SELECT id FROM users WHERE username = ?", username);
    if (!existing.empty())
    {
        throw usernameTaken();
    }
    const auto hash = co_await hashPassword(password);
    try
    {
        auto tx = co_await db->newTransactionCoro();
        const auto user = co_await tx->execSqlCoro(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            username,
            hash);
        co_await tx->execSqlCoro(
            "INSERT INTO members (type, user_id, display_name) "
            "VALUES ('human', ?, ?)",
            static_cast<int64_t>(user.insertId()),
            username);
        co_await commit(std::move(tx));
    }
    catch (const drogon::orm::DrogonDbException &e)
    {
        if (std::strstr(e.base().what(), "Duplicate entry"))
        {
            throw usernameTaken();
        }
        throw;
    }
}

drogon::Task<Session> UserService::authenticate(std::string username,
                                                std::string password)
{
    // Accounts created before names were validated must not get a session,
    // since the name would be truncated on the way through Redis.
    if (!isPrintableText(username))
    {
        co_await burnPasswordCheck(password);
        throw invalidCredentials();
    }
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT u.id, u.password_hash, m.id AS member_id "
        "FROM users u JOIN members m ON m.user_id = u.id "
        "WHERE u.username = ?",
        username);
    if (rows.empty())
    {
        co_await burnPasswordCheck(password);
        throw invalidCredentials();
    }
    const auto &row = rows[0];
    if (!co_await verifyPassword(password,
                                 row["password_hash"].as<std::string>()))
    {
        throw invalidCredentials();
    }
    co_return Session{row["id"].as<int64_t>(),
                      row["member_id"].as<int64_t>(),
                      username};
}
}  // namespace chat
