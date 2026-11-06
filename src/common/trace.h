#pragma once
#include <chrono>
#include <string>

namespace chat
{
// Only allowlisted metadata belongs here. SQL, payloads and credentials are
// deliberately absent from this interface.
class TraceSpan
{
  public:
    TraceSpan(std::string requestId, std::string operation, std::string stage);
    ~TraceSpan();
    void finish(std::string result = "ok", double elapsedMs = -1);
    TraceSpan(const TraceSpan &) = delete;
    TraceSpan &operator=(const TraceSpan &) = delete;
  private:
    std::string requestId_, operation_, stage_;
    std::chrono::steady_clock::time_point start_;
    bool finished_{false};
};
std::string safeTraceId(const std::string &id);
}
