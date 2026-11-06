#include "common/trace.h"
#include "common/util.h"
#include <drogon/utils/Utilities.h>
#include <cctype>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <openssl/sha.h>

namespace chat
{
std::string safeTraceId(const std::string &id)
{
    if (id.empty()) return drogon::utils::getUuid();
    bool valid = id.size() <= 128;
    for (const unsigned char c : id)
        if (!std::isalnum(c) && c != '-' && c != '_' && c != ':') valid = false;
    if (!valid)
    {
        unsigned char digest[SHA256_DIGEST_LENGTH];
        SHA256(reinterpret_cast<const unsigned char *>(id.data()), id.size(), digest);
        constexpr char digits[] = "0123456789abcdef";
        std::string encoded;
        for (const auto byte : digest)
        {
            encoded += digits[byte >> 4];
            encoded += digits[byte & 15];
        }
        return encoded;
    }
    return id;
}
TraceSpan::TraceSpan(std::string requestId, std::string operation, std::string stage)
    : requestId_(safeTraceId(requestId)), operation_(std::move(operation)), stage_(std::move(stage)),
      start_(std::chrono::steady_clock::now()) {}
TraceSpan::~TraceSpan() { if (!finished_) finish("error"); }
void TraceSpan::finish(std::string result, double elapsedMs)
{
    if (finished_) return;
    finished_ = true;
    Json::Value event;
    event["event"] = "request_stage";
    event["request_id"] = requestId_;
    const auto *host = std::getenv("HOSTNAME");
    event["instance"] = host ? host : "app";
    event["operation"] = operation_;
    event["stage"] = stage_;
    event["result"] = std::move(result);
    event["elapsed_ms"] = elapsedMs >= 0 ? elapsedMs : std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now()-start_).count();
    event["timestamp_ms"] = Json::Int64(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    static std::mutex output;
    const auto text = toJsonString(event) + "\n";
    std::lock_guard<std::mutex> guard(output);
    std::cout << text << std::flush;
}
}
