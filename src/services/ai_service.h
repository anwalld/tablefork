#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <optional>
#include <string>
#include <vector>

#include "services/session_store.h"
#include "buffering/guarded_transaction.h"

namespace chat
{
// Preset AI members in rooms, and users' API keys as seen from the chat
// server: whether one exists, never the key itself.
class AiService
{
  public:
    // The preset AIs, each marked with whether the caller has its key.
    static drogon::Task<Json::Value> catalog(Session session);
    static drogon::Task<Json::Value> models(Session session);
    static drogon::Task<Json::Value> createRole(Session session, std::string model, std::string name);
    static drogon::Task<Json::Value> editPersona(Session session, int64_t roomId, int64_t memberId,
                                               std::string persona, int64_t version);
    static drogon::Task<> removeOwned(const GuardedTransactionPtr &tx, int64_t roomId, int64_t userId);

    struct RequestOutcome
    {
        bool joined;
        int64_t aiMemberId;
        // Owners and admins to notify of a new request.
        std::vector<int64_t> reviewers;
    };
    // Adds the AI at once for owners and admins, otherwise files a request.
    static drogon::Task<RequestOutcome> request(Session session,
                                                int64_t roomId,
                                                std::string slug, std::string persona = "");

    static drogon::Task<Json::Value> pendingRequests(Session session, int64_t roomId);

    struct Review
    {
        int64_t aiMemberId;
        int64_t requestedBy;
    };
    static drogon::Task<Review> review(Session session,
                                       int64_t roomId,
                                       std::string slug,
                                       bool approve);

    // Returns the AI's member id, or nothing when it was not in the room.
    static drogon::Task<std::optional<int64_t>> remove(Session session,
                                                       int64_t roomId,
                                                       std::string slug);

    static drogon::Task<Json::Value> keys(Session session);
    static drogon::Task<> deleteKey(Session session, std::string provider);
    static bool isProvider(const std::string &provider);
};
}  // namespace chat
