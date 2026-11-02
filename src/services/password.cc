#include "services/password.h"

#include <array>
#include <drogon/utils/Utilities.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <stdexcept>
#include <trantor/net/EventLoopThreadPool.h>
#include <vector>

namespace chat
{
namespace
{
constexpr uint64_t kN = 1U << 15;
constexpr uint64_t kR = 8;
constexpr uint64_t kP = 1;
constexpr uint64_t kMaxMemory = 64U * 1024 * 1024;
constexpr size_t kSaltBytes = 16;
constexpr size_t kKeyBytes = 32;

std::array<unsigned char, kKeyBytes> derive(const std::string &password,
                                            const std::string &salt)
{
    std::array<unsigned char, kKeyBytes> key{};
    if (EVP_PBE_scrypt(password.data(),
                       password.size(),
                       reinterpret_cast<const unsigned char *>(salt.data()),
                       salt.size(),
                       kN,
                       kR,
                       kP,
                       kMaxMemory,
                       key.data(),
                       key.size()) != 1)
    {
        throw std::runtime_error("scrypt failed");
    }
    return key;
}

std::string makeHash(const std::string &password)
{
    std::string salt(kSaltBytes, '\0');
    if (!drogon::utils::secureRandomBytes(salt.data(), salt.size()))
    {
        throw std::runtime_error("no secure random source");
    }
    const auto key = derive(password, salt);
    return "scrypt$" + drogon::utils::binaryStringToHex(
                           reinterpret_cast<const unsigned char *>(salt.data()),
                           salt.size()) +
           "$" + drogon::utils::binaryStringToHex(key.data(), key.size());
}

bool matches(const std::string &password, const std::string &stored)
{
    const auto parts = drogon::utils::splitString(stored, "$");
    if (parts.size() != 3 || parts[0] != "scrypt")
    {
        return false;
    }
    const auto salt = drogon::utils::hexToBinaryString(parts[1].data(),
                                                       parts[1].size());
    const auto expected = drogon::utils::hexToBinaryString(parts[2].data(),
                                                           parts[2].size());
    const auto key = derive(password, salt);
    return expected.size() == key.size() &&
           CRYPTO_memcmp(expected.data(), key.data(), key.size()) == 0;
}

trantor::EventLoop *cryptoLoop()
{
    static auto *pool = [] {
        auto *p = new trantor::EventLoopThreadPool(2, "CryptoLoop");
        p->start();
        return p;
    }();
    return pool->getNextLoop();
}
}  // namespace

drogon::Task<std::string> hashPassword(std::string password)
{
    co_return co_await drogon::queueInLoopCoro<std::string>(
        cryptoLoop(), [&password] { return makeHash(password); });
}

drogon::Task<bool> verifyPassword(std::string password, std::string stored)
{
    co_return co_await drogon::queueInLoopCoro<bool>(
        cryptoLoop(),
        [&password, &stored] { return matches(password, stored); });
}

drogon::Task<> burnPasswordCheck(std::string password)
{
    co_await drogon::queueInLoopCoro<bool>(cryptoLoop(), [&password] {
        static const std::string decoy = makeHash("decoy");
        return matches(password, decoy);
    });
}
}  // namespace chat
