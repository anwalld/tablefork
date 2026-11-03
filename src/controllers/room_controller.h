#pragma once

#include <drogon/HttpController.h>
#include <string>

namespace chat
{
class RoomController : public drogon::HttpController<RoomController>
{
  public:
    METHOD_LIST_BEGIN
    ADD_METHOD_TO(RoomController::create, "/api/rooms", drogon::Post);
    ADD_METHOD_TO(RoomController::list, "/api/rooms", drogon::Get);
    ADD_METHOD_TO(RoomController::invite, "/api/rooms/{1}/invites", drogon::Post);
    ADD_METHOD_TO(RoomController::join, "/api/rooms/{1}/join", drogon::Post);
    ADD_METHOD_TO(RoomController::leave, "/api/rooms/{1}/leave", drogon::Post);
    ADD_METHOD_TO(RoomController::members, "/api/rooms/{1}/members", drogon::Get);
    ADD_METHOD_TO(RoomController::messages, "/api/rooms/{1}/messages", drogon::Get);
    ADD_METHOD_TO(RoomController::transferOwner, "/api/rooms/{1}/owner", drogon::Post);
    ADD_METHOD_TO(RoomController::appointAdmin, "/api/rooms/{1}/admins/{2}", drogon::Put);
    ADD_METHOD_TO(RoomController::dismissAdmin, "/api/rooms/{1}/admins/{2}", drogon::Delete);
    METHOD_LIST_END

    using Response = drogon::Task<drogon::HttpResponsePtr>;

    // Room ids arrive as text so that a malformed one can be reported as a
    // missing room rather than failing in Drogon's parameter conversion.

    Response create(drogon::HttpRequestPtr req);
    Response list(drogon::HttpRequestPtr req);
    Response invite(drogon::HttpRequestPtr req, std::string roomId);
    Response join(drogon::HttpRequestPtr req, std::string roomId);
    Response leave(drogon::HttpRequestPtr req, std::string roomId);
    Response members(drogon::HttpRequestPtr req, std::string roomId);
    Response messages(drogon::HttpRequestPtr req, std::string roomId);
    Response transferOwner(drogon::HttpRequestPtr req, std::string roomId);
    Response appointAdmin(drogon::HttpRequestPtr req,
                          std::string roomId,
                          std::string username);
    Response dismissAdmin(drogon::HttpRequestPtr req,
                          std::string roomId,
                          std::string username);
};
}  // namespace chat
