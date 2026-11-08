"""A tiny stdio MCP server for tests: echo (read-only), slow, and
send_email (a side effect that appends to the file in $MCP_LOG)."""

import asyncio
import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("test-tools")


@server.tool()
def echo(text: str) -> str:
    """Repeats the text."""
    return f"echo:{text}"


@server.tool()
async def slow() -> str:
    """Takes a long time."""
    await asyncio.sleep(30)
    return "late"


@server.tool()
def send_email(to: str) -> str:
    """Sends an email (writes a line to the log file)."""
    with open(os.environ["MCP_LOG"], "a") as log:
        log.write(to + "\n")
    return f"sent to {to}"


if __name__ == "__main__":
    server.run()
