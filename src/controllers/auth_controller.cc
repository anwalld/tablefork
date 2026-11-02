#include "controllers/auth_controller.h"

#include "common/util.h"
#include "services/session_store.h"
#include "services/user_service.h"

namespace chat
{
namespace
{
Json::Value userJson(const Session &session)
{
    Json::Value body;
    body["id"] = Json::Int64(session.userId);
    body["member_id"] = Json::Int64(session.memberId);
    body["username"] = session.username;
    return body;
}
}  // namespace

drogon::Task<drogon::HttpResponsePtr> AuthController::registerUser(
    drogon::HttpRequestPtr req)
{
    const auto &body = jsonBody(req);
    co_await UserService::registerUser(stringField(body, "username"),
                                       stringField(body, "password"));
    co_return jsonResponse(Json::objectValue, drogon::k201Created);
}

drogon::Task<drogon::HttpResponsePtr> AuthController::login(
    drogon::HttpRequestPtr req)
{
    const auto &body = jsonBody(req);
    const auto session = co_await UserService::authenticate(
        stringField(body, "username"), stringField(body, "password"));
    const auto token = co_await SessionStore::create(session);
    auto resp = jsonResponse(userJson(session));
    SessionStore::setCookie(resp, token);
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::logout(
    drogon::HttpRequestPtr req)
{
    co_await SessionStore::require(req);
    co_await SessionStore::remove(req->getCookie(kSessionCookie));
    auto resp = jsonResponse(Json::objectValue);
    SessionStore::clearCookie(resp);
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::me(
    drogon::HttpRequestPtr req)
{
    co_return jsonResponse(userJson(co_await SessionStore::require(req)));
}
}  // namespace chat
