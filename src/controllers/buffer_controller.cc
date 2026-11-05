#include "controllers/buffer_controller.h"
#include "buffering/request_buffer.h"
namespace chat {
drogon::Task<drogon::HttpResponsePtr> BufferController::query(drogon::HttpRequestPtr req,std::string id)
{ co_return co_await RequestBuffer::instance().query(std::move(req),std::move(id)); }
drogon::Task<drogon::HttpResponsePtr> BufferController::control(drogon::HttpRequestPtr req,std::string action)
{ co_return co_await RequestBuffer::instance().control(std::move(req),std::move(action)); }
}
