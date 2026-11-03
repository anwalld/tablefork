#include "common/util.h"

#include <charconv>
#include <cstdlib>

namespace chat
{
std::string utf8Prefix(std::string_view text, size_t count)
{
    size_t end = 0;
    for (size_t taken = 0; end < text.size() && taken < count; ++taken)
    {
        // Skip the lead byte, then any continuation bytes.
        ++end;
        while (end < text.size() &&
               (static_cast<unsigned char>(text[end]) & 0xC0) == 0x80)
        {
            ++end;
        }
    }
    return std::string(text.substr(0, end));
}

std::optional<size_t> utf8Length(std::string_view text)
{
    size_t count = 0;
    for (size_t i = 0; i < text.size(); ++count)
    {
        const auto lead = static_cast<unsigned char>(text[i]);
        size_t extra = 0;
        unsigned int codePoint = 0;
        if (lead < 0x80)
        {
            codePoint = lead;
        }
        else if ((lead & 0xE0) == 0xC0)
        {
            extra = 1;
            codePoint = lead & 0x1F;
        }
        else if ((lead & 0xF0) == 0xE0)
        {
            extra = 2;
            codePoint = lead & 0x0F;
        }
        else if ((lead & 0xF8) == 0xF0)
        {
            extra = 3;
            codePoint = lead & 0x07;
        }
        else
        {
            return std::nullopt;
        }
        if (i + extra >= text.size() + (extra == 0 ? 1 : 0))
        {
            return std::nullopt;
        }
        for (size_t j = 1; j <= extra; ++j)
        {
            const auto next = static_cast<unsigned char>(text[i + j]);
            if ((next & 0xC0) != 0x80)
            {
                return std::nullopt;
            }
            codePoint = (codePoint << 6) | (next & 0x3F);
        }
        static constexpr unsigned int minimum[] = {0, 0x80, 0x800, 0x10000};
        if (codePoint < minimum[extra] || codePoint > 0x10FFFF ||
            (codePoint >= 0xD800 && codePoint <= 0xDFFF))
        {
            return std::nullopt;
        }
        i += extra + 1;
    }
    return count;
}

bool isPrintableText(std::string_view text)
{
    if (!utf8Length(text))
    {
        return false;
    }
    for (size_t i = 0; i < text.size(); ++i)
    {
        const auto byte = static_cast<unsigned char>(text[i]);
        if (byte < 0x20 || byte == 0x7F)
        {
            return false;
        }
        // C1 controls, U+0080 to U+009F, are encoded as C2 80 to C2 9F.
        if (byte == 0xC2 && static_cast<unsigned char>(text[i + 1]) < 0xA0)
        {
            return false;
        }
    }
    return true;
}

std::string toJsonString(const Json::Value &value)
{
    Json::StreamWriterBuilder builder;
    builder["indentation"] = "";
    builder["emitUTF8"] = true;
    return Json::writeString(builder, value);
}

drogon::HttpResponsePtr jsonResponse(const Json::Value &body,
                                     drogon::HttpStatusCode status)
{
    auto resp = drogon::HttpResponse::newHttpResponse();
    resp->setStatusCode(status);
    resp->setContentTypeCode(drogon::CT_APPLICATION_JSON);
    resp->setBody(toJsonString(body));
    return resp;
}

Json::Value errorBody(const ApiError &error)
{
    Json::Value body;
    body["error"]["code"] = error.code();
    body["error"]["message"] = error.what();
    body["error"]["retryable"] = error.retryable();
    if (const auto seconds = error.retryAfter())
    {
        body["error"]["retry_after"] = *seconds;
    }
    if (const auto &provider = error.provider())
    {
        body["error"]["provider"] = *provider;
    }
    return body;
}

drogon::HttpResponsePtr errorResponse(const ApiError &error)
{
    auto resp = jsonResponse(errorBody(error), error.status());
    if (const auto seconds = error.retryAfter())
    {
        resp->addHeader("Retry-After", std::to_string(*seconds));
    }
    return resp;
}

const Json::Value &jsonBody(const drogon::HttpRequestPtr &req)
{
    const auto &json = req->getJsonObject();
    if (!json || !json->isObject())
    {
        throw ApiError::invalidArgument("expected a JSON object");
    }
    return *json;
}

std::string stringField(const Json::Value &body, const char *name)
{
    const auto &value = body[name];
    if (!value.isString())
    {
        throw ApiError::invalidArgument(std::string(name) + " is required");
    }
    return value.asString();
}

std::optional<int64_t> intParameter(const drogon::HttpRequestPtr &req,
                                    const std::string &name)
{
    const auto &text = req->getParameter(name);
    if (text.empty())
    {
        return std::nullopt;
    }
    char *end = nullptr;
    const auto value = std::strtoll(text.c_str(), &end, 10);
    if (*end != '\0' || value < 0)
    {
        throw ApiError::invalidArgument(name + " must be a non-negative integer");
    }
    return value;
}

int64_t parseRoomId(const std::string &text)
{
    int64_t roomId = 0;
    const auto *end = text.data() + text.size();
    const auto [parsed, error] = std::from_chars(text.data(), end, roomId);
    if (error != std::errc{} || parsed != end || roomId <= 0)
    {
        throw ApiError::roomNotFound();
    }
    return roomId;
}

drogon::Task<> commit(std::shared_ptr<drogon::orm::Transaction> tx)
{
    if (!co_await CommitAwaiter(std::move(tx)))
    {
        throw ApiError::unavailable();
    }
}
}  // namespace chat
