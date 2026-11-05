#pragma once

#include <array>
#include <string>
#include <string_view>

namespace chat
{
// AES-256-GCM envelopes; task ID/record purpose is authenticated as AAD.
// No plaintext credential or unhashed capability is saved in queue metadata.
class PayloadCrypto
{
  public:
    explicit PayloadCrypto(std::string_view hexKey);
    std::string seal(std::string_view text, std::string_view purpose) const;
    std::string open(std::string_view envelope, std::string_view purpose) const;
    std::string digest(std::string_view domain, std::string_view text) const;
    static std::string randomToken();

  private:
    std::array<unsigned char, 32> key_;
};
}  // namespace chat
