"""REA MCP bridge: search / inspect / call the 207 rea-agents tools on demand.

The rea MCP server's tools/list payload is ~3.16MB, so mounting it as an MCP
server would flood every request. Instead this module exposes five small
meta-tools backed by a local SQLite FTS5 + BM25 index (see Tools/rea/rea_tools.py).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from Tools.registry import registry

_INDEX_SCRIPT = Path(__file__).resolve().parents[1] / "rea" / "rea_tools.py"


def _index() -> object:
    """Load the index/CLI module from Tools/rea/rea_tools.py."""
    spec = importlib.util.spec_from_file_location("rea_mcp_index", _INDEX_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load index module: {_INDEX_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ok(**payload: object) -> str:
    return json.dumps({"status": "success", **payload}, ensure_ascii=False)


def _err(exc: BaseException) -> str:
    return json.dumps(
        {"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
    )


def rea_search(query: str, limit: int = 8) -> str:
    """BM25 search over the rea tool catalog (Chinese queries are expanded)."""
    try:
        mod = _index()
        hits = mod.search(mod.DEFAULT_DB, str(query), max(1, int(limit)))
        return _ok(query=query, count=len(hits), tools=hits)
    except (Exception, SystemExit) as exc:
        return _err(exc)


def rea_schema(tool_name: str) -> str:
    """Return one rea tool's full inputSchema."""
    try:
        mod = _index()
        return _ok(tool=mod.get_schema(mod.DEFAULT_DB, str(tool_name)))
    except (Exception, SystemExit) as exc:
        return _err(exc)


def rea_call(
    tool_name: str,
    arguments: dict | None = None,
    timeout: float = 300.0,
    max_chars: int = 12000,
) -> str:
    """Forward a tools/call to the real rea MCP server over stdio.

    max_chars caps the returned payload (0 = unlimited); oversized payloads are
    returned as ``result_text`` plus a note instead of ``result``.
    """
    try:
        mod = _index()
        if arguments is None:
            args: dict = {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return _err(
                TypeError(
                    f"arguments must be a JSON object, got {type(arguments).__name__}"
                )
            )
        with mod.McpSession(mod.find_rea(), timeout=float(timeout)) as session:
            result = session.call_tool(str(tool_name), args)
        text = json.dumps(result, ensure_ascii=False)
        limit = int(max_chars)
        if limit > 0 and len(text) > limit:
            return _ok(
                tool=tool_name,
                truncated=True,
                result_chars=len(text),
                note=f"result truncated to {limit} chars; pass max_chars=0 for the full payload",
                result_text=text[:limit],
            )
        return _ok(tool=tool_name, truncated=False, result_chars=len(text), result=result)
    except (Exception, SystemExit) as exc:
        return _err(exc)


def rea_stats() -> str:
    """Index metadata: tool count, server version, index timestamp."""
    try:
        mod = _index()
        return _ok(**mod.stats(mod.DEFAULT_DB))
    except (Exception, SystemExit) as exc:
        return _err(exc)


def rea_index() -> str:
    """Rebuild the catalog index from the live rea MCP server."""
    try:
        mod = _index()
        return _ok(**mod.build_index(mod.DEFAULT_DB, verbose=False))
    except (Exception, SystemExit) as exc:
        return _err(exc)


registry.register(
    name="rea_search",
    description=(
        "Search the rea-agents reverse-engineering tool catalog (207 tools) by BM25. "
        "Use this FIRST to find the right tool; the catalog is too large to mount directly. "
        "Supports Chinese queries. Read-only, no side effects."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords, e.g. 'decompile native function' or '反编译 函数'.",
            },
            "limit": {"type": "integer", "description": "Max hits to return.", "default": 8},
        },
        "required": ["query"],
    },
    handler=rea_search,
    group="utility",
)

registry.register(
    name="rea_schema",
    description=(
        "Get the full inputSchema of one rea tool found via rea_search, "
        "so you can build correct arguments before calling rea_call. Read-only."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Exact rea tool name."},
        },
        "required": ["tool_name"],
    },
    handler=rea_schema,
    group="utility",
)

registry.register(
    name="rea_call",
    description=(
        "Execute one rea-agents reverse-engineering tool by forwarding tools/call to the "
        "live rea MCP server over stdio. Has side effects on the analysis target (may launch "
        "processes, read/write files, touch devices). Always call rea_schema first to learn "
        "required arguments. Output is capped by max_chars (default 12000)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Exact rea tool name."},
            "arguments": {
                "type": "object",
                "description": "Arguments object matching the tool's inputSchema.",
                "default": {},
            },
            "timeout": {
                "type": "number",
                "description": "Seconds to wait for the MCP response.",
                "default": 300,
            },
            "max_chars": {
                "type": "integer",
                "description": "Cap on returned payload size; 0 = unlimited.",
                "default": 12000,
            },
        },
        "required": ["tool_name"],
    },
    handler=rea_call,
    group="utility",
)

registry.register(
    name="rea_stats",
    description="Report the rea tool index status: tool count, server version, build time.",
    arguments_schema={"type": "object", "properties": {}},
    handler=rea_stats,
    group="utility",
)

registry.register(
    name="rea_index",
    description=(
        "Rebuild the rea tool catalog index from the live MCP server. Use only when the "
        "catalog changed (e.g. rea-agents was upgraded); takes a few seconds."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=rea_index,
    group="utility",
)
