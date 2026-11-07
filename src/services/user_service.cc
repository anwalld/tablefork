#include "services/user_service.h"

#include <cstring>
#include <drogon/drogon.h>

#include "common/util.h"
#include "buffering/request_memo.h"
#include "services/password.h"
#include "services/password_policy.h"
#include "services/session_store.h"

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

// The same reply whether the username, the question or the answer is wrong, so
// probing cannot learn which account exists or what its question is.
ApiError invalidAnswer()
{
    return {drogon::k401Unauthorized,
            "invalid_answer",
            "wrong username or security answer"};
}

ApiError usernameTaken()
{
    return {drogon::k409Conflict, "username_taken", "username already taken"};
}

ApiError passwordMismatch()
{
    return {drogon::k400BadRequest, "password_mismatch", "passwords do not match"};
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
    if (!passwordLength || *passwordLength < 10 || password.size() > 1024)
    {
        throw ApiError::invalidArgument(
            "password must be at least 10 characters");
    }
    PasswordPolicy::instance().check(username, password);
}

void validateQuestion(const std::string &question)
{
    if (question.empty() || !isPrintableText(question) || question.size() > 255)
    {
        throw ApiError::invalidArgument(
            "security question must be 1 to 255 printable characters");
    }
}

void validateAnswer(const std::string &answer)
{
    if (answer.empty() || !isPrintableText(answer) || answer.size() > 255)
    {
        throw ApiError::invalidArgument(
            "security answer must be 1 to 255 printable characters");
    }
}
}  // namespace

drogon::Task<> UserService::registerUser(std::string username,
                                         std::string password,
                                         std::string securityQuestion,
                                         std::string securityAnswer,
                                         std::string passwordConfirm,
                                         RequestContextPtr context)
{
    if (!passwordConfirm.empty() && passwordConfirm != password)
    {
        throw passwordMismatch();
    }
    // The pair is optional for legacy callers, but never half-supplied.
    if (securityQuestion.empty() != securityAnswer.empty())
    {
        throw ApiError::invalidArgument(
            "security question and answer must both be provided");
    }
    validate(username, password);
    if (!securityQuestion.empty())
    {
        validateQuestion(securityQuestion);
        validateAnswer(securityAnswer);
    }
    auto db = drogon::app().getDbClient();
    const auto hash = co_await hashPassword(password);
    const auto answerHash = securityAnswer.empty()
                                ? std::optional<std::string>{}
                                : std::optional<std::string>{co_await hashPassword(securityAnswer)};
    try
    {
        auto tx = co_await newGuardedTransaction(context);
        const auto actor = context ? "anonymous:" + context->anonymousOwner : "legacy";
        if (auto prior = co_await RequestMemo::begin(tx, context, actor, "auth.register"))
            co_return;
        const auto existing = co_await tx->execSqlCoro("SELECT id FROM users WHERE username = ?", username);
        if (!existing.empty()) throw usernameTaken();
        const std::optional<std::string> questionParam =
            securityQuestion.empty() ? std::nullopt : std::optional<std::string>(securityQuestion);
        const auto user = co_await tx->execSqlCoro(
            "INSERT INTO users (username, password_hash, security_question, security_answer_hash) "
            "VALUES (?, ?, ?, ?)",
            username,
            hash,
            questionParam,
            answerHash);
        co_await tx->execSqlCoro(
            "INSERT INTO members (type, user_id, display_name) "
            "VALUES ('human', ?, ?)",
            static_cast<int64_t>(user.insertId()),
            username);
        co_await RequestMemo::finish(tx, context, actor, Json::objectValue);
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

drogon::Task<std::optional<std::string>> UserService::securityQuestion(
    std::string username)
{
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT security_question FROM users WHERE username = ?", username);
    if (rows.empty() || rows[0]["security_question"].isNull())
    {
        co_return std::nullopt;
    }
    co_return rows[0]["security_question"].as<std::string>();
}

drogon::Task<> UserService::resetPassword(std::string username,
                                          std::string answer,
                                          std::string password,
                                          std::string passwordConfirm,
                                          RequestContextPtr context)
{
    if (!passwordConfirm.empty() && passwordConfirm != password)
    {
        throw passwordMismatch();
    }
    const auto rows = co_await drogon::app().getDbClient()->execSqlCoro(
        "SELECT id, security_answer_hash FROM users WHERE username = ?", username);
    if (rows.empty() || rows[0]["security_answer_hash"].isNull())
    {
        // Same cost and outcome as a wrong answer.
        co_await burnPasswordCheck(answer);
        throw invalidAnswer();
    }
    const auto userId = rows[0]["id"].as<int64_t>();
    if (!co_await verifyPassword(answer, rows[0]["security_answer_hash"].as<std::string>()))
    {
        throw invalidAnswer();
    }
    validate(username, password);
    const auto hash = co_await hashPassword(password);

    auto tx = co_await newGuardedTransaction(context);
    co_await tx->execSqlCoro("UPDATE users SET password_hash = ? WHERE id = ?", hash, userId);
    co_await commit(std::move(tx));

    co_await SessionStore::endAllForUser(userId, context);
}
}  // namespace chat