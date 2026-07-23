"""
FastMCP server factory — 创建 MCP server 实例并注册全部工具。

对标 NyaaQiny-MCP src/server.ts 的 createMcpServer + TOOL_REGISTRY 模式。
"""

from mcp.server.fastmcp import FastMCP

SERVER_NAME = "nyaadiypc-mcp"
SERVER_VERSION = "0.1.0"

from app.mcp.tools import register_all_placeholder_tools


def create_mcp_server() -> FastMCP:
    """Create and configure the FastMCP server with all registered tools."""
    mcp = FastMCP(SERVER_NAME)

    # P1: 注册占位工具（P4-P5 替换为业务实现）
    register_all_placeholder_tools(mcp)

    return mcp
