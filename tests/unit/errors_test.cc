#include <drogon/drogon_test.h>

#include "common/config.h"
#include "common/errors.h"
#include "common/util.h"

using chat::ApiError;

DROGON_TEST(RetryAfterAppearsInBodyAndHeader)
{
    const auto error = ApiError::tooManyAttempts(7);
    const auto body = chat::errorBody(error);
    CHECK(body["error"]["code"].asString() == "too_many_attempts");
    CHECK(body["error"]["retryable"].asBool());
    CHECK(body["error"]["retry_after"].asInt() == 7);

    const auto resp = chat::errorResponse(error);
    CHECK(resp->getStatusCode() == drogon::k429TooManyRequests);
    CHECK(resp->getHeader("Retry-After") == "7");
}

DROGON_TEST(RateLimitedIsRetryable)
{
    const auto body = chat::errorBody(ApiError::rateLimited(3));
    CHECK(body["error"]["code"].asString() == "rate_limited");
    CHECK(body["error"]["retryable"].asBool());
    CHECK(body["error"]["retry_after"].asInt() == 3);
}

DROGON_TEST(ErrorsWithoutRetryAfterOmitIt)
{
    const auto error = ApiError::weakPassword("too common");
    const auto body = chat::errorBody(error);
    CHECK(body["error"]["code"].asString() == "weak_password");
    CHECK(!body["error"]["retryable"].asBool());
    CHECK(!body["error"].isMember("retry_after"));
    CHECK(chat::errorResponse(error)->getHeader("Retry-After").empty());
    CHECK(chat::errorResponse(error)->getStatusCode() == drogon::k400BadRequest);
}

DROGON_TEST(PositiveOrFallsBackOnBadValues)
{
    CHECK(chat::positiveOr(nullptr, 5) == 5);
    CHECK(chat::positiveOr("", 5) == 5);
    CHECK(chat::positiveOr("12", 5) == 12);
    CHECK(chat::positiveOr("0", 5) == 5);
    CHECK(chat::positiveOr("-3", 5) == 5);
    CHECK(chat::positiveOr("abc", 5) == 5);
    CHECK(chat::positiveOr("10s", 5) == 5);
    CHECK(chat::positiveOr("99999999999999999999", 5) == 5);
}
