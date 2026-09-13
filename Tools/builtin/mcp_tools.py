"""
mcp_manage 工具 — 运行时热配置 MCP server（list / add / remove / refresh）。

用户在使用中说"接一个 MCP server"时，agent 调用本工具完成：
连接 → 工具桥接进工具表（mcp__<server>__<tool>）→ 持久化到 config.yaml。
"""
import json
import logging

from Tools.registry import registry

logger = logging.getLogger(__name__)


async def mcp_manage(
    action: str = "list",
    name: str = "",
    transport: str = "stdio",
    command: str = "",
    args: list | None = None,
    env: dict | None = None,
    url: str = "",
    headers: dict | None = None,
) -> str:
    from Tools.mcp.manager import mcp_manager

    action = str(action or "list").strip().lower()

    if action == "list":
        return json.dumps({"servers": mcp_manager.list_servers()}, ensure_ascii=False)

    name = str(name or "").strip()
    if not name:
        return json.dumps({"error": "name 参数不能为空"}, ensure_ascii=False)

    if action == "add":
        transport = str(transport or "stdio").strip().lower()
        if transport == "stdio":
            if not command:
                return json.dumps({"error": "stdio transport 需要 command 参数"}, ensure_ascii=False)
            spec = {"transport": "stdio", "command": command}
            if args:
                spec["args"] = args
            if env:
                spec["env"] = env
        elif transport in ("http", "streamable_http", "streamable-http"):
            if not url:
                return json.dumps({"error": "http transport 需要 url 参数"}, ensure_ascii=False)
            spec = {"transport": "http", "url": url}
            if headers:
                spec["headers"] = headers
        else:
            return json.dumps(
                {"error": f"不支持的 transport: {transport}（支持 stdio / http）"},
                ensure_ascii=False,
            )

        try:
            tools = await mcp_manager.connect(name, spec, persist=True)
        except Exception as exc:
            return json.dumps({
                "error": f"MCP server '{name}' 连接失败: {type(exc).__name__}: {exc}",
                "hint": "server 配置已保存，修复后可用 action=refresh 重连；或用 action=remove 移除",
            }, ensure_ascii=False)
        logger.info("MCP server added at runtime: %s (%d tools)", name, len(tools))
        return json.dumps({
            "status": "connected",
            "server": name,
            "tools": tools,
            "message": f"已连接并注册 {len(tools)} 个工具，配置已持久化到 runtime_memory/mcp_servers.json",
        }, ensure_ascii=False)

    if action == "remove":
        existed = await mcp_manager.disconnect(name, persist=True)
        if not existed:
            return json.dumps({"error": f"MCP server 不存在: {name}"}, ensure_ascii=False)
        logger.info("MCP server removed at runtime: %s", name)
        return json.dumps({"status": "removed", "server": name}, ensure_ascii=False)

    if action == "refresh":
        spec = mcp_manager.get_server_spec(name)
        if spec is None:
            return json.dumps({"error": f"MCP server 不存在: {name}"}, ensure_ascii=False)
        try:
            tools = await mcp_manager.connect(name, spec, persist=False)
        except Exception as exc:
            return json.dumps(
                {"error": f"重连失败: {type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        return json.dumps({"status": "connected", "server": name, "tools": tools}, ensure_ascii=False)

    return json.dumps(
        {"error": f"未知 action: {action}（支持 list / add / remove / refresh）"},
        ensure_ascii=False,
    )


registry.register(
    name="mcp_manage",
    description=(
        "Manage MCP (Model Context Protocol) servers at runtime: list / add / remove / refresh. "
        "Adding a server connects to it and registers its tools as mcp__<server>__<tool> — "
        "they become callable like built-in tools immediately (hot-load, no restart). "
        "Runtime changes persist to runtime_memory/mcp_servers.json (config.yaml is never rewritten). "
        "For stdio servers provide command + args (e.g. command='npx', args=['-y', '@modelcontextprotocol/server-filesystem', 'E:/']); "
        "on Windows prefer full paths or cmd /c wrappers. For HTTP servers provide url (+ optional headers)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list | add | remove | refresh", "default": "list"},
            "name": {"type": "string", "description": "Server name (required for add/remove/refresh)."},
            "transport": {"type": "string", "description": "stdio (default) or http.", "default": "stdio"},
            "command": {"type": "string", "description": "stdio: executable to launch the server."},
            "args": {"type": "array", "description": "stdio: command arguments.", "items": {"type": "string"}},
            "env": {"type": "object", "description": "stdio: extra environment variables."},
            "url": {"type": "string", "description": "http: server endpoint URL."},
            "headers": {"type": "object", "description": "http: extra request headers."},
        },
        "required": [],
    },
    handler=mcp_manage,
    group="mcp",
    is_async=True,
)
