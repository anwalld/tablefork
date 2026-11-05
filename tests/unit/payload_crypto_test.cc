#include <drogon/drogon_test.h>
#include "buffering/payload_crypto.h"

DROGON_TEST(QueuePayloadAuthenticatesOwnerAndBinaryBody)
{
    chat::PayloadCrypto crypto(std::string(64, 'a'));
    const std::string secret("password\0cookie", 15);
    auto a = crypto.seal(secret, "task-a:request");
    auto b = crypto.seal(secret, "task-a:request");
    CHECK(a != b);
    CHECK(a.find("password") == std::string::npos);
    CHECK(crypto.open(a, "task-a:request") == secret);
    CHECK(crypto.open(crypto.seal("", "empty"), "empty").empty());
    auto rejects = [&](const std::string &envelope, const std::string &purpose) {
        try { crypto.open(envelope, purpose); return false; }
        catch (const std::exception &) { return true; }
    };
    CHECK(rejects(a, "task-b:request"));
    a.back() = a.back() == 'a' ? 'b' : 'a';
    CHECK(rejects(a, "task-a:request"));
    CHECK(rejects("bad", "task-a:request"));
    CHECK(crypto.digest("owner", "capability") != crypto.digest("task", "capability"));
    CHECK(crypto.digest("owner", "capability") == crypto.digest("owner", "capability"));
    CHECK(chat::PayloadCrypto::randomToken().size() == 64U);
}
