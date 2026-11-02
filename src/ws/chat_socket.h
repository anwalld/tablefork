#pragma once

#include <drogon/WebSocketController.h>

namespace chat
{
class ChatSocket : public drogon::WebSocketController<ChatSocket>
{
  public:
    WS_PATH_LIST_BEGIN
    WS_PATH_ADD("/ws", "chat::WsAuthFilter");
    WS_PATH_LIST_END

    void handleNewConnection(const drogon::HttpRequestPtr &req,
                             const drogon::WebSocketConnectionPtr &connection) override;
    void handleNewMessage(const drogon::WebSocketConnectionPtr &connection,
                          std::string &&message,
                          const drogon::WebSocketMessageType &type) override;
    void handleConnectionClosed(
        const drogon::WebSocketConnectionPtr &connection) override;
};
}  // namespace chat
