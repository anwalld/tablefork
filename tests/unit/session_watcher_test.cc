#include <drogon/drogon_test.h>

#include "ws/session_watcher.h"

using chat::keyBatches;

DROGON_TEST(KeyBatchesDropDuplicates)
{
    const auto batches = keyBatches({"session:b", "session:a", "session:b"}, 500);
    REQUIRE(batches.size() == 1);
    CHECK(batches[0] == std::vector<std::string>({"session:a", "session:b"}));
}

DROGON_TEST(KeyBatchesSplitAtTheSize)
{
    std::vector<std::string> keys;
    for (int i = 0; i < 5; ++i)
    {
        keys.push_back("session:" + std::to_string(i));
    }
    const auto batches = keyBatches(keys, 2);
    REQUIRE(batches.size() == 3);
    CHECK(batches[0].size() == 2);
    CHECK(batches[2].size() == 1);
}

DROGON_TEST(KeyBatchesOfNothing)
{
    CHECK(keyBatches({}, 500).empty());
}
