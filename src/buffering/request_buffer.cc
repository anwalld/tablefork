#include "buffering/request_buffer.h"
#include "common/trace.h"
#include <drogon/drogon.h>
#include <openssl/crypto.h>
#include <cstdlib>
#include <sstream>
#include "common/config.h"
#include "common/util.h"

namespace chat {
namespace {
std::string env(const char *key)
{
    const auto *value=std::getenv(key);
    return value ? value : "";
}
long number(const char *key,long fallback) { return positiveOr(std::getenv(key),fallback); }
bool validToken(const std::string &value)
{
    return value.size()==64 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
std::string modeName(RunPermit::Mode mode)
{
    if(mode==RunPermit::Mode::Direct) return "direct";
    if(mode==RunPermit::Mode::Draining) return "draining";
    return "paused";
}
bool recoverable(const drogon::HttpRequestPtr &req)
{
    const auto &path=req->getPath();
    if(req->method()==drogon::Get) return true;
    if(path=="/api/auth/register" || path=="/api/ai/roles") return true;
    if(path.rfind("/api/auth/",0)==0) return false;
    if(path.rfind("/api/keys/",0)==0 && req->method()==drogon::Put) return false;
    return path.rfind("/api/rooms",0)==0 || (path.rfind("/api/keys/",0)==0 && req->method()==drogon::Delete);
}
Json::Value descriptor(const drogon::HttpRequestPtr &req)
{
    Json::Value value;
    value["kind"]="http";
    value["method"]=static_cast<int>(req->method());
    value["path"]=req->getPath();
    value["query"]=req->query();
    value["body"]=std::string(req->body());
    // Deliberate allowlist: never trust forwarding or internal control headers.
    for(const auto *name:{"content-type","origin","user-agent"})
        value["headers"][name]=req->getHeader(name);
    for(const auto *name:{"chat_session", "chat_device"})
        value["cookies"][name]=req->getCookie(name);
    return value;
}
std::string parameters(const drogon::HttpRequestPtr &req)
{
    Json::Value value;
    value["method"]=static_cast<int>(req->method()); value["path"]=req->getPath();
    value["query"]=req->query(); value["body"]=std::string(req->body());
    return toJsonString(value);
}
Json::Value serialize(const drogon::HttpResponsePtr &resp)
{
    Json::Value value;
    value["status"]=static_cast<int>(resp->statusCode());
    value["body"]=std::string(resp->body());
    value["content_type"]=resp->getHeader("content-type");
    value["retry_after"]=resp->getHeader("retry-after");
    value["cookies"]=Json::arrayValue;
    for(const auto &[key,cookie]:resp->cookies())
    {
        Json::Value item;
        item["key"]=cookie.key(); item["value"]=cookie.value(); item["path"]=cookie.path();
        item["secure"]=cookie.isSecure(); item["http_only"]=cookie.isHttpOnly();
        item["same_site"]=static_cast<int>(cookie.sameSite());
        if(cookie.maxAge()) item["max_age"]=*cookie.maxAge();
        value["cookies"].append(item);
    }
    return value;
}
void deliverCookies(const drogon::HttpResponsePtr &resp,const Json::Value &value)
{
    for(const auto &item:value["cookies"])
    {
        drogon::Cookie cookie(item["key"].asString(),item["value"].asString());
        cookie.setPath(item["path"].asString()); cookie.setSecure(item["secure"].asBool());
        cookie.setHttpOnly(item["http_only"].asBool());
        cookie.setSameSite(static_cast<drogon::Cookie::SameSite>(item["same_site"].asInt()));
        if(item.isMember("max_age")) cookie.setMaxAge(item["max_age"].asInt());
        resp->addCookie(cookie);
    }
}
}
RequestBuffer &RequestBuffer::instance() { static RequestBuffer value; return value; }
RequestBuffer::RequestBuffer()
 : enabled_(env("BUFFER_REQUESTS_ENABLED")=="1"), token_(env("BUFFER_CONTROL_TOKEN")),
   permit_(PayloadCrypto::randomToken(),std::chrono::milliseconds(number("BUFFER_PERMIT_MS",5000))),
   drain_(number("QUEUE_DRAIN_RATE",50),number("QUEUE_MAX_RUNNING",8))
{
    if(enabled_)
    {
        if(token_.size()<32) throw std::invalid_argument("BUFFER_CONTROL_TOKEN must contain at least 32 bytes");
        crypto_=std::make_unique<PayloadCrypto>(env("QUEUE_ENCRYPTION_KEY"));
    }
}
void RequestBuffer::start()
{
    if(!enabled_) return;
    SharedQueue::Options options;
    options.maxWaiting=number("QUEUE_MAX_WAITING",1024);
    options.maxBodyBytes=number("QUEUE_MAX_BODY_BYTES",16*1024*1024);
    options.maxMemory=number("QUEUE_MAX_MEMORY_BYTES",64*1024*1024);
    options.maxResultBytes=number("QUEUE_MAX_RESULT_BYTES",16*1024*1024);
    options.waitMs=number("QUEUE_WAIT_MS",45000);
    options.resultMs=number("QUEUE_RESULT_MS",300000);
    options.claimMs=number("QUEUE_CLAIM_MS",10000);
    queue_=std::make_unique<SharedQueue>(drogon::app().getRedisClient("queue"),*crypto_,"business-buffer:v1",options);
    drogon::app().getLoop()->runEvery(.01,[this] {
        if(ticking_.exchange(true)) return;
        drogon::async_run([this]() -> drogon::Task<> {
            try { co_await tick(); }
            catch(const std::exception &) { ready_=false; }
            ticking_=false;
        });
    });
}
RequestContextPtr RequestBuffer::executionContext(std::string requestId,std::string fingerprint,std::string owner)
{
    auto stamp=permit_.enter();
    if(!stamp) throw ApiError::unavailable();
    auto context=std::make_shared<RequestContext>();
    context->requestId=std::move(requestId); context->fingerprint=std::move(fingerprint);
    context->anonymousOwner=std::move(owner);
    context->checkPermit=[this,stamp=*stamp]{ if(!permit_.valid(stamp)) throw ApiError::unavailable(); };
    return context;
}
RequestContextPtr RequestBuffer::backgroundContext()
{
    if(!enabled_) return {};
    return executionContext(PayloadCrypto::randomToken(),std::string(64,'0'),"internal");
}
drogon::Task<Json::Value> RequestBuffer::enqueue(Json::Value value,std::string capability,
                                                std::string requestId,size_t bytes,bool safe)
{
    if(!queue_ || !ready_) throw ApiError::unavailable();
    co_return co_await queue_->admit(std::move(capability),std::move(requestId),std::move(value),bytes,safe,!permit_.enter().has_value());
}
drogon::Task<drogon::HttpResponsePtr> RequestBuffer::admit(drogon::HttpRequestPtr req, std::string route, std::vector<std::string> args)
{
    if(!enabled_) co_return nullptr;
    if(req->attributes()->get<bool>("chat.buffering.execution"))
    {
        checkRequest(requestContext(req));
        co_return nullptr;
    }
    auto capability=req->getHeader("x-queue-owner");
    if(capability.empty()) capability=PayloadCrypto::randomToken();
    if(!validToken(capability)) throw ApiError::invalidArgument("invalid queue owner capability");
    auto requestId=req->getHeader("x-request-id");
    if(requestId.empty()) requestId=req->attributes()->get<std::string>("chat.trace.id");
    if(requestId.empty()) requestId=PayloadCrypto::randomToken();
    if(requestId.size()>128 || !isPrintableText(requestId)) throw ApiError::invalidArgument("invalid request ID");
    auto value=descriptor(req);
    value["route"]=route;
    value["args"]=Json::arrayValue;
    for(const auto &arg:args) value["args"].append(arg);
    value["request_id"]=requestId;
    value["fingerprint"]=crypto_->digest("business-parameters",parameters(req)+toJsonString(value["args"])+route);
    value["owner"]=crypto_->digest("owner",capability);
    TraceSpan admission(safeTraceId(requestId), route, "queue_admission");
    auto admitted=co_await enqueue(value,capability,requestId,req->body().size(),recoverable(req));
    admission.finish(admitted["direct"].asBool() ? "direct" : admitted.isMember("error") ? "rejected" : "queued");
    if(admitted["direct"].asBool())
    {
        auto context=executionContext(requestId,value["fingerprint"].asString(),value["owner"].asString());
        req->attributes()->insert("chat.buffering.context",context);
        co_return nullptr;
    }
    if(admitted.isMember("error"))
    {
        if(admitted["error"]=="request_conflict")
            throw ApiError(drogon::k409Conflict,"request_conflict","request ID was reused with different parameters");
        throw ApiError::unavailable();
    }
    Json::Value body;
    body["task_id"]=admitted["id"]; body["state"]=admitted["state"];
    // Legacy clients without a supplied capability need it to claim their
    // anonymous task. This is not a login/session cookie.
    body["claim_token"]=capability;
    co_return jsonResponse(body,drogon::k202Accepted);
}
drogon::Task<drogon::HttpResponsePtr> RequestBuffer::query(drogon::HttpRequestPtr req,std::string id)
{
    if(!enabled_ || !queue_ || !ready_) throw ApiError::unavailable();
    const auto capability=req->getHeader("x-queue-owner");
    if(!validToken(capability) || !validToken(id)) throw ApiError::unauthenticated();
    auto value=co_await queue_->query(id,capability);
    const auto error=value.get("error", "").asString();
    if(error=="forbidden") throw ApiError::unauthenticated();
    if(!error.empty()) value["state"]="unknown";
    Json::Value body;
    body["task_id"]=id; body["state"]=value["state"];
    auto result=value["result"];
    if(result.isObject())
    {
        body["response"]=result;
        body["response"].removeMember("cookies"); // HttpOnly stays outside JS
    }
    auto resp=jsonResponse(body);
    if(value["state"]=="succeeded") deliverCookies(resp,result);
    resp->addHeader("Cache-Control","no-store");
    co_return resp;
}
drogon::Task<drogon::HttpResponsePtr> RequestBuffer::control(drogon::HttpRequestPtr req,std::string action)
{
    if(!enabled_) throw ApiError::unavailable();
    const auto supplied=req->getHeader("authorization");
    const auto expected="Bearer "+token_;
    if(supplied.size()!=expected.size() || CRYPTO_memcmp(supplied.data(),expected.data(),expected.size()))
        throw ApiError::unauthenticated();
    const auto &body=jsonBody(req);
    Json::Value response;
    if(action=="status")
    {
        response["boot"]=permit_.boot(); response["mode"]=modeName(permit_.mode());
        response["queue_ready"]=ready_.load();
        const auto state=permit_.state();
        response["epoch"]=state.epoch; response["revision"]=Json::UInt64(state.revision);
        if(queue_ && ready_)
        {
            response["queue"]=co_await queue_->status();
            if(response["queue"].isMember("error")) ready_=false;
        }
        response["queue_ready"]=ready_.load();
        co_return jsonResponse(response);
    }
    if(body["boot"].asString()!=permit_.boot()) throw ApiError::invalidArgument("stale backend boot");
    const auto epoch=body["epoch"].asString(); const auto revision=body["revision"].asUInt64();
    if(action=="fence") response["ok"]=permit_.fence(body["expected_epoch"].asString(),epoch,revision);
    else if(action=="pause") response["ok"]=permit_.pause(epoch,revision);
    else if(action=="challenge")
    {
        auto challenge=permit_.challenge(epoch,revision,PayloadCrypto::randomToken());
        response["ok"]=static_cast<bool>(challenge);
        if(challenge) response["nonce"]=challenge->nonce;
    }
    else if(action=="grant")
    {
        RunPermit::Challenge challenge{permit_.boot(),epoch,body["nonce"].asString(),revision};
        const auto mode=body["mode"]=="direct" ? RunPermit::Mode::Direct : RunPermit::Mode::Draining;
        response["ok"]=permit_.grant(challenge,mode);
    }
    else if(action=="queue")
    {
        if(!queue_ || !ready_) throw ApiError::unavailable();
        response=co_await queue_->control(body["expected_epoch"].asString(),epoch,revision,body["paused"].asBool());
    }
    else throw ApiError::invalidArgument("unknown control command");
    co_return jsonResponse(response);
}
drogon::Task<> RequestBuffer::tick()
{
    if(!ready_)
    {
        co_await queue_->initialize();
        ready_=true;
    }
    auto stamp=permit_.enter();
    if(!stamp || !drain_.tryStart()) co_return;
    const auto claim=PayloadCrypto::randomToken();
    Json::Value task;
    try { task=co_await queue_->claim(stamp->epoch,permit_.boot(),claim); }
    catch(...) { drain_.finish(); throw; }
    if(task["error"]=="queue_restarted") ready_=false;
    if(!task.isMember("id")) { drain_.finish(); co_return; }
    const auto &value=task["payload"];
    RequestContextPtr context;
    try { context=executionContext(value["request_id"].asString(),value["fingerprint"].asString(),value["owner"].asString()); }
    catch(...) { drain_.finish(); co_return; } // lost permit: lease recovery decides outcome
    drogon::async_run([this,task=std::move(task),context=std::move(context),claim]() mutable -> drogon::Task<> {
        try { co_await execute(std::move(task),std::move(context),claim); }
        catch(const std::exception &) { /* claim expiry handles unknown/recovery */ }
        drain_.finish();
    });
}
drogon::Task<> RequestBuffer::execute(Json::Value task,RequestContextPtr context,std::string claim)
{
    TraceSpan waiting(safeTraceId(context->requestId), "business", "queue_wait");
    waiting.finish("claimed", task.get("wait_ms", 0).asDouble());
    TraceSpan execution(safeTraceId(context->requestId), task["payload"].get("route", "ws.send").asString(), "queue_execution");
    drogon::HttpResponsePtr resp;
    try
    {
        context->check();
        const auto &value=task["payload"];
        if(value["kind"]=="ws")
        {
            resp=jsonResponse(co_await sendChat(value,context));
        }
        else
        {
        auto req=drogon::HttpRequest::newHttpRequest();
        req->setMethod(static_cast<drogon::HttpMethod>(value["method"].asInt()));
        req->setPath(value["path"].asString()); req->setBody(value["body"].asString());
        // Parse the original query through the normal request API.
        const auto rawQuery=value["query"].asString();
        for(const auto &part:drogon::utils::splitString(rawQuery,"&"))
        {
            const auto equals=part.find('=');
            if(!part.empty()) req->setParameter(drogon::utils::urlDecode(part.substr(0,equals)),
                equals==std::string::npos ? "" : drogon::utils::urlDecode(part.substr(equals+1)));
        }
        for(const auto &name:value["headers"].getMemberNames()) req->addHeader(name,value["headers"][name].asString());
        for(const auto &name:value["cookies"].getMemberNames())
            if(!value["cookies"][name].asString().empty()) req->addCookie(name,value["cookies"][name].asString());
        req->attributes()->insert("chat.buffering.route",value["route"].asString());
        std::vector<std::string> args;
        for(const auto &arg:value["args"]) args.push_back(arg.asString());
        req->attributes()->insert("chat.buffering.args",args);
        req->attributes()->insert("chat.buffering.execution",true);
        req->attributes()->insert("chat.buffering.context",context);
        resp=co_await dispatch(req);
        }
    }
    catch(const ApiError &e) { LOG_WARN << "buffered execution: " << e.code(); resp=errorResponse(e); }
    catch(const std::exception &e) { LOG_ERROR << "buffered execution exception type: " << typeid(e).name(); resp=errorResponse(ApiError::unavailable()); }
#if defined(CHAT_BUFFER_TEST_HOOKS)
    // Deterministic commit/worker-death test only; absent from runtime builds.
    if(context->requestId.rfind("crash-probe-",0)==0)
    {
        const auto delay=number("BUFFER_TEST_AFTER_EXECUTION_MS",0);
        if(delay>0) co_await drogon::sleepCoro(drogon::app().getLoop(),std::chrono::milliseconds(delay));
    }
#endif
    execution.finish(std::to_string(static_cast<int>(resp->statusCode())));
    const auto state=resp->statusCode()>=500 ? "unknown" : resp->statusCode()>=400 ? "failed" : "succeeded";
    co_await queue_->finish(task["id"].asString(),permit_.boot(),std::move(claim),state,serialize(resp));
}
} // namespace chat
