#pragma once

#include <json/json.h>
#include <optional>
#include <string>
#include <vector>

namespace chat
{
// Table templates: a named group of AIs with their roles, launched into a
// room in one step (ChatDev's role-chain idea). Loaded from
// TABLE_TEMPLATES_FILE (data/table-templates.json).
struct TemplateAi
{
    std::string name;
    std::string model;  // an ai_model_types.model_slug
    std::string persona;
    bool chair = false;  // joins the council
};

struct TableTemplate
{
    std::string id;
    std::string name;
    std::string description;
    bool council = false;
    std::vector<TemplateAi> ais;
};

class TableTemplates
{
  public:
    // Validates the whole file; throws std::invalid_argument naming the
    // first problem, so a broken file is caught at start.
    static std::vector<TableTemplate> parse(const Json::Value &root);
    static std::vector<TableTemplate> loadFile(const std::string &path);
    // The deployment's templates, loaded once.
    static const std::vector<TableTemplate> &all();
    static std::optional<TableTemplate> find(const std::string &id);
    static Json::Value toJson(const TableTemplate &table);
};
}  // namespace chat
