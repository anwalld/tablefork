#pragma once
#include <atomic>
#include <drogon/HttpResponse.h>
#include <memory>
#include "buffering/leaky_gate.h"
#include "buffering/run_permit.h"
#include "buffering/shared_queue.h"
#include "buffering/request_context.h"

namespace chat
{
class RequestBuffer
{
  public:
    static RequestBuffer &instance();
    bool enabled() const { return enabled_; }
    void start();
    drogon::Task<drogon::HttpResponsePtr> admit(drogon::HttpRequestPtr req, std::string route, std::vector<std::string> args = {});
    drogon::Task<drogon::HttpResponsePtr> query(drogon::HttpRequestPtr req, std::string id);
    drogon::Task<drogon::HttpResponsePtr> control(drogon::HttpRequestPtr req, std::string action);
    RequestContextPtr executionContext(std::string requestId, std::string fingerprint,
                                      std::string owner);
    RequestContextPtr backgroundContext();
    static drogon::Task<Json::Value> sendChat(Json::Value descriptor, RequestContextPtr context);
    // Also used by the WS admission path. Never trusts a client-supplied stamp.
    drogon::Task<Json::Value> enqueue(Json::Value descriptor, std::string capability,
                                    std::string requestId, size_t bytes, bool recoverable);
    static drogon::Task<drogon::HttpResponsePtr> dispatch(drogon::HttpRequestPtr req);
  private:
    RequestBuffer();
    drogon::Task<> tick();
    drogon::Task<> execute(Json::Value task, RequestContextPtr context, std::string claim);
    const bool enabled_;
    const std::string token_;
    RunPermit permit_;
    LeakyGate drain_;
    std::unique_ptr<PayloadCrypto> crypto_;
    std::unique_ptr<SharedQueue> queue_;
    std::atomic<bool> ticking_{false};
    std::atomic<bool> ready_{false};
};
} // namespace chat
