"""
MCP 客户端管理器。

职责：
- 按 config.yaml 的 tools.mcp.servers 启动时连接（start）
- 运行时热添加/移除 server（connect/disconnect，可选持久化到 config.yaml）
- 把 MCP server 的工具桥接进 Tools.registry，命名 mcp__<server>__<tool>，
  对模型表现为普通工具（对齐 Claude Code 的命名规范）
- 调用失败时惰性重连一次（对齐 Claude Code：不在 onclose 里立即重连）

生命周期注意：所有 session 挂在调用 start/connect 时所在的 asyncio 事件循环上
（gateway 主循环）。handler 必须也在该循环上 await——registry 对 async
handler 的处理满足这一点。
"""

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack

from configurationLoader import config
from Tools.registry import registry

logger = logging.getLogger(__name__)

_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_]")


def _safe(part):
    return _NAME_SAFE_RE.sub("_", str(part)) or "x"


def _serialize_result(result):
    """MCP CallToolResult → 文本通道字符串（isError 时包成 error JSON）。"""
    parts = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        else:
            try:
                parts.append(json.dumps(block.model_dump(), ensure_ascii=False))
            except Exception:
                parts.append(str(block))
    text = "\n".join(p for p in parts if p).strip()
    if getattr(result, "isError", False):
        return json.dumps({"error": text or "MCP tool returned an error"}, ensure_ascii=False)
    return text or json.dumps({"result": "ok"}, ensure_ascii=False)


class _ServerConn:
    def __init__(self, name, spec):
        self.name = name
        self.spec = dict(spec)
        self.stack = None
        self.session = None
        self.tool_names = []
        self.status = "disconnected"
        self.error: str | None = None


class MCPClientManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._servers = {}
        self._runtime = None
        self._lock = asyncio.Lock()
        self._initialized = True

    def attach_runtime(self, runtime):
        """绑定 tool runtime，用于 server 增删后刷新 last_tool_definitions。"""
        self._runtime = runtime

    async def _refresh_tool_defs(self):
        if self._runtime is not None:
            try:
                await self._runtime.list_tools()
            except Exception as exc:
                logger.warning("MCP: refresh tool definitions failed: %s", exc)

    # ── 配置持久化 ──────────────────────────────────────────
    # 运行期增删的 server 写入独立 JSON 文件，绝不 config.save()——
    # yaml.dump 会剥掉 config.yaml 里的所有注释。

    _PERSIST_FILE = None

    @classmethod
    def _persist_path(cls):
        if cls._PERSIST_FILE is None:
            from pathlib import Path
            cls._PERSIST_FILE = Path("runtime_memory") / "mcp_servers.json"
        return cls._PERSIST_FILE

    @classmethod
    def _load_persisted(cls):
        try:
            data = json.loads(cls._persist_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _persist(self):
        specs = {name: conn.spec for name, conn in self._servers.items()}
        try:
            path = self._persist_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("MCP: persist %s failed: %s", self._persist_path(), exc)

    # ── 会话建立 / 拆除 ─────────────────────────────────────

    async def _open_session(self, conn):
        from mcp import ClientSession

        spec = conn.spec
        transport = str(spec.get("transport", "stdio")).strip().lower()
        stack = AsyncExitStack()
        try:
            if transport == "stdio":
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client

                env = spec.get("env") or None
                if env:
                    from mcp.client.stdio import get_default_environment
                    env = {**get_default_environment(), **env}
                params = StdioServerParameters(
                    command=spec["command"],
                    args=[str(a) for a in (spec.get("args") or [])],
                    env=env,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif transport in ("http", "streamable_http", "streamable-http"):
                from mcp.client.streamable_http import streamable_http_client

                http_client = None
                if spec.get("headers"):
                    import httpx
                    http_client = await stack.enter_async_context(
                        httpx.AsyncClient(headers=spec["headers"], follow_redirects=True)
                    )
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(spec["url"], http_client=http_client)
                )
            else:
                raise ValueError(f"Unsupported MCP transport: {transport} (supported: stdio, http)")

            conn.stack = stack
            conn.session = await stack.enter_async_context(ClientSession(read, write))
            await conn.session.initialize()
        except Exception:
            await stack.aclose()
            raise

        tools = await conn.session.list_tools()
        conn.tool_names = []
        for tool in tools.tools:
            full_name = f"mcp__{_safe(conn.name)}__{_safe(tool.name)}"
            registry.register(
                name=full_name,
                description=f"[MCP:{conn.name}] {tool.description or ''}".strip(),
                arguments_schema=dict(tool.inputSchema or {}),
                handler=self._make_handler(conn.name, tool.name),
                group="mcp",
                is_async=True,
            )
            conn.tool_names.append(full_name)
        logger.info("MCP server '%s' connected: %d tools", conn.name, len(conn.tool_names))

    async def _teardown(self, conn):
        for tool_name in conn.tool_names:
            registry.unregister(tool_name)
        conn.tool_names = []
        conn.session = None
        if conn.stack is not None:
            try:
                await conn.stack.aclose()
            except Exception as exc:
                logger.warning("MCP server '%s' close error: %s", conn.name, exc)
            conn.stack = None

    # ── 对外操作 ────────────────────────────────────────────

    async def start(self):
        """启动时连接所有 server（config.yaml 静态配置 + 运行期持久化文件，后者覆盖前者）。"""
        servers = {}
        static = config.get("tools.mcp.servers", None)
        if isinstance(static, dict):
            servers.update(static)
        servers.update(self._load_persisted())
        for name, spec in servers.items():
            try:
                await self.connect(name, spec, persist=False)
            except Exception as exc:
                logger.error("MCP server '%s' startup connect failed: %s", name, exc)

    async def connect(self, name, spec, persist=True):
        """连接（或重连）一个 server 并桥接其工具。返回注册的工具全名列表。"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("server name is required")
        async with self._lock:
            old = self._servers.pop(name, None)
            if old is not None:
                await self._teardown(old)

            conn = _ServerConn(name, spec)
            self._servers[name] = conn
            try:
                await self._open_session(conn)
                conn.status = "connected"
                conn.error = None
            except Exception as exc:
                conn.status = "failed"
                conn.error = f"{type(exc).__name__}: {exc}"
                if persist:
                    self._persist()
                raise

            if persist:
                self._persist()
            await self._refresh_tool_defs()
            return list(conn.tool_names)

    async def disconnect(self, name, persist=True):
        """移除 server：卸载其全部工具并关闭会话。返回是否存在该 server。"""
        async with self._lock:
            conn = self._servers.pop(str(name or "").strip(), None)
            if conn is None:
                return False
            await self._teardown(conn)
            if persist:
                self._persist()
            await self._refresh_tool_defs()
            return True

    def get_server_spec(self, name):
        """取某个 server 的配置（运行期持久化优先，回退 config.yaml 静态配置）。"""
        persisted = self._load_persisted()
        if name in persisted:
            return persisted[name]
        static = config.get("tools.mcp.servers", None)
        if isinstance(static, dict):
            return static.get(name)
        return None

    def list_servers(self):
        return {
            name: {
                "status": conn.status,
                "error": conn.error,
                "tools": list(conn.tool_names),
                "transport": conn.spec.get("transport", "stdio"),
            }
            for name, conn in self._servers.items()
        }

    # ── 工具调用路由 ────────────────────────────────────────

    def _make_handler(self, server_name, tool_name):
        async def _handler(**kwargs):
            return await self._call_tool(server_name, tool_name, kwargs)

        return _handler

    async def _call_tool(self, server_name, tool_name, arguments):
        conn = self._servers.get(server_name)
        if conn is None or conn.session is None:
            return json.dumps({"error": f"MCP server not connected: {server_name}"}, ensure_ascii=False)
        try:
            result = await conn.session.call_tool(tool_name, arguments)
        except Exception as exc:
            # 惰性重连一次后重试
            logger.warning(
                "MCP call %s::%s failed (%s), reconnecting...",
                server_name, tool_name, exc,
            )
            try:
                async with self._lock:
                    await self._teardown(conn)
                    await self._open_session(conn)
                    conn.status = "connected"
                    conn.error = None
                await self._refresh_tool_defs()
                result = await conn.session.call_tool(tool_name, arguments)
            except Exception as exc2:
                conn.status = "failed"
                conn.error = f"{type(exc2).__name__}: {exc2}"
                return json.dumps(
                    {"error": f"MCP tool call failed after reconnect: {type(exc2).__name__}: {exc2}"},
                    ensure_ascii=False,
                )
        return _serialize_result(result)

    async def close(self):
        """进程退出时关闭全部连接（不改动已持久化的配置）。"""
        async with self._lock:
            for conn in self._servers.values():
                await self._teardown(conn)
            self._servers.clear()


mcp_manager = MCPClientManager()
