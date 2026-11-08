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
    ADD_METHOD_TO(AiControlController::roomMemories, "/api/rooms/{1}/memories", drogon::Get);
    ADD_METHOD_TO(AiControlController::deleteRoomMemory, "/api/rooms/{1}/memories/{2}", drogon::Delete);
    ADD_METHOD_TO(AiControlController::myMemories, "/api/me/memories", drogon::Get);
    ADD_METHOD_TO(AiControlController::deleteMyMemory, "/api/me/memories/{1}", drogon::Delete);
    ADD_METHOD_TO(AiControlController::importKnowledge, "/api/rooms/{1}/knowledge", drogon::Post);
    ADD_METHOD_TO(AiControlController::listKnowledge, "/api/rooms/{1}/knowledge", drogon::Get);
    ADD_METHOD_TO(AiControlController::deleteKnowledge, "/api/rooms/{1}/knowledge/{2}", drogon::Delete);
    METHOD_LIST_END
    using Response = drogon::Task<drogon::HttpResponsePtr>;
    Response state(drogon::HttpRequestPtr req, std::string room);
    Response pause(drogon::HttpRequestPtr req, std::string room);
    Response stop(drogon::HttpRequestPtr req, std::string room, std::string generation);
    Response collaboration(drogon::HttpRequestPtr req, std::string room);
    Response council(drogon::HttpRequestPtr req, std::string room);
    // Room memories: any member who can read the room may list them; only
    // owners and admins may delete one. User memories: each user lists and
    // deletes their own. Deleted memories are never injected again, since the
    // AI service reads memories from SQL for every reply.
    Response roomMemories(drogon::HttpRequestPtr req, std::string room);
    Response deleteRoomMemory(drogon::HttpRequestPtr req, std::string room, std::string memory);
    Response myMemories(drogon::HttpRequestPtr req);
    Response deleteMyMemory(drogon::HttpRequestPtr req, std::string memory);
    // Knowledge base: members import .txt/.md/.log text (JSON {name, content});
    // the AI service chunks and embeds it. Readers of the room see the list;
    // the uploader, owners and admins may delete a document.
    Response importKnowledge(drogon::HttpRequestPtr req, std::string room);
    Response listKnowledge(drogon::HttpRequestPtr req, std::string room);
    Response deleteKnowledge(drogon::HttpRequestPtr req, std::string room, std::string document);
};
}
