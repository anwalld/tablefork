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
    METHOD_LIST_END

    drogon::Task<drogon::HttpResponsePtr> registerUser(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> login(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> logout(drogon::HttpRequestPtr req);
    drogon::Task<drogon::HttpResponsePtr> me(drogon::HttpRequestPtr req);
};
}  // namespace chat
