#include "controllers/auth_controller.h"

#include "common/config.h"
#include "common/util.h"
#include "services/device_store.h"
#include "services/device_trust.h"
#include "services/login_limiter.h"
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
    const auto username = stringField(body, "username");
    const auto password = stringField(body, "password");

    // A browser that has logged in as this user before counts its attempts
    // apart from everyone else, so others guessing the name cannot lock it
    // out.
    const auto device = req->getCookie(kDeviceCookie);
    const bool knownDevice = co_await DeviceStore::owner(device) == username;
    const auto limitKey = knownDevice ? LoginLimiter::deviceKey(device)
                                      : LoginLimiter::nameKey(username);
    co_await LoginLimiter::admit(limitKey);

    auto session = co_await UserService::authenticate(username, password);
    co_await LoginLimiter::clear(limitKey);
    // The session remembers its device, so that messages sent over the
    // WebSocket, which never sees the device cookie, can be counted for it.
    const auto deviceToken = knownDevice ? device : co_await DeviceStore::issue(username);
    session.device = drogon::utils::getSha256(deviceToken);
    session.deviceKind = deviceKind(req->getHeader("user-agent"));
    const auto token = co_await SessionStore::create(session);
    co_await DeviceTrust::evaluate(session.userId, session.device, session.deviceKind);
    auto resp = jsonResponse(userJson(session));
    SessionStore::setCookie(resp, token);
    if (knownDevice)
    {
        co_await DeviceStore::refresh(device);
    }
    else
    {
        DeviceStore::setCookie(resp, deviceToken);
    }
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::logout(
    drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    // Logging out on a trusted device logs the user out everywhere.
    const bool trusted =
        co_await DeviceTrust::isTrusted(session.userId, session.device, session.deviceKind);
    co_await SessionStore::end(session, req->getCookie(kSessionCookie), trusted);
    auto resp = jsonResponse(Json::objectValue);
    SessionStore::clearCookie(resp);
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::me(
    drogon::HttpRequestPtr req)
{
    co_return jsonResponse(userJson(co_await SessionStore::require(req)));
}

drogon::Task<drogon::HttpResponsePtr> AuthController::refresh(
    drogon::HttpRequestPtr req)
{
    const auto session = co_await SessionStore::require(req);
    // Refreshing is also a chance for a well-used device to become trusted.
    if (!co_await DeviceTrust::evaluate(session.userId, session.device, session.deviceKind))
    {
        throw ApiError(drogon::k403Forbidden,
                       "device_required",
                       "only a trusted device can refresh its session");
    }
    const auto device = req->getCookie(kDeviceCookie);
    const auto token = req->getCookie(kSessionCookie);
    if (!co_await SessionStore::extend(session, token))
    {
        throw ApiError::unauthenticated();
    }
    if (co_await DeviceStore::owner(device) == session.username)
    {
        co_await DeviceStore::refresh(device);
    }
    Json::Value body;
    body["expires_in"] = Config::get().sessionTtlSeconds;
    auto resp = jsonResponse(body);
    SessionStore::setCookie(resp, token);
    co_return resp;
}
}  // namespace chat
