#pragma once

#include <drogon/HttpFilter.h>

namespace chat
{
// Rejects WebSocket upgrades without a valid session or from a foreign Origin.
// Browsers send the session cookie cross-site, so the Origin check is what
// stops another site from opening a socket as the user.
class WsAuthFilter : public drogon::HttpFilter<WsAuthFilter>
{
  public:
    void doFilter(const drogon::HttpRequestPtr &req,
                  drogon::FilterCallback &&reject,
                  drogon::FilterChainCallback &&next) override;
};
}  // namespace chat
