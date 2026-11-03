#pragma once

#include "ai.pb.h"

namespace chat
{
// Carries out what the AI service sends: streamed text goes to the room as
// is, finished replies are stored like any message, and notices reach only
// the member they are for. Runs on a Drogon event loop.
void handleAiAction(ai::AiAction action);
}  // namespace chat
