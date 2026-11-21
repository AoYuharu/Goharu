#!/usr/bin/env python3
"""REA MCP tool index: SQLite FTS5 + BM25 retrieval + stdio call forwarding.

Usage:
  python rea_tools.py index
  python rea_tools.py search "decompile native function" -n 8
  python rea_tools.py schema analyze_function
  python rea_tools.py call binary_session '{"action":"status"}'
  python rea_tools.py stats
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("REA_TOOL_DB", r"E:/Goharu/runtime_memory/rea_tools.db"))

# BM25 column weights: name, description, params
WEIGHTS = (10.0, 4.0, 1.0)

# Minimal Chinese -> English expansion so Chinese queries hit the English catalog.
ZH_SYNONYMS = {
    "反编译": "decompile pseudocode",
    "函数": "function procedure",
    "过程": "procedure",
    "字符串": "string",
    "符号": "symbol",
    "汇编": "assembly instruction",
    "指令": "instruction",
    "调用": "call",
    "引用": "xref reference",
    "交叉引用": "xref",
    "类型": "type data",
    "二进制": "binary",
    "文件": "file artifact",
    "安卓": "android apk",
    "清单": "manifest inventory",
    "权限": "permission",
    "苹果": "apple macos",
    "浏览器": "browser web",
    "网页": "web page",
    "截图": "screenshot capture",
    "对比": "compare diff",
    "比较": "compare",
    "抓包": "capture network",
    "网络": "network",
    "数据库": "database sqlite",
    "会话": "session",
    "进程": "process",
    "固件": "firmware",
    "调试": "debug gdb",
    "断点": "breakpoint",
    "内存": "memory",
    "分析": "analysis inspect",
    "打开": "open",
    "关闭": "close",
    "列出": "list",
    "搜索": "search",
    "导出": "export",
    "证据": "evidence",
    "安卓资源": "apktool resources",
    "漏洞": "vulnerability",
    "加密": "encrypt crypto",
    "脚本": "script javascript",
    "运行时": "runtime",
    "代码": "code",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_rea() -> list[str]:
    """Return a command prefix that launches the rea CLI."""
    exe = shutil.which("rea")
    if exe:
        if exe.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", exe]
        return [exe]
    try:
        root = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:
        root = ""
    candidate = Path(root) / "rea-agents" / "scripts" / "rea.mjs"
    if candidate.exists():
        return ["node", str(candidate)]
    raise SystemExit("rea CLI not found. Run: npm install -g rea-agents")


class McpSession:
    """Minimal MCP stdio client for the rea server."""

    def __init__(self, cmd: list[str], timeout: float = 120.0):
        self.cmd = cmd + ["mcp"]
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self._stderr: list[str] = []
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stdout_reader: threading.Thread | None = None
        self._next_id = 1

    def __enter__(self) -> "McpSession":
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._stdout_reader = threading.Thread(target=self._drain_stdout, daemon=True)
        self._stdout_reader.start()
        init = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "rea-tool-index", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized", {})
        self.server_info = init.get("result", {}).get("serverInfo", {})
        return self

    def __exit__(self, *exc) -> None:
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _drain_stderr(self) -> None:
        try:
            for line in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr.append(line)
                if len(self._stderr) > 200:
                    del self._stderr[:100]
        except Exception:
            pass

    def _drain_stdout(self) -> None:
        """Feed stdout lines into a queue so readers can apply a real deadline."""
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                self._lines.put(line)
        except Exception:
            pass
        finally:
            self._lines.put(None)  # EOF sentinel

    def _write(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict) -> dict:
        req_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.time() + self.timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise SystemExit(f"timeout waiting for {method}")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise SystemExit(f"timeout waiting for {method}")
            if line is None:
                raise SystemExit(f"rea MCP server closed during {method}")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log(f"non-JSON from server: {line[:200]}")
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise SystemExit(f"MCP error on {method}: {json.dumps(msg['error'])[:800]}")
                return msg
        raise SystemExit(f"timeout waiting for {method}")

    def list_tools(self) -> list[dict]:
        return self.request("tools/list", {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------

def _param_text(schema: dict) -> str:
    """Flatten parameter names, types and descriptions into searchable text."""
    chunks: list[str] = []
    props = schema.get("properties") or {}
    for key, value in props.items():
        if not isinstance(value, dict):
            chunks.append(key)
            continue
        bits = [key]
        desc = value.get("description")
        if desc:
            bits.append(str(desc))
        typ = value.get("type")
        if isinstance(typ, list):
            bits.append(" ".join(map(str, typ)))
        elif typ:
            bits.append(str(typ))
        enum = value.get("enum")
        if enum:
            bits.append(" ".join(map(str, enum[:20])))
        chunks.append(" ".join(bits))
    required = schema.get("required") or []
    if required:
        chunks.append("required " + " ".join(map(str, required)))
    return "\n".join(chunks)


def build_index(db_path: Path, verbose: bool = True) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = find_rea()
    if verbose:
        log(f"launching: {' '.join(cmd)} mcp")
    with McpSession(cmd) as session:
        tools = session.list_tools()
        server_info = getattr(session, "server_info", {})

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS tools;
            DROP TABLE IF EXISTS tools_fts;
            CREATE TABLE tools (
                rowid INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                title TEXT,
                description TEXT,
                input_schema TEXT,
                required_json TEXT
            );
            CREATE VIRTUAL TABLE tools_fts USING fts5(
                name, description, params,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        for idx, tool in enumerate(tools, start=1):
            name = tool.get("name", "")
            desc = tool.get("description") or ""
            schema = tool.get("inputSchema") or {}
            conn.execute(
                "INSERT INTO tools(rowid, name, title, description, input_schema, required_json)"
                " VALUES (?,?,?,?,?,?)",
                (
                    idx,
                    name,
                    tool.get("title") or "",
                    desc,
                    json.dumps(schema, ensure_ascii=False),
                    json.dumps(schema.get("required") or []),
                ),
            )
            conn.execute(
                "INSERT INTO tools_fts(rowid, name, description, params) VALUES (?,?,?,?)",
                (idx, name.replace("_", " "), desc, _param_text(schema)),
            )
        conn.commit()
        meta = {
            "db": str(db_path),
            "server": server_info,
            "tool_count": len(tools),
            "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        for key, value in meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()
    return meta


# --------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"index missing: {db_path}\nRun: python {Path(__file__).name} index")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: OR of quoted tokens.

    Chinese tokens are expanded through ZH_SYNONYMS; ASCII tokens pass through.
    Returns None when the text has no searchable token (punctuation only), so the
    caller can answer with an empty result set instead of failing.
    """
    expanded: list[str] = []
    for token in text.split():
        if any("\u4e00" <= ch <= "\u9fff" for ch in token):
            expanded.extend(ZH_SYNONYMS.get(token, "").split())
        else:
            expanded.append(token)
    tokens = [
        t
        for t in "".join(c if c.isalnum() else " " for c in " ".join(expanded)).split()
        if t
    ]
    if not tokens:
        # No searchable token left. Blank input is a usage error; punctuation-only
        # input simply has nothing to match.
        raw = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if t]
        if not raw:
            if text.strip():
                return None
            raise SystemExit("empty query")
        tokens = raw
    return " OR ".join(f'"{t}"' for t in tokens)


