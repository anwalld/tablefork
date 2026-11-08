#include "services/table_templates.h"

#include <cstdlib>
#include <fstream>
#include <set>
#include <stdexcept>

#include "common/util.h"

namespace chat
{
namespace
{
std::string text(const Json::Value &value, const char *field, std::size_t maxLength, const std::string &where)
{
    if (!value[field].isString()) throw std::invalid_argument(where + ": " + field + " must be text");
    const auto result = value[field].asString();
    const auto length = utf8Length(result);
    if (!length || *length == 0 || *length > maxLength)
        throw std::invalid_argument(where + ": " + field + " must be 1 to " + std::to_string(maxLength) + " characters");
    return result;
}
}  // namespace

std::vector<TableTemplate> TableTemplates::parse(const Json::Value &root)
{
    if (!root["templates"].isArray()) throw std::invalid_argument("templates must be a list");
    std::vector<TableTemplate> result;
    std::set<std::string> ids;
    for (const auto &item : root["templates"])
    {
        TableTemplate table;
        table.id = text(item, "id", 32, "template");
        const auto where = "template " + table.id;
        if (!ids.insert(table.id).second) throw std::invalid_argument(where + ": duplicate id");
        table.name = text(item, "name", 32, where);
        table.description = item["description"].isString() ? item["description"].asString() : "";
        table.council = item["council"].asBool();
        if (!item["ais"].isArray() || item["ais"].empty()) throw std::invalid_argument(where + ": ais required");
        std::set<std::string> names;
        int chairs = 0;
        for (const auto &ai : item["ais"])
        {
            TemplateAi member;
            member.name = text(ai, "name", 64, where);
            member.model = text(ai, "model", 32, where);
            member.persona = text(ai, "persona", 16384, where);
            member.chair = ai["chair"].asBool();
            if (!names.insert(member.name).second) throw std::invalid_argument(where + ": duplicate AI name " + member.name);
            chairs += member.chair ? 1 : 0;
            table.ais.push_back(std::move(member));
        }
        // A council needs at least three valid votes to decide anything.
        if (table.council && chairs < 3) throw std::invalid_argument(where + ": a council needs at least 3 chairs");
        if (!table.council && chairs > 0) throw std::invalid_argument(where + ": chairs without a council");
        result.push_back(std::move(table));
    }
    return result;
}

std::vector<TableTemplate> TableTemplates::loadFile(const std::string &path)
{
    std::ifstream file(path);
    if (!file) throw std::invalid_argument("cannot read " + path);
    Json::Value root;
    Json::CharReaderBuilder builder;
    std::string errors;
    if (!Json::parseFromStream(builder, file, &root, &errors)) throw std::invalid_argument(path + ": " + errors);
    return parse(root);
}

const std::vector<TableTemplate> &TableTemplates::all()
{
    static const std::vector<TableTemplate> templates = [] {
        const char *path = std::getenv("TABLE_TEMPLATES_FILE");
        return loadFile(path && *path ? path : "/usr/local/share/chat/table-templates.json");
    }();
    return templates;
}

std::optional<TableTemplate> TableTemplates::find(const std::string &id)
{
    for (const auto &table : all())
        if (table.id == id) return table;
    return std::nullopt;
}

Json::Value TableTemplates::toJson(const TableTemplate &table)
{
    Json::Value result;
    result["id"] = table.id;
    result["name"] = table.name;
    result["description"] = table.description;
    result["council"] = table.council;
    result["ais"] = Json::arrayValue;
    for (const auto &ai : table.ais)
    {
        Json::Value item;
        item["name"] = ai.name;
        item["model"] = ai.model;
        item["chair"] = ai.chair;
        result["ais"].append(item);
    }
    return result;
}
}  // namespace chat
