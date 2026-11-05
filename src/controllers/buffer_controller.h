#pragma once
#include <drogon/HttpController.h>
namespace chat {
class BufferController : public drogon::HttpController<BufferController>
{
 public:
  METHOD_LIST_BEGIN
  ADD_METHOD_TO(BufferController::query, "/api/tasks/{1}", drogon::Get);
  ADD_METHOD_TO(BufferController::control, "/internal/buffer/{1}", drogon::Post);
  METHOD_LIST_END
  drogon::Task<drogon::HttpResponsePtr> query(drogon::HttpRequestPtr req,std::string id);
  drogon::Task<drogon::HttpResponsePtr> control(drogon::HttpRequestPtr req,std::string action);
};
}
