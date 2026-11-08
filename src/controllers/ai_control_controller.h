#pragma once
#include <drogon/HttpController.h>

namespace chat
{
class AiControlController : public drogon::HttpController<AiControlController>
{
  public:
    METHOD_LIST_BEGIN
    ADD_METHOD_TO(AiControlController::state, "/api/rooms/{1}/ai-state", drogon::Get);
    ADD_METHOD_TO(AiControlController::pause, "/api/rooms/{1}/ai-state", drogon::Put);
    ADD_METHOD_TO(AiControlController::stop, "/api/rooms/{1}/ai-generations/{2}/stop", drogon::Post);
    ADD_METHOD_TO(AiControlController::collaboration, "/api/rooms/{1}/collaboration", drogon::Put);
    ADD_METHOD_TO(AiControlController::council, "/api/rooms/{1}/council", drogon::Put);
    METHOD_LIST_END
    using Response = drogon::Task<drogon::HttpResponsePtr>;
    Response state(drogon::HttpRequestPtr req, std::string room);
    Response pause(drogon::HttpRequestPtr req, std::string room);
    Response stop(drogon::HttpRequestPtr req, std::string room, std::string generation);
    Response collaboration(drogon::HttpRequestPtr req, std::string room);
    Response council(drogon::HttpRequestPtr req, std::string room);
};
}
