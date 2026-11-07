#include "controllers/auth_controller.h"

#include "common/config.h"
#include "common/util.h"
#include "buffering/request_buffer.h"
#include "buffering/external_request.h"
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
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::registerUser")) co_return queued;
    const auto &body = jsonBody(req);
    co_await UserService::registerUser(stringField(body, "username"),
                                       stringField(body, "password"),
                                       optionalStringField(body, "security_question"),
                                       optionalStringField(body, "security_answer"),
                                       optionalStringField(body, "password_confirm"),
                                       requestContext(req));
    co_return jsonResponse(Json::objectValue, drogon::k201Created);
}

drogon::Task<drogon::HttpResponsePtr> AuthController::login(
    drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::login")) co_return queued;
    const auto &body = jsonBody(req);
    const auto username = stringField(body, "username");
    const auto password = stringField(body, "password");

    const auto context = requestContext(req);
    co_await beginExternalRequest(context, context ? "anonymous:" + context->anonymousOwner : "legacy", "auth.login");
    // A browser that has logged in as this user before counts its attempts
    // apart from everyone else, so others guessing the name cannot lock it
    // out.
    const auto device = req->getCookie(kDeviceCookie);
    const bool knownDevice = co_await DeviceStore::owner(device, requestContext(req)) == username;
    const auto limitKey = knownDevice ? LoginLimiter::deviceKey(device)
                                      : LoginLimiter::nameKey(username);
    co_await LoginLimiter::admit(limitKey, requestContext(req));

    auto session = co_await UserService::authenticate(username, password);
    session.request = requestContext(req);
    checkRequest(session.request);
    co_await LoginLimiter::clear(limitKey, requestContext(req));
    // The session remembers its device, so that messages sent over the
    // WebSocket, which never sees the device cookie, can be counted for it.
    const auto deviceToken = knownDevice ? device : co_await DeviceStore::issue(username, requestContext(req));
    session.device = drogon::utils::getSha256(deviceToken);
    session.deviceKind = deviceKind(req->getHeader("user-agent"));
    const auto token = co_await SessionStore::create(session);
    co_await DeviceTrust::evaluate(session.userId, session.device, session.deviceKind, session.request);
    auto resp = jsonResponse(userJson(session));
    SessionStore::setCookie(resp, token);
    if (knownDevice)
    {
        co_await DeviceStore::refresh(device, requestContext(req));
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
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::logout")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await beginExternalRequest(session.request, "user:" + std::to_string(session.userId), "auth.logout");
    // Logging out on a trusted device logs the user out everywhere.
    const bool trusted =
        co_await DeviceTrust::isTrusted(session.userId, session.device, session.deviceKind, session.request);
    co_await SessionStore::end(session, req->getCookie(kSessionCookie), trusted);
    auto resp = jsonResponse(Json::objectValue);
    SessionStore::clearCookie(resp);
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::me(
    drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::me")) co_return queued;
    co_return jsonResponse(userJson(co_await SessionStore::require(req)));
}

drogon::Task<drogon::HttpResponsePtr> AuthController::refresh(
    drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::refresh")) co_return queued;
    const auto session = co_await SessionStore::require(req);
    co_await beginExternalRequest(session.request, "user:" + std::to_string(session.userId), "auth.refresh");
    // Refreshing is also a chance for a well-used device to become trusted.
    if (!co_await DeviceTrust::evaluate(session.userId, session.device, session.deviceKind, session.request))
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
    if (co_await DeviceStore::owner(device, requestContext(req)) == session.username)
    {
        co_await DeviceStore::refresh(device, requestContext(req));
    }
    Json::Value body;
    body["expires_in"] = Config::get().sessionTtlSeconds;
    auto resp = jsonResponse(body);
    SessionStore::setCookie(resp, token);
    co_return resp;
}

drogon::Task<drogon::HttpResponsePtr> AuthController::passwordRecoveryQuestion(
    drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::passwordRecoveryQuestion")) co_return queued;
    const auto &body = jsonBody(req);
    const auto question = co_await UserService::securityQuestion(stringField(body, "username"));
    Json::Value result;
    result["question"] = question.value_or("");
    co_return jsonResponse(result);
}

drogon::Task<drogon::HttpResponsePtr> AuthController::passwordRecoveryReset(
    drogon::HttpRequestPtr req)
{
    if (auto queued = co_await RequestBuffer::instance().admit(req, "AuthController::passwordRecoveryReset")) co_return queued;
    const auto &body = jsonBody(req);
    const auto username = stringField(body, "username");
    const auto context = requestContext(req);
    // Answer attempts share the username's login-failure counter, so guessing
    // someone's answer is throttled exactly like guessing their password.
    co_await LoginLimiter::admit(LoginLimiter::nameKey(username), context);
    co_await UserService::resetPassword(username,
                                        stringField(body, "answer"),
                                        stringField(body, "password"),
                                        optionalStringField(body, "password_confirm"),
                                        context);
    co_await LoginLimiter::clear(LoginLimiter::nameKey(username), context);
    co_return jsonResponse(Json::objectValue);
}
}  // namespace chat
