#pragma once

#include <drogon/HttpController.h>

namespace chat
{
class HealthController : public drogon::HttpController<HealthController>
{
  public:
    METHOD_LIST_BEGIN
    ADD_METHOD_TO(HealthController::check, "/health", drogon::Get);
    METHOD_LIST_END

    drogon::Task<drogon::HttpResponsePtr> check(drogon::HttpRequestPtr req);
};
}  // namespace chat
