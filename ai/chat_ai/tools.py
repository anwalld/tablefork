"""Tools the AIs may call: a fixed registry of built-in tools and MCP servers.

Only what the deployment lists in tools.toml exists; nobody can add a tool at
runtime. Every tool is tagged "readonly" (runs at once) or "side_effect"
(runs only after a person with the right confirms, and is never retried).
MCP servers are reached with the official Python SDK, one short session per
call, so no connection state lives in the AI process.

tools.toml:

    [[builtin]]
    name = "current_time"
    risk = "readonly"

    [[mcp]]
    server = "files"                 # a label for the server
    command = "npx"                  # stdio transport ...
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    # url = "http://mcp:8000/mcp"    # ... or streamable HTTP
    [mcp.tools]                      # only these are exposed, each tagged
    read_file = "readonly"
    write_file = "side_effect"
"""

import asyncio
import json
import os
import tomllib
from dataclasses import dataclass, field

RISKS = ("readonly", "side_effect")

BUILTINS = {
    "current_time": ("当前日期和时间", {"type": "object", "properties": {}}),
    "search_messages": ("在本房间较早的聊天记录里按关键词搜索", {
        "type": "object", "properties": {"query": {"type": "string", "description": "关键词"}},
        "required": ["query"]}),
    "search_knowledge": ("在本房间导入的资料里按语义搜索，结果带资料名和位置", {
        "type": "object", "properties": {"query": {"type": "string", "description": "要找的内容"}},
        "required": ["query"]}),
}


class ToolError(Exception):
    """A tool ran and failed; the message goes back to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str
    kind: str  # "builtin" | "mcp"
    description: str = ""
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    server: dict = field(default_factory=dict)  # mcp: command/args/env or url


def load(path: str) -> dict[str, Tool]:
    """The registry; raises ValueError on anything ambiguous, so a bad file
    stops the service at start instead of exposing the wrong tool."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    registry: dict[str, Tool] = {}

    def add(tool: Tool):
        if tool.risk not in RISKS:
            raise ValueError(f"tool {tool.name}: risk must be readonly or side_effect")
        if tool.name in registry:
            raise ValueError(f"tool {tool.name} is registered twice")
        registry[tool.name] = tool

    for entry in data.get("builtin", []):
        name = entry.get("name", "")
        if name not in BUILTINS:
            raise ValueError(f"unknown built-in tool {name!r}")
        description, parameters = BUILTINS[name]
        add(Tool(name, entry.get("risk", ""), "builtin", description, parameters))
    for entry in data.get("mcp", []):
        server = {k: entry[k] for k in ("server", "command", "args", "env", "url") if k in entry}
        if not (server.get("command") or server.get("url")):
            raise ValueError(f"MCP server {entry.get('server', '?')} needs command or url")
        for name, risk in (entry.get("tools") or {}).items():
            add(Tool(name, risk, "mcp", server=server))
    return registry


def schemas(registry: dict[str, Tool]) -> list[dict]:
    return [{"name": t.name, "description": t.description or t.name, "parameters": t.parameters}
            for t in registry.values()]


def summarize(text: str, limit: int = 80) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def call_record(name: str, arguments: dict, result: str) -> str:
    """The timeline line for one call, kept in the AI's stored reply."""
    shown = summarize(json.dumps(arguments, ensure_ascii=False), 60) if arguments else ""
    return f"〔工具 {name}({shown}) → {summarize(result)}〕"


def _session_streams(server: dict):
    from mcp import StdioServerParameters
    if server.get("url"):
        from mcp.client.streamable_http import streamable_http_client
        return streamable_http_client(server["url"])
    from mcp.client.stdio import stdio_client
    return stdio_client(StdioServerParameters(
        command=server["command"], args=list(server.get("args", [])),
        env={**os.environ, **server.get("env", {})} if server.get("env") else None))


async def _with_session(server: dict, action, timeout: float):
    from mcp import ClientSession
    async with asyncio.timeout(timeout):
        async with _session_streams(server) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await action(session)


async def mcp_describe(registry: dict[str, Tool], timeout: float) -> dict[str, Tool]:
    """Fills in descriptions and parameter schemas from each MCP server.
    Registered tools a server does not offer are dropped."""
    result = {name: tool for name, tool in registry.items() if tool.kind != "mcp"}
    servers = {}
    for tool in registry.values():
        if tool.kind == "mcp":
            servers.setdefault(json.dumps(tool.server, sort_keys=True), []).append(tool)
    for key, wanted in servers.items():
        try:
            listed = await _with_session(json.loads(key), lambda s: s.list_tools(), timeout)
        except Exception:  # noqa: BLE001 - an unreachable server offers nothing
            continue
        offered = {t.name: t for t in listed.tools}
        for tool in wanted:
            remote = offered.get(tool.name)
            if remote is not None:
                result[tool.name] = Tool(tool.name, tool.risk, "mcp", remote.description or tool.name,
                                         remote.input_schema or tool.parameters, tool.server)
    return result


async def call_mcp(tool: Tool, arguments: dict, timeout: float) -> str:
    result = await _with_session(tool.server, lambda s: s.call_tool(tool.name, arguments), timeout)
    text = "\n".join(c.text for c in getattr(result, "content", []) if getattr(c, "text", None))
    if getattr(result, "is_error", False):
        raise ToolError(text or "工具返回错误")
    return text or json.dumps(getattr(result, "structured_content", None) or {}, ensure_ascii=False)
