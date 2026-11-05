#include "buffering/payload_crypto.h"

#include <climits>
#include <memory>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <stdexcept>

namespace chat
{
namespace
{
std::string hex(std::string_view bytes)
{
    constexpr char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(bytes.size() * 2);
    for (unsigned char c : bytes)
    {
        out += digits[c >> 4];
        out += digits[c & 15];
    }
    return out;
}
std::string unhex(std::string_view text)
{
    auto digit = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        throw std::invalid_argument("invalid encrypted envelope");
    };
    if (text.size() % 2) throw std::invalid_argument("invalid encrypted envelope");
    std::string out(text.size() / 2, '\0');
    for (size_t i = 0; i < out.size(); ++i)
        out[i] = static_cast<char>((digit(text[i * 2]) << 4) | digit(text[i * 2 + 1]));
    return out;
}
using Context = std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)>;
void check(bool ok)
{
    if (!ok) throw std::runtime_error("encrypted queue payload verification failed");
}
}  // namespace

PayloadCrypto::PayloadCrypto(std::string_view hexKey)
{
    if (hexKey.size() != 64) throw std::invalid_argument("queue key must contain 64 hex digits");
    auto bytes = unhex(hexKey);
    std::copy(bytes.begin(), bytes.end(), key_.begin());
}

std::string PayloadCrypto::randomToken()
{
    std::string bytes(32, '\0');
    check(RAND_bytes(reinterpret_cast<unsigned char *>(bytes.data()), bytes.size()) == 1);
    return hex(bytes);
}

std::string PayloadCrypto::seal(std::string_view text, std::string_view purpose) const
{
    check(text.size() <= INT_MAX && purpose.size() <= INT_MAX);
    std::string nonce(12, '\0');
    check(RAND_bytes(reinterpret_cast<unsigned char *>(nonce.data()), nonce.size()) == 1);
    Context ctx(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    check(ctx != nullptr);
    check(EVP_EncryptInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr, key_.data(),
                            reinterpret_cast<const unsigned char *>(nonce.data())) == 1);
    int count = 0;
    check(EVP_EncryptUpdate(ctx.get(), nullptr, &count,
                           reinterpret_cast<const unsigned char *>(purpose.data()), purpose.size()) == 1);
    std::string cipher(text.size() + 16, '\0');
    check(EVP_EncryptUpdate(ctx.get(), reinterpret_cast<unsigned char *>(cipher.data()), &count,
                           reinterpret_cast<const unsigned char *>(text.data()), text.size()) == 1);
    int total = count;
    check(EVP_EncryptFinal_ex(ctx.get(), reinterpret_cast<unsigned char *>(cipher.data()) + total, &count) == 1);
    cipher.resize(total + count);
    std::string tag(16, '\0');
    check(EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_GET_TAG, tag.size(), tag.data()) == 1);
    return hex(nonce + tag + cipher);
}

std::string PayloadCrypto::open(std::string_view envelope, std::string_view purpose) const
{
    check(envelope.size() <= INT_MAX && purpose.size() <= INT_MAX);
    auto bytes = unhex(envelope);
    check(bytes.size() >= 28);
    Context ctx(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    check(ctx != nullptr);
    check(EVP_DecryptInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr, key_.data(),
                            reinterpret_cast<const unsigned char *>(bytes.data())) == 1);
    int count = 0;
    check(EVP_DecryptUpdate(ctx.get(), nullptr, &count,
                           reinterpret_cast<const unsigned char *>(purpose.data()), purpose.size()) == 1);
    std::string text(bytes.size(), '\0');
    check(EVP_DecryptUpdate(ctx.get(), reinterpret_cast<unsigned char *>(text.data()), &count,
                           reinterpret_cast<const unsigned char *>(bytes.data()) + 28, bytes.size() - 28) == 1);
    int total = count;
    check(EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_TAG, 16, bytes.data() + 12) == 1);
    check(EVP_DecryptFinal_ex(ctx.get(), reinterpret_cast<unsigned char *>(text.data()) + total, &count) == 1);
    text.resize(total + count);
    return text;
}

std::string PayloadCrypto::digest(std::string_view domain, std::string_view text) const
{
    check(domain.size() <= 128);
    // Domain length encoding avoids ambiguous prefix concatenation.
    std::string input = std::to_string(domain.size()) + ":" + std::string(domain) + std::string(text);
    unsigned char out[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    check(HMAC(EVP_sha256(), key_.data(), key_.size(),
               reinterpret_cast<const unsigned char *>(input.data()), input.size(), out, &length) != nullptr);
    return hex(std::string_view(reinterpret_cast<char *>(out), length));
}
}  // namespace chat
