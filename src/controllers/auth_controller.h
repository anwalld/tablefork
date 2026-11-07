#pragma once

#include <drogon/HttpController.h>

namespace chat
{
class AuthController : public drogon::HttpController<AuthController>
{
  public:
    METHOD_LIST_BEGIN
    ADD_METHOD_TO(AuthController::registerUser, "/api/auth/register", drogon::Post);
    ADD_METHOD_TO(AuthController::login, "/api/auth/login", drogon::Post);
    ADD_METHOD_TO(AuthController::logout, "/api/auth/logout", drogon::Post);
    ADD_METHOD_TO(AuthController::me, "/api/auth/me", drogon::Get);
    ADD_METHOD_TO(AuthController::refresh, "/api/auth/refresh", drogon::Post);
    ADD_METHOD_TO(AuthController::passwordRecoveryQuestion, "/api/auth/password-recovery/question", drogon::Post);
    ADD_METHOD_TO(AuthController::passwordRecoveryReset, "/api/auth/password-recovery/reset", drogon::Post);
    METHOD_LIST_END

    drogon::Task<drogon::HttpResponsePtr> registerUser(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> login(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> logout(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> me(drogon::HttpRequestPtr req);
    // Restarts the current session's lifetime, for browsers holding the
    // user's device token.
    drogon::Task<drogon::HttpResponsePtr> refresh(drogon::HttpRequestPtr req);
    // Returns the security question so the user can answer it. Missing and
    // unknown accounts report an empty question, revealing nothing.
    drogon::Task<drogon::HttpResponsePtr> passwordRecoveryQuestion(drogon::HttpRequestPtr req);
    // Verifies the security answer and installs a new password.
    drogon::Task<drogon::HttpResponsePtr> passwordRecoveryReset(drogon::HttpRequestPtr req);
};
}  // namespace chat
