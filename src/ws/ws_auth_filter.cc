#include "ws/ws_auth_filter.h"

#include <algorithm>
#include <drogon/drogon.h>

#include "common/config.h"
#include "common/util.h"
#include "services/session_store.h"

namespace chat
{
void WsAuthFilter::doFilter(const drogon::HttpRequestPtr &req,
                            drogon::FilterCallback &&reject,
                            drogon::FilterChainCallback &&next)
{
    const auto &allowed = Config::get().allowedOrigins;
    if (std::find(allowed.begin(), allowed.end(), req->getHeader("origin")) ==
        allowed.end())
    {
        const ApiError error(drogon::k403Forbidden,
                             "origin_not_allowed",
                             "origin not allowed");
        reject(jsonResponse(errorBody(error), error.status()));
        return;
    }
    drogon::async_run([req,
                       reject = std::move(reject),
                       next = std::move(next)]() -> drogon::Task<> {
        try
        {
            const auto session = co_await SessionStore::require(req);
            req->attributes()->insert("session", session);
            next();
        }
        catch (const ApiError &error)
        {
            reject(jsonResponse(errorBody(error), error.status()));
        }
    });
}
}  // namespace chat
