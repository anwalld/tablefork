#pragma once
#include "buffering/request_context.h"
#include <drogon/utils/coroutine.h>
namespace chat {
// A durable intent fence for operations whose Redis/gRPC effects cannot join
// a SQL transaction. A duplicate intent is UNKNOWN, never blindly re-executed.
// Successful response credentials remain only in encrypted temporary results.
drogon::Task<> beginExternalRequest(RequestContextPtr context,std::string actor,std::string operation);
}
