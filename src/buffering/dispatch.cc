#include "controllers/ai_control_controller.h"
#include "buffering/request_buffer.h"
#include "common/util.h"
#include "controllers/auth_controller.h"
#include "controllers/room_controller.h"
#include "controllers/ai_controller.h"
namespace chat {
drogon::Task<drogon::HttpResponsePtr> RequestBuffer::dispatch(drogon::HttpRequestPtr req)
{
    const auto route=req->attributes()->get<std::string>("chat.buffering.route");
    const auto args=req->attributes()->get<std::vector<std::string>>("chat.buffering.args");
    if(route=="AiControlController::state" && args.size()==1)
        co_return co_await AiControlController().state(req, args[0]);
    if(route=="AiControlController::pause" && args.size()==1)
        co_return co_await AiControlController().pause(req, args[0]);
    if(route=="AiControlController::stop" && args.size()==2)
        co_return co_await AiControlController().stop(req, args[0], args[1]);
    if(route=="AuthController::registerUser" && args.size()==0)
        co_return co_await AuthController().registerUser(req);
    if(route=="AuthController::login" && args.size()==0)
        co_return co_await AuthController().login(req);
    if(route=="AuthController::logout" && args.size()==0)
        co_return co_await AuthController().logout(req);
    if(route=="AuthController::me" && args.size()==0)
        co_return co_await AuthController().me(req);
    if(route=="AuthController::refresh" && args.size()==0)
        co_return co_await AuthController().refresh(req);
    if(route=="RoomController::create" && args.size()==0)
        co_return co_await RoomController().create(req);
    if(route=="RoomController::list" && args.size()==0)
        co_return co_await RoomController().list(req);
    if(route=="RoomController::invite" && args.size()==1)
        co_return co_await RoomController().invite(req, args[0]);
    if(route=="RoomController::join" && args.size()==1)
        co_return co_await RoomController().join(req, args[0]);
    if(route=="RoomController::leave" && args.size()==1)
        co_return co_await RoomController().leave(req, args[0]);
    if(route=="RoomController::members" && args.size()==1)
        co_return co_await RoomController().members(req, args[0]);
    if(route=="RoomController::markRead" && args.size()==1)
        co_return co_await RoomController().markRead(req, args[0]);
    if(route=="RoomController::search" && args.size()==1)
        co_return co_await RoomController().search(req, args[0]);
    if(route=="RoomController::messages" && args.size()==1)
        co_return co_await RoomController().messages(req, args[0]);
    if(route=="RoomController::transferOwner" && args.size()==1)
        co_return co_await RoomController().transferOwner(req, args[0]);
    if(route=="RoomController::appointAdmin" && args.size()==2)
        co_return co_await RoomController().appointAdmin(req, args[0], args[1]);
    if(route=="RoomController::dismissAdmin" && args.size()==2)
        co_return co_await RoomController().dismissAdmin(req, args[0], args[1]);
    if(route=="RoomController::kick" && args.size()==2)
        co_return co_await RoomController().kick(req, args[0], args[1]);
    if(route=="RoomController::ban" && args.size()==2)
        co_return co_await RoomController().ban(req, args[0], args[1]);
    if(route=="RoomController::unban" && args.size()==2)
        co_return co_await RoomController().unban(req, args[0], args[1]);
    if(route=="RoomController::bans" && args.size()==1)
        co_return co_await RoomController().bans(req, args[0]);
    if(route=="AiController::models" && args.empty()) co_return co_await AiController().models(req);
    if(route=="AiController::createRole" && args.empty()) co_return co_await AiController().createRole(req);
    if(route=="AiController::editPersona" && args.size()==2) co_return co_await AiController().editPersona(req,args[0],args[1]);
    if(route=="AiController::catalog" && args.size()==0)
        co_return co_await AiController().catalog(req);
    if(route=="AiController::request" && args.size()==1)
        co_return co_await AiController().request(req, args[0]);
    if(route=="AiController::remove" && args.size()==2)
        co_return co_await AiController().remove(req, args[0], args[1]);
    if(route=="AiController::pending" && args.size()==1)
        co_return co_await AiController().pending(req, args[0]);
    if(route=="AiController::approve" && args.size()==2)
        co_return co_await AiController().approve(req, args[0], args[1]);
    if(route=="AiController::reject" && args.size()==2)
        co_return co_await AiController().reject(req, args[0], args[1]);
    if(route=="AiController::keys" && args.size()==0)
        co_return co_await AiController().keys(req);
    if(route=="AiController::storeKey" && args.size()==1)
        co_return co_await AiController().storeKey(req, args[0]);
    if(route=="AiController::deleteKey" && args.size()==1)
        co_return co_await AiController().deleteKey(req, args[0]);
    throw ApiError(drogon::k404NotFound,"not_found","business endpoint not found");
}
}
