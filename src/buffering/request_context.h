#pragma once
#include <drogon/HttpRequest.h>
#include <functional>
#include <memory>
#include <string>

namespace chat
{
struct RequestContext
{
    std::string requestId;
    std::string fingerprint; // keyed digest, never a password hash
    std::string anonymousOwner;
    std::function<void()> checkPermit;
    bool replayed{false};
    void check() const { if (checkPermit) checkPermit(); }
};
using RequestContextPtr = std::shared_ptr<RequestContext>;
inline RequestContextPtr requestContext(const drogon::HttpRequestPtr &req)
{
    return req->attributes()->get<RequestContextPtr>("chat.buffering.context");
}
inline void checkRequest(const RequestContextPtr &context)
{
    if (context) context->check();
}
} // namespace chat
