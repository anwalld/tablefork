#include <drogon/drogon_test.h>

#include <stdexcept>

#include "services/table_templates.h"

using chat::TableTemplates;

namespace
{
Json::Value parse(const std::string &text)
{
    Json::Value root;
    Json::CharReaderBuilder builder;
    std::string errors;
    std::istringstream stream(text);
    Json::parseFromStream(builder, stream, &root, &errors);
    return root;
}

bool rejects(const std::string &text)
{
    try
    {
        TableTemplates::parse(parse(text));
        return false;
    }
    catch (const std::invalid_argument &)
    {
        return true;
    }
}
}  // namespace

DROGON_TEST(ShippedTableTemplatesLoad)
{
    // Run from the source root, like the password blocklist test.
    const auto templates = TableTemplates::loadFile("data/table-templates.json");
    REQUIRE(templates.size() == 3);
    CHECK(templates[0].id == "troubleshoot");
    CHECK(templates[0].ais.size() == 3);
    CHECK(templates[0].ais[1].name == "分析者");
    int chairs = 0;
    for (const auto &ai : templates[1].ais) chairs += ai.chair ? 1 : 0;
    CHECK(templates[1].council);
    CHECK(chairs == 3);
}

DROGON_TEST(TableTemplatesRejectBrokenEntries)
{
    const std::string ai = R"({"name":"甲","model":"deepseek","persona":"p"})";
    CHECK(!rejects(R"({"templates":[{"id":"t","name":"桌","ais":[)" + ai + "]}]}"));
    CHECK(rejects(R"({"templates":[{"id":"t","name":"桌","ais":[]}]})"));
    CHECK(rejects(R"({"templates":[{"id":"t","name":"桌","ais":[)" + ai + "," + ai + "]}]}"));
    CHECK(rejects(R"({"templates":[{"id":"t","name":"桌","council":true,"ais":[)" + ai + "]}]}"));
    CHECK(rejects(R"({"templates":[{"id":"t","name":"桌","ais":[{"name":"甲","model":"deepseek","persona":"p","chair":true}]}]})"));
    CHECK(rejects(R"({"templates":[{"id":"t","name":"桌","ais":[)" + ai + R"(]},{"id":"t","name":"桌","ais":[)" + ai + "]}]}"));
    CHECK(rejects(R"({"nothing":1})"));
}
