#include "services/password_policy.h"

#include <algorithm>
#include <fstream>
#include <stdexcept>

#include "common/config.h"
#include "common/errors.h"

namespace chat
{
namespace
{
// The list is ASCII, so only ASCII letters need folding.
std::string lowerAscii(std::string text)
{
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a')
                                    : static_cast<char>(c);
    });
    return text;
}
}  // namespace

PasswordPolicy::PasswordPolicy(std::unordered_set<std::string> blocklist)
    : blocklist_(std::move(blocklist))
{
}

PasswordPolicy PasswordPolicy::fromFile(const std::string &path)
{
    std::ifstream file(path);
    if (!file)
    {
        throw std::runtime_error("cannot read password blocklist " + path);
    }
    std::unordered_set<std::string> blocklist;
    std::string line;
    while (std::getline(file, line))
    {
        if (!line.empty() && line.back() == '\r')
        {
            line.pop_back();
        }
        if (!line.empty())
        {
            blocklist.insert(lowerAscii(line));
        }
    }
    return PasswordPolicy(std::move(blocklist));
}

void PasswordPolicy::check(const std::string &username,
                           const std::string &password) const
{
    const auto lowered = lowerAscii(password);
    if (lowered == lowerAscii(username))
    {
        throw ApiError::weakPassword("password must differ from the username");
    }
    if (blocklist_.contains(lowered))
    {
        throw ApiError::weakPassword("password is too common");
    }
}

const PasswordPolicy &PasswordPolicy::instance()
{
    static const PasswordPolicy policy =
        fromFile(Config::get().passwordBlocklistFile);
    return policy;
}
}  // namespace chat
