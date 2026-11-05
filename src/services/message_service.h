#pragma once

#include <cstdint>
#include <drogon/utils/coroutine.h>
#include <json/json.h>
#include <optional>
#include <string>

#include "services/session_store.h"

namespace chat
{
struct StoredMessage
{
    Json::Value json;  // the message as clients see it
    int64_t roomId;
    int64_t seq;
    bool duplicate;
};

struct HistoryQuery
{
    std::optional<int64_t> before;
    std::optional<int64_t> after;
    int64_t limit;
};

// The single entry point for messages, whoever sends them.
class MessageService
{
  public:
    // Validates and durably stores a message. A repeated client_msg_id returns
    // the stored message with duplicate set.
    static drogon::Task<StoredMessage> save(const Session &session,
                                            int64_t roomId,
                                            std::string clientMsgId,
                                            std::string content);

    // Stores an AI's finished reply. The generation id is its client_msg_id,
    // so storing the same generation twice keeps one message. AI messages do
    // not count towards any user's rate limit.
    static drogon::Task<StoredMessage> saveAiMessage(int64_t aiMemberId,
                                                     int64_t roomId,
                                                     std::string generationId,
                                                     std::string content);

    // Updates the hot cache and publishes. The message is already durable, so
    // failures are only logged; clients backfill by seq.
    static drogon::Task<> distribute(const StoredMessage &message, RequestContextPtr context = {});

    static drogon::Task<Json::Value> history(Session session,
                                             int64_t roomId,
                                             HistoryQuery query);

    // Called after room deletion commits; a failed cleanup is explicitly
    // unknown and may be reconciled with the original business request ID.
    static drogon::Task<> dropCache(int64_t roomId, RequestContextPtr context = {});
};
}  // namespace chat
