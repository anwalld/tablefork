#include "controllers/health_controller.h"

#include <drogon/drogon.h>

namespace chat
{
drogon::Task<drogon::HttpResponsePtr> HealthController::check(
    drogon::HttpRequestPtr)
{
    Json::Value body;
    bool healthy = true;
    try
    {
        co_await drogon::app().getDbClient()->execSqlCoro("SELECT 1");
        body["mysql"] = "ok";
    }
    catch (const drogon::orm::DrogonDbException &e)
    {
        LOG_ERROR << "health: mysql: " << e.base().what();
        body["mysql"] = "unavailable";
        healthy = false;
    }
    try
    {
        co_await drogon::app().getRedisClient()->execCommandCoro("PING");
        body["redis"] = "ok";
    }
    catch (const std::exception &e)
    {
        LOG_ERROR << "health: redis: " << e.what();
        body["redis"] = "unavailable";
        healthy = false;
    }
    auto resp = drogon::HttpResponse::newHttpJsonResponse(body);
    resp->setStatusCode(healthy ? drogon::k200OK
                                : drogon::k503ServiceUnavailable);
    co_return resp;
}
}  // namespace chat
