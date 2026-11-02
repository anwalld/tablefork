#pragma once

#include <string>
#include <unordered_set>

namespace chat
{
// Rules for new passwords beyond their length: not a common password and not
// the user name. Only registration applies them, so older accounts keep
// working.
class PasswordPolicy
{
  public:
    explicit PasswordPolicy(std::unordered_set<std::string> blocklist);

    // One lower-case password per line. Throws when the file cannot be read.
    static PasswordPolicy fromFile(const std::string &path);

    // Throws ApiError::weakPassword() when the password breaks a rule.
    void check(const std::string &username, const std::string &password) const;

    // The policy loaded from Config::passwordBlocklistFile, read at startup.
    static const PasswordPolicy &instance();

  private:
    std::unordered_set<std::string> blocklist_;
};
}  // namespace chat
