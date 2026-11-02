#pragma once

#include <cstdint>
#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>
#include <drogon/orm/DbClient.h>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

#include "common/errors.h"

namespace chat
{
// Number of UTF-8 code points, or nullopt when the text is not valid UTF-8.
std::optional<size_t> utf8Length(std::string_view text);

// True for valid UTF-8 without control characters. Names must satisfy this: a
// NUL would truncate them on the way through Redis, letting one user pass as
// another.
bool isPrintableText(std::string_view text);

std::string toJsonString(const Json::Value &value);

drogon::HttpResponsePtr jsonResponse(
    const Json::Value &body,
    drogon::HttpStatusCode status = drogon::k200OK);

Json::Value errorBody(const ApiError &error);

const Json::Value &jsonBody(const drogon::HttpRequestPtr &req);

// Reads a string field, reporting a missing one as invalid_argument.
std::string stringField(const Json::Value &body, const char *name);

// Reads an optional integer query parameter.
std::optional<int64_t> intParameter(const drogon::HttpRequestPtr &req,
                                    const std::string &name);

// Parses a room id from a URL. Anything that cannot be a room is simply a room
// that does not exist.
int64_t parseRoomId(const std::string &text);

// Commits the transaction and waits for the outcome. Transactions commit when
// their last reference goes away, so this takes ownership.
struct CommitAwaiter : drogon::CallbackAwaiter<bool>
{
    explicit CommitAwaiter(std::shared_ptr<drogon::orm::Transaction> tx)
        : tx_(std::move(tx))
    {
    }

    void await_suspend(std::coroutine_handle<> handle)
    {
        tx_->setCommitCallback([this, handle](bool committed) {
            setValue(committed);
            handle.resume();
        });
        tx_.reset();
    }

  private:
    std::shared_ptr<drogon::orm::Transaction> tx_;
};

// Throws ApiError::unavailable() when the commit fails.
drogon::Task<> commit(std::shared_ptr<drogon::orm::Transaction> tx);
}  // namespace chat
