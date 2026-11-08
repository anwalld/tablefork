#pragma once

#include <drogon/HttpController.h>
#include <string>

namespace chat
{
class AiController : public drogon::HttpController<AiController>
{
  public:
    METHOD_LIST_BEGIN
    ADD_METHOD_TO(AiController::models, "/api/ai/models", drogon::Get);
    ADD_METHOD_TO(AiController::catalog, "/api/ai/roles", drogon::Get);
    ADD_METHOD_TO(AiController::createRole, "/api/ai/roles", drogon::Post);
    ADD_METHOD_TO(AiController::editPersona, "/api/rooms/{1}/ais/{2}/persona", drogon::Put);
    ADD_METHOD_TO(AiController::catalog, "/api/ai-agents", drogon::Get);
    ADD_METHOD_TO(AiController::request, "/api/rooms/{1}/ai-members", drogon::Post);
    ADD_METHOD_TO(AiController::remove, "/api/rooms/{1}/ai-members/{2}", drogon::Delete);
    ADD_METHOD_TO(AiController::pending, "/api/rooms/{1}/ai-requests", drogon::Get);
    ADD_METHOD_TO(AiController::approve, "/api/rooms/{1}/ai-requests/{2}/approve", drogon::Post);
    ADD_METHOD_TO(AiController::reject, "/api/rooms/{1}/ai-requests/{2}/reject", drogon::Post);
    ADD_METHOD_TO(AiController::keys, "/api/keys", drogon::Get);
    ADD_METHOD_TO(AiController::storeKey, "/api/keys/{1}", drogon::Put);
    ADD_METHOD_TO(AiController::deleteKey, "/api/keys/{1}", drogon::Delete);
    ADD_METHOD_TO(AiController::tableTemplates, "/api/table-templates", drogon::Get);
    ADD_METHOD_TO(AiController::launchTemplate, "/api/rooms/{1}/table-template", drogon::Post);
    METHOD_LIST_END

    using Response = drogon::Task<drogon::HttpResponsePtr>;

    Response catalog(drogon::HttpRequestPtr req);
    Response models(drogon::HttpRequestPtr req);
    Response createRole(drogon::HttpRequestPtr req);
    Response editPersona(drogon::HttpRequestPtr req,std::string roomId,std::string memberId);
    Response request(drogon::HttpRequestPtr req, std::string roomId);
    Response remove(drogon::HttpRequestPtr req, std::string roomId, std::string slug);
    Response pending(drogon::HttpRequestPtr req, std::string roomId);
    Response approve(drogon::HttpRequestPtr req, std::string roomId, std::string slug);
    Response reject(drogon::HttpRequestPtr req, std::string roomId, std::string slug);
    Response keys(drogon::HttpRequestPtr req);
    // The request body holds the key in the clear: it is passed on to the AI
    // service and never logged or stored here.
    Response storeKey(drogon::HttpRequestPtr req, std::string provider);
    Response deleteKey(drogon::HttpRequestPtr req, std::string provider);
    Response tableTemplates(drogon::HttpRequestPtr req);
    Response launchTemplate(drogon::HttpRequestPtr req, std::string roomId);
};
}  // namespace chat
