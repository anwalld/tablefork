#include <drogon/drogon_test.h>
#include "services/room_service.h"

DROGON_TEST(RoomManagementPermissionMatrix)
{
    const std::string_view roles[] = {"owner", "admin", "member", "none"};
    const bool allowed[4][4] = {
        {false, true, true, true},
        {false, false, true, true},
        {false, false, false, false},
        {false, false, false, false},
    };
    for (size_t actor = 0; actor < 4; ++actor)
    {
        for (size_t target = 0; target < 4; ++target)
        {
            CHECK(chat::RoomService::canManage(roles[actor], roles[target]) == allowed[actor][target]);
        }
    }
    CHECK(!chat::RoomService::canManage("unknown", "member"));
    CHECK(!chat::RoomService::canManage("owner", "unknown"));
}
