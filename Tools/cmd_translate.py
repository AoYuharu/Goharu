"""跨平台命令转译层（run_cmd 兜底）。

run_cmd 现在统一走 bash（Windows 上用 Git Bash），模型偶尔残留 Windows cmd 习惯
（如 `dir`/`copy`/`del`）。本模块在执行前把常见的 Windows 命令转译为 POSIX 等价命令，
让"意图"在 bash 下正确执行，而不是报 command not found。

设计原则：
- 只转译常见、语义明确的命令（保守表），拿不准的原样放行
- 被安全策略或专用工具策略拦截的命令（type/cat 等）不做转译
- 复合命令（&&、||、;、| 分隔）逐段转译，引号内内容不动
"""

import re
import shlex
from typing import List, Optional, Tuple


# ── 复合命令切分（引号感知） ──────────────────────────────────

def _split_segments(cmd: str) -> List[str]:
    segs, buf, quote, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("&&", "||"):
            segs.append("".join(buf))
            segs.append(two)
            buf = []
            i += 2
            continue
        if ch in ";|":
            segs.append("".join(buf))
            segs.append(ch)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segs.append("".join(buf))
    return segs


def _tokens(seg: str) -> List[str]:
    try:
        return shlex.split(seg, posix=False)
    except ValueError:
        return seg.split()


def _join(tokens: List[str]) -> str:
    return " ".join(tokens)


# ── Windows → POSIX ──────────────────────────────────────────

def _w2u_dir(t: List[str]) -> str:
    out = ["ls"]
    if any(a.lower() == "/a" for a in t[1:]):
        out.append("-a")
    if any(a.lower() == "/s" for a in t[1:]):
        out.append("-R")
    if any(a.lower() == "/b" for a in t[1:]):
        out.append("-1")
    return _join(out + [a for a in t[1:] if not a.startswith("/")])


def _w2u_taskkill(t: List[str]) -> str:
    pid = None
    for i, a in enumerate(t):
        if a.upper() == "/PID" and i + 1 < len(t):
            pid = t[i + 1]
    if pid is None:
        return _join(t)
    return f"kill -9 {pid}" if any(a.upper() == "/F" for a in t) else f"kill {pid}"


def _w2u_del(t: List[str]) -> Optional[str]:
    if any(a.lower() == "/s" for a in t[1:]):
        return None  # del /s 不转译（危险区）
    force = any(a.lower() == "/f" for a in t[1:])
    files = [a for a in t[1:] if not a.startswith("/")]
    return _join(["rm"] + (["-f"] if force else []) + files)


def _w2u_set(t: List[str]) -> str:
    # `set K=V`（赋值）→ export K=V；裸 `set`（列环境）→ env
    if len(t) > 1 and "=" in t[1]:
        return _join(["export"] + t[1:])
    return "env"


_WINDOWS_TO_UNIX = {
    "dir": _w2u_dir,
    "cls": lambda t: "clear",
    "where": lambda t: _join(["which"] + t[1:]),
    "tasklist": lambda t: "ps aux",
    "taskkill": _w2u_taskkill,
    "copy": lambda t: _join(["cp"] + t[1:]),
    "move": lambda t: _join(["mv"] + t[1:]),
    "del": _w2u_del,
    "ren": lambda t: _join(["mv"] + t[1:]),
    "rename": lambda t: _join(["mv"] + t[1:]),
    "set": _w2u_set,
    "ver": lambda t: "uname -a",
    "type": lambda t: None,  # 读文件交给 Read 工具策略处理，不转译
}

_ENV_TO_POSIX = re.compile(r"%(\w+)%")


def translate_command(cmd: str) -> Tuple[str, Optional[str]]:
    """把模型残留的 Windows cmd 命令转译为 POSIX（run_cmd 统一走 bash）。

    Returns:
        (translated_cmd, original_cmd | None)——发生转译时第二项为原命令，否则为 None
    """
    table = _WINDOWS_TO_UNIX

    segs = _split_segments(cmd)
    changed = False
    out: List[str] = []
    for seg in segs:
        if seg in ("&&", "||", ";", "|"):
            out.append(seg)
            continue
        toks = _tokens(seg)
        new_seg = None
        if toks:
            handler = table.get(toks[0].lower())
            if handler:
                new_seg = handler(toks)
        if new_seg and new_seg != seg.strip():
            changed = True
            lead = " " if seg[:1].isspace() else ""
            trail = " " if seg[-1:].isspace() else ""
            out.append(f"{lead}{new_seg}{trail}")
        else:
            out.append(seg)

    translated = "".join(out)
    if changed:
        # 环境变量形态同步转译：%VAR% → $VAR，nul → /dev/null
        translated = _ENV_TO_POSIX.sub(r"$\1", translated)
        translated = translated.replace("nul", "/dev/null")
    return translated, (cmd if changed else None)