def search(db_path: Path, query: str, limit: int = 8) -> list[dict]:
    conn = _connect(db_path)
    try:
        fts_query = _fts_query(query)
        if fts_query is None:
            return []
        sql = (
            "SELECT t.name, t.description, t.required_json,"
            f" bm25(tools_fts, {WEIGHTS[0]}, {WEIGHTS[1]}, {WEIGHTS[2]}) AS score"
            " FROM tools_fts JOIN tools t ON t.rowid = tools_fts.rowid"
            " WHERE tools_fts MATCH ? ORDER BY score LIMIT ?"
        )
        rows = conn.execute(sql, (fts_query, limit)).fetchall()
        out = []
        for row in rows:
            desc = " ".join((row["description"] or "").split())
            out.append(
                {
                    "name": row["name"],
                    "score": round(row["score"], 4),
                    "required": json.loads(row["required_json"] or "[]"),
                    "description": desc[:300] + ("..." if len(desc) > 300 else ""),
                }
            )
        return out
    finally:
        conn.close()


def get_schema(db_path: Path, name: str) -> dict:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT name, description, input_schema FROM tools WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            raise SystemExit(f"unknown tool: {name}")
        return {
            "name": row["name"],
            "description": row["description"],
            "inputSchema": json.loads(row["input_schema"]),
        }
    finally:
        conn.close()


def stats(db_path: Path) -> dict:
    conn = _connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
        meta = {
            row["key"]: json.loads(row["value"])
            for row in conn.execute("SELECT key, value FROM meta")
        }
        meta["tool_count"] = count
        return meta
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="REA tool index (FTS5 + BM25)")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="rebuild the index from rea mcp tools/list")
    p_search = sub.add_parser("search", help="BM25 search")
    p_search.add_argument("query")
    p_search.add_argument("-n", "--limit", type=int, default=8)
    p_schema = sub.add_parser("schema", help="print one tool's input schema")
    p_schema.add_argument("name")
    p_call = sub.add_parser("call", help="call a tool through rea mcp")
    p_call.add_argument("name")
    p_call.add_argument("arguments", nargs="?", default="{}")
    p_call.add_argument("--timeout", type=float, default=300.0)
    sub.add_parser("stats", help="index metadata")

    args = parser.parse_args(argv)
    db_path = Path(args.db)

    if args.command == "index":
        print(json.dumps(build_index(db_path), ensure_ascii=False, indent=2))
        return 0

    if args.command == "search":
        for item in search(db_path, args.query, args.limit):
            req = ",".join(item["required"]) or "-"
            print(f"{abs(item['score']):>8.2f}  {item['name']:<34} req[{req}]  {item['description']}")
        return 0

    if args.command == "schema":
        print(json.dumps(get_schema(db_path, args.name), ensure_ascii=False, indent=2))
        return 0

    if args.command == "call":
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON arguments: {exc}")
        with McpSession(find_rea(), timeout=args.timeout) as session:
            result = session.call_tool(args.name, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "stats":
        print(json.dumps(stats(db_path), ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
