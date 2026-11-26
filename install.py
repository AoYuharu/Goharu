#!/usr/bin/env python3
"""Goharu 交互式安装向导（仅标准库，跨 Windows / Linux / macOS）。

交互形态（TTY）：**多栏配置表单**——←→ 切换栏（模型 / 检索 / 运行 / 通道 / 高级 / 执行），
↑↓ 选栏内条目，Enter 切换布尔与选项、或在框内展开输入框（密钥遮罩、Esc 取消）；
最后一栏「执行」里选「开始安装」。非 TTY（脚本/CI/管道）自动退化为顺序问答。

引导流程：
    1. 环境探测（Python / Git / Node / npm）
    2. 多栏表单：模型厂商 + API Key（必填，联网验证通过才放行）、检索模式、
       运行方式、QQ Bot / HaruNet / 代理 / Arch 初始探索方向
    3. 创建 venv + 安装 Python 依赖
    4. 构建 TUI（npm install + tsc）
    5. 写入 .env 与 config.yaml
    6. 完成 → 可选立即启动

幂等：可重复执行，已完成的步骤自动跳过，上次装的厂商 / 模型 / 密钥自动带回表单。
无任何第三方依赖，因此可在 venv 建好之前直接以系统 Python 运行（bootstrap 入口即此）。
"""
from __future__ import annotations

import getpass
import http.client
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request

IS_WIN = os.name == "nt"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PIP_MIRROR = os.environ.get("PIP_MIRROR", "")
# pip 镜像回退链：单源失败自动换下一个。PIP_MIRROR 显式指定时只用该源。
PIP_MIRRORS = [PIP_MIRROR] if PIP_MIRROR else [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://pypi.mirrors.ustc.edu.cn/simple/",
]

def _install_draft_path() -> str:
    return os.environ.get("GOHARU_INSTALL_DRAFT") or os.path.join(
        os.path.expanduser("~"), ".goharu", "install_draft.json")



def _load_error_rules():
    """按文件路径加载纯标准库的 Agent/providers/errors.py。

    不走 `from Agent.providers import ...`：那会执行包 __init__（以后若混入 SDK 依赖，
    向导在 venv 建好之前就会崩）。安装时的密钥验证与运行期报错共用同一套归类规则。
    """
    spec = importlib.util.spec_from_file_location(
        "goharu_llm_errors", os.path.join(REPO_DIR, "Agent", "providers", "errors.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_error_rules = _load_error_rules()
classify_failure = _error_rules.classify_failure
failure_message = _error_rules.failure_message

# ── 模型厂商表（与 config.yaml 的 model_history 对齐）────────────────
PROVIDERS = [
    {
        "label": "DeepSeek",
        "model": "deepseek-flash",
        "models": ["deepseek-flash", "deepseek-v4-pro", "deepseek-chat"],
        "base_url": "https://api.deepseek.com/anthropic",
        "env": "ZHIPU_API_KEY",
        "hint": "国内直连，便宜快速",
    },
    {
        "label": "Kimi",
        "model": "kimi-k3",
        "models": ["kimi-k3", "kimi-k3.1-pro", "kimi-k2"],
        "base_url": "https://api.kimi.com/coding/",
        "env": "ZHIPU_API_KEY",
        "hint": "Moonshot coding 端点",
    },
    {
        "label": "智谱 GLM",
        "model": "glm-5.3-Flash",
        "models": ["glm-5.3-Flash", "glm-4.6", "glm-4.5"],
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "env": "ZHIPU_API_KEY",
        "hint": "智谱 Anthropic 兼容端点",
    },
    {
        "label": "MiniMax",
        "model": "MiniMax-M2.7",
        "models": ["MiniMax-M2.7", "MiniMax-M2"],
        "base_url": "https://api.minimaxi.com/anthropic",
        "env": "ANTHROPIC_API_KEY",
        "hint": "MiniMax 官方 Anthropic 兼容端点",
    },
    {
        "label": "自定义",
        "model": "",
        "models": [],
        "base_url": "",
        "env": "ANTHROPIC_API_KEY",
        "hint": "自行填写 base_url 与模型名",
    },
]
PROVIDER_LABELS = [p["label"] for p in PROVIDERS]


# ── ANSI / 输出 ──────────────────────────────────────────────────────
def _setup_console() -> None:
    """Windows 控制台切 UTF-8（CP65001）+ 启用 VT 转义 + stdout/stderr 重配 utf-8。
    保证中文与非 GBK 符号（✓/✗ 等）不崩溃；老旧终端则降级为 ? 而非报错。"""
    if IS_WIN:
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetConsoleOutputCP(65001)
            k.SetConsoleCP(65001)
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console()

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "reverse": "\033[7m",
}
if os.environ.get("NO_COLOR"):
    _ANSI = {k: "" for k in _ANSI}


def c(color: str, text: str) -> str:
    return f"{_ANSI.get(color, '')}{text}{_ANSI['reset']}"


def info(msg: str) -> None:
    print(f"{c('green', '[+]')} {msg}")


def warn(msg: str) -> None:
    print(f"{c('yellow', '[!]')} {msg}")


def err(msg: str) -> None:
    print(f"{c('red', '[x]')} {msg}")


def title(msg: str) -> None:
    print()
    print(c("bold", c("cyan", f"== {msg} ==")))


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{c('cyan', '?')} {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return raw or default


def confirm(msg: str, default: bool = True) -> bool:
    mark = "Y/n" if default else "y/N"
    raw = ask(f"{msg} ({mark})")
    if not raw:
        return default
    return raw.lower() in ("y", "yes", "1", "true")


def ask_secret(prompt: str) -> str:
    if not sys.stdin.isatty():
        # 非 TTY（管道/重定向/脚本化）：getpass 在 Windows 会读真实控制台而挂住，退化为明文输入
        try:
            return input(f"{c('cyan', '?')} {prompt}（明文回显，可回车跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
    try:
        return getpass.getpass(f"{c('cyan', '?')} {prompt}（输入不可见，可回车跳过）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


# ── 键盘读取（跨平台，归一化为方向/编辑键名或单个字符）──────────────────
_KEY_NAMES = {
    "\r": "enter", "\n": "enter", "\t": "right", "\x03": "ctrl-c",
    "\x08": "backspace", "\x7f": "backspace", "\x15": "ctrl-u",
}


def _read_key() -> str:
    """读一个按键 → up/down/left/right/home/end/delete/enter/escape/ctrl-c/ctrl-u/
    backspace，或单个可打印字符（未知特殊键返回空串）。"""
    if IS_WIN:
        import msvcrt

        k = msvcrt.getwch()
        if k in ("\x00", "\xe0"):  # 特殊键前缀
            k2 = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right",
                    "G": "home", "O": "end", "S": "delete"}.get(k2, "")
        return "escape" if k == "\x1b" else _KEY_NAMES.get(k, k)
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # 转义序列（方向键 / Home / End / Delete）；等不到后续字节 = 单独的 Esc
            return _rest_of_escape(fd, old)
        return _KEY_NAMES.get(ch, ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _rest_of_escape(fd, old) -> str:
    """读完 Esc 之后的转义序列。后续字节最多等 0.1s（VMIN=0 / VTIME=1）。"""
    import termios

    timed = list(old)
    timed[6][termios.VMIN] = 0
    timed[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, timed)

    def more() -> str:
        return sys.stdin.read(1) or ""

    nxt = more()
    if nxt == "O":  # SS3 序列：Home / End
        return {"H": "home", "F": "end"}.get(more(), "")
    if nxt != "[":
        return "escape"
    params = ""
    while len(params) <= 8:
        ch = more()
        if ch in "HF":
            return {"H": "home", "F": "end"}[ch]
        if ch == "~":
            return {"3": "delete", "1": "home", "4": "end", "7": "home", "8": "end"}.get(params, "")
        if ch in "ABCD":
            return {"A": "up", "B": "down", "C": "right", "D": "left"}[ch]
        params += ch
    return ""


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose(msg: str, options: list[str], default: int = 0) -> int:
    """选择菜单：TTY 用方向键，非 TTY（脚本/CI/管道）退回数字输入。"""
    if not _is_tty():
        print(f"{c('cyan', '?')} {msg}")
        for i, opt in enumerate(options, 1):
            marker = ">" if i - 1 == default else " "
            print(f"  {c('green', marker)} {c('bold', str(i))}. {opt}")
        while True:
            raw = ask(f"选择 (1-{len(options)})", str(default + 1))
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            err("无效选择，请重试")
    print(f"{c('cyan', '?')} {msg}  {c('dim', '（↑↓ 选择，回车确认）')}")
    n = len(options)
    idx = default
    for _ in options:
        print()  # 占位行

    def render() -> None:
        sys.stdout.write(f"\x1b[{n}A")
        for i, opt in enumerate(options):
            sel = i == idx
            marker = c("green", "❯") if sel else " "
            line = f"{marker} {c('bold', opt) if sel else opt}"
            sys.stdout.write("\x1b[2K" + line + "\n")
        sys.stdout.flush()

    render()
    while True:
        k = _read_key()
        if k == "up":
            idx = (idx - 1) % n
        elif k == "down":
            idx = (idx + 1) % n
        elif k == "enter":
            break
        elif k == "ctrl-c":
            print()
            raise SystemExit(130)
        render()
    return idx


# ── 多栏配置表单 ────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LABEL_W = 24                       # 条目名列宽
EMBED_WARNING = "警告:这会安装一段时间，慎重考虑，推荐不安装"
KEYS_HELP = "←→ 切换栏 · ↑↓ 选择 · Enter 编辑/切换 · Ctrl+C 退出"

Seg = tuple[str, str]              # (文本, 颜色)；颜色 "" = 终端默认


def _plain(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _dwidth(s: str) -> int:
    """显示宽度（CJK 全角按 2 列），忽略 ANSI 转义。"""
    return sum(_cw(ch) for ch in _plain(s))


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dwidth(s))


def _fit(text: str, width: int) -> str:
    """按显示宽度截断（去 ANSI 后切），超出以 … 结尾——防窄终端撑破边框。"""
    if _dwidth(text) <= width:
        return text
    return _cut(_plain(text), max(0, width - 1))[0] + "…"


def _cut(text: str, width: int) -> tuple[str, int]:
    """按显示宽度取前缀 → (前缀, 实际宽度)。"""
    out, w = "", 0
    for ch in text:
        cw = _cw(ch)
        if w + cw > width:
            break
        out, w = out + ch, w + cw
    return out, w


def _fit_segs(segs: list[Seg], width: int) -> list[Seg]:
    """片段列表按显示宽度截断（尾加 …），各片段颜色不丢。"""
    if sum(_dwidth(t) for t, _ in segs) <= width:
        return segs
    out, w = [], 0
    for text, color in segs:
        part, pw = _cut(text, max(0, width - 1 - w))
        if part:
            out.append((part, color))
            w += pw
        if pw < _dwidth(text):
            break
    out.append(("…", out[-1][1] if out else ""))
    return out


def _render_segs(segs: list[Seg], selected: bool = False, width: int | None = None) -> str:
    """片段 → 带 ANSI 的文本。选中行整体反白加粗且各片段颜色保留（dim 会让反白行发灰，去掉）。"""
    if width is not None:
        segs = _fit_segs(segs, width)
    parts = []
    for text, color in segs:
        color = "" if selected and color == "dim" else color
        s = c(color, text) if color else text
        parts.append(c("reverse", c("bold", s)) if selected else s)
    return "".join(parts)


def _wrap_segs(segs: list[Seg], width: int) -> list[Seg]:
    """每个片段按显示宽度折成多行（英文单词不在中间断开），供说明区多行显示。"""
    lines: list[Seg] = []
    for text, color in segs:
        for part in text.split("\n"):
            cur, w = "", 0
            for ch in part:
                cw = _cw(ch)
                if cur and w + cw > width:
                    cut = cur.rfind(" ")
                    if ch != " " and cut > width // 2:
                        lines.append((cur[:cut], color))
                        cur, w = cur[cut + 1:], _dwidth(cur[cut + 1:])
                    else:
                        lines.append((cur.rstrip(), color))
                        cur, w = "", 0
                    if ch == " " and not cur:
                        continue
                cur, w = cur + ch, w + cw
            lines.append((cur.rstrip(), color))
    return lines


def _mask_secret(secret: str) -> str:
    if not secret:
        return "未设置"
    return "****" + secret[-4:] if len(secret) > 4 else "****"


def _default_values() -> dict:
    return {
        "provider": 0, "api_key": "", "model": 0, "model_manual": "", "base_url": "",
        "embedding": False,
        "systemd": False,
        "qq": False, "qq_app_id": "", "qq_secret": "",
        "harunet": False, "harunet_url": "ws://159.75.26.204:6000",
        "harunet_name": "", "harunet_password": "",
        "proxy": "",
        "initial_exploration": "",
    }


# ── 模型选择（预置模型列表 + 手动输入）─────────────────────────────────
def _model_options(values: dict) -> list[str]:
    return (PROVIDERS[int(values["provider"])].get("models") or []) + ["✎ 手动输入…"]


def _choice_idx(values: dict, key: str, n: int) -> int:
    """choice 值 → 合法索引；脏数据/旧草稿（字符串）/越界一律归 0。"""
    try:
        i = int(values.get(key, 0))
    except (TypeError, ValueError):
        return 0
    return i if 0 <= i < n else 0


def _model_is_manual(values: dict) -> bool:
    p = PROVIDERS[int(values["provider"])]
    return _choice_idx(values, "model", len(_model_options(values))) >= len(p.get("models") or [])


def _form_sections() -> list[dict]:
    """栏 → 条目。type: bool / choice / str / secret / action；show_if 控制按需显示；
    suffix(values) → 值右侧追加的着色片段（警告 / 校验状态）。"""
    return [
        {"title": "模型", "items": [
            {"key": "provider", "label": "厂商", "type": "choice",
             "options": PROVIDER_LABELS, "hint": "Enter 切换厂商（Anthropic 兼容端点）"},
            {"key": "api_key", "label": "API Key", "type": "secret",
             "hint": "必填：粘贴密钥（输入不可见），确认后自动联网验证；不通过则不能开始安装",
             "suffix": _check_segs},
            {"key": "model", "label": "模型名", "type": "choice",
             "options": _model_options, "hint": "Enter 循环切换预置模型；选「手动输入」可自填",
             "show_if": lambda v: v["provider"] != len(PROVIDERS) - 1},
            {"key": "model_manual", "label": "自定义模型名", "type": "str",
             "placeholder": "（输入完整模型名）", "hint": "Enter 输入模型名",
             "show_if": lambda v: v["provider"] == len(PROVIDERS) - 1 or _model_is_manual(v)},
            {"key": "base_url", "label": "API 地址", "type": "str",
             "placeholder": "（自定义厂商时填写）", "hint": "Enter 输入 base_url",
             "show_if": lambda v: v["provider"] == len(PROVIDERS) - 1},
        ]},
        {"title": "检索", "items": [
            {"key": "embedding", "label": "向量检索", "type": "bool",
             "hint": "Enter 切换；开启=装 torch 等重依赖（>2GB）；关闭=FTS5+BM25（功能完整）",
             "suffix": lambda v: [(" " + EMBED_WARNING, "red")]},
        ]},
        {"title": "运行", "items": [
            {"key": "systemd", "label": "systemd 无头守护（仅 Linux）", "type": "bool",
             "hint": "Enter 切换；开机自启 + 崩溃自拉。Windows 自动忽略"},
        ]},
        {"title": "通道", "items": [
            {"key": "qq", "label": "QQ Bot 频道机器人", "type": "bool", "hint": "Enter 切换"},
            {"key": "qq_app_id", "label": "QQBOT_APP_ID", "type": "str", "hint": "Enter 输入",
             "show_if": lambda v: v["qq"]},
            {"key": "qq_secret", "label": "QQBOT_CLIENT_SECRET", "type": "secret",
             "hint": "Enter 输入（不可见）", "show_if": lambda v: v["qq"]},
            {"key": "harunet", "label": "HaruNet 组网接入", "type": "bool",
             "hint": "Enter 切换；默认不接入（未接入的智能体对组网一无所知）"},
            {"key": "harunet_url", "label": "hub 地址", "type": "str", "hint": "Enter 输入 ws:// 地址",
             "show_if": lambda v: v["harunet"]},
            {"key": "harunet_name", "label": "网络昵称", "type": "str",
             "placeholder": "（默认 config harunet.name）", "hint": "Enter 输入；留空用默认",
             "show_if": lambda v: v["harunet"]},
            {"key": "harunet_password", "label": "接入密码", "type": "secret",
             "hint": "Enter 输入（不可见；回车保持，输入 - 清空）；输错可再进来重输",
             "show_if": lambda v: v["harunet"]},
        ]},
        {"title": "高级", "items": [
            {"key": "proxy", "label": "出站代理", "type": "str",
             "placeholder": "（不使用，如 socks5://127.0.0.1:7890）", "hint": "Enter 输入；留空跳过"},
            {"key": "initial_exploration", "label": "初始探索方向", "type": "str",
             "placeholder": "（留空跳过）",
             "hint": "Enter 输入一句话，引导 Arch 首次探索；留空跳过"},
        ]},
        {"title": "执行", "items": [
            {"key": "__go", "label": "▶ 开始安装", "type": "action",
             "hint": "Enter 按当前配置开始安装（API Key 须已验证通过）",
             "suffix": lambda v: [] if _api_ready(v) else [
                 ("  API Key " + ("未填写" if not v["api_key"] else "未验证"), "yellow")]},
            {"key": "__quit", "label": "✕ 退出", "type": "action", "hint": "Enter 退出向导"},
        ]},
    ]


def _visible(items: list[dict], values: dict) -> list[dict]:
    return [it for it in items if "show_if" not in it or it["show_if"](values)]


def _fmt_value(item: dict, values: dict) -> list[Seg]:
    """条目的当前值 → 着色片段。"""
    v = values.get(item["key"], item.get("default"))
    t = item["type"]
    if t == "bool":
        return [("是", "green")] if v else [("否", "dim")]
    if t == "choice":
        opts = item["options"](values) if callable(item["options"]) else item["options"]
        return [(opts[_choice_idx(values, item["key"], len(opts))], "bold")]
    if t == "secret":
        return [(_mask_secret(v), "green")] if v else [("（未设置）", "dim")]
    if t == "action":
        return [(item.get("value", ""), "bold")]
    if v:
        return [(str(v), "")]
    return [(item.get("placeholder", "（未设置）"), "dim")]


def _suffix(item: dict, values: dict) -> list[Seg]:
    fn = item.get("suffix")
    return fn(values) if fn else []


def _row(item: dict, values: dict, selected: bool, width: int) -> str:
    """条目行：缩进 + 选中标记 + 名称列 + 值（+ 右侧警告 / 校验状态）。"""
    label = item["label"]
    segs: list[Seg] = [("  ", ""), ("› " if selected else "  ", ""), (label, "")]
    gap = LABEL_W - _dwidth(label)
    if gap > 0:
        segs.append((" " * gap, ""))
    segs.append((" ", ""))
    segs += _fmt_value(item, values) + _suffix(item, values)
    return _render_segs(segs, selected, width)


def _frame_line(content: str, width: int) -> str:
    return c("cyan", "│") + " " + _pad(content, width) + c("cyan", "│")


class _Edit:
    """框内输入框的编辑态：缓冲区 + 光标位置；secret 类型显示为 *。"""

    def __init__(self, item: dict, buf: str = ""):
        self.item = item
        self.buf = buf
        self.cur = len(buf)

    def shown(self) -> str:
        return "*" * len(self.buf) if self.item["type"] == "secret" else self.buf


def _window(text: str, cur: int, width: int) -> tuple[int, int, int]:
    """横向滚动窗口 → (起点, 终点, 光标列)。窗口贴右对齐（靠近正在输入的位置），光标永远可见。"""
    if _dwidth(text) <= width:
        return 0, len(text), _dwidth(text[:cur])
    end, acc = len(text), 0
    while end > 0 and acc + _cw(text[end - 1]) <= width:
        end -= 1
        acc += _cw(text[end])
    if cur >= end:
        return end, len(text), _dwidth(text[end:cur])
    start, acc = cur, 0   # 光标在窗口左侧：以光标为起点向右取
    while start < len(text) and (acc == 0 or acc + _cw(text[start]) <= width):
        acc += _cw(text[start])
        start += 1
    return cur, start, 0


def _edit_box(edit: "_Edit", width: int) -> list[str]:
    """框内输入框的三行：上边框 / 编辑区（光标反白，超宽横向滚动）/ 下边框。"""
    shown = edit.shown()
    start, end, col = _window(shown, edit.cur, width - 8)
    body = shown[start:end]
    cursor = c("reverse", c("bold", body[col] if col < len(body) else " "))
    text = c("bold", body[:col]) + cursor + c("bold", body[col + 1:])
    box = "─" * (width - 4)
    return [_frame_line(" " + c("cyan", "┌" + box + "┐"), width),
            _frame_line(" " + c("cyan", "│") + " " + text + " " + c("cyan", "│"), width),
            _frame_line(" " + c("cyan", "└" + box + "┘"), width)]


def _edit_hint(edit: "_Edit") -> str:
    if edit.item["type"] == "secret":
        return "不可见 · 留空保持不变 · 输入 - 清空 · Enter 确认 · Esc 取消"
    return "Enter 确认 · Esc 取消 · Ctrl+U 清空"


def _edit_key(edit: "_Edit", k: str) -> str | None:
    """输入框按键 → "confirm" / "cancel" / None（继续编辑）。"""
    buf, cur = edit.buf, edit.cur
    if k == "enter":
        return "confirm"
    if k in ("escape", "ctrl-c"):
        return "cancel"
    if k == "backspace" and cur > 0:
        edit.buf, edit.cur = buf[:cur - 1] + buf[cur:], cur - 1
    elif k == "delete" and cur < len(buf):
        edit.buf = buf[:cur] + buf[cur + 1:]
    elif k == "left":
        edit.cur = max(0, cur - 1)
    elif k == "right":
        edit.cur = min(len(buf), cur + 1)
    elif k == "home":
        edit.cur = 0
    elif k == "end":
        edit.cur = len(buf)
    elif k == "ctrl-u":
        edit.buf, edit.cur = "", 0
    elif len(k) == 1 and k.isprintable():
        edit.buf, edit.cur = buf[:cur] + k + buf[cur:], cur + 1
    return None


def _apply_edit(edit: "_Edit", values: dict) -> None:
    """把输入框的结果写回表单值。secret：留空保持不变，输入 - 清空。"""
    k = edit.item["key"]
    if edit.item["type"] == "secret":
        if edit.buf == "-":
            values[k] = ""
        elif edit.buf:
            values[k] = edit.buf
    else:
        values[k] = edit.buf.strip()


def _render_frame(sections: list[dict], values: dict, sec_i: int, item_i: int,
                  vis: list[dict], edit: "_Edit | None" = None,
                  msg: list[Seg] | None = None) -> list[str]:
    """画整帧：标题 / 栏标签 / 条目行 /（框内输入框）/ 说明区 / 按键提示。
    说明区优先显示 msg（校验结果等多行提示），其次输入框的按键说明，最后当前条目的 hint。"""
    cols = shutil.get_terminal_size((80, 24)).columns
    W = max(20, min(cols - 2, 80))
    width = W - 3   # 边框内可用内容宽度
    bar = "─" * (W - 2)
    L = [c("cyan", "╭" + bar + "╮"),
         _frame_line(c("bold", "Goharu 安装向导"), width),
         c("cyan", "├" + bar + "┤")]
    tabs = ""
    for i, s in enumerate(sections):
        t = f" {s['title']} "
        tabs += (c("reverse", c("bold", t)) if i == sec_i else c("dim", t)) + " "
    L.append(_frame_line(_fit(tabs, width), width))
    L.append(c("cyan", "├" + bar + "┤"))
    if not vis:
        L.append(_frame_line(c("dim", "（无项目）"), width))
    for j, it in enumerate(vis):
        L.append(_frame_line(_row(it, values, j == item_i, width), width))
    if edit is not None:
        L.extend(_edit_box(edit, width))
    L.append(c("cyan", "├" + bar + "┤"))
    if msg:
        L.extend(_frame_line(c(color, text), width) for text, color in _wrap_segs(msg, width))
    else:
        hint = (_edit_hint(edit) if edit is not None
                else (vis[item_i].get("hint", "") if vis else ""))
        L.append(_frame_line(c("dim", _fit(hint, width)), width))
    L.append(_frame_line(c("dim", KEYS_HELP), width))
    L.append(c("cyan", "╰" + bar + "╯"))
    return L


def _edit_item(item: dict, values: dict) -> None:
    """非 TTY：真正询问一个条目（TTY 下布尔/选项由主循环切换，文本走框内输入框）。"""
    k, t = item["key"], item["type"]
    cur = values.get(k, item.get("default"))
    if t == "bool":
        values[k] = confirm(item["label"], default=bool(cur))
    elif t == "choice":
        opts = item["options"](values) if callable(item["options"]) else item["options"]
        values[k] = choose(item["label"], opts, default=_choice_idx(values, k, len(opts)))
    elif t == "secret":
        nv = ask_secret(f"{item['label']}（回车保持不变；输入 - 清空）")
        if nv == "-":
            values[k] = ""
        elif nv:
            values[k] = nv
    elif t == "str":
        values[k] = ask(item["label"], str(cur or "")).strip()
    # action 由调用方处理


def _check_sig(values: dict) -> tuple:
    """决定「这次验证还算不算数」的全部输入：厂商 / 密钥 / 模型 / 自定义地址 / 代理。"""
    return (values["provider"], values["api_key"], values["model"],
            values.get("model_manual", ""), values["base_url"], values.get("proxy", ""))


def _missing_field(values: dict) -> str:
    """自定义厂商或选了「手动输入」时模型名必填；自定义厂商还须填 API 地址。"""
    custom = int(values["provider"]) == len(PROVIDERS) - 1
    if (custom or _model_is_manual(values)) and not values.get("model_manual"):
        return "模型名"
    if custom and not values["base_url"]:
        return "API 地址"
    return ""


def _api_ready(values: dict) -> bool:
    ck = values.get("_check") or {}
    return (bool(values["api_key"]) and ck.get("sig") == _check_sig(values)
            and ck.get("ok") is True)


def _check_segs(values: dict) -> list[Seg]:
    """API Key 行右侧的校验状态。"""
    if not values.get("api_key"):
        return [(" 必填", "yellow")]
    ck = values.get("_check") or {}
    if ck.get("sig") != _check_sig(values):
        return [(" 未验证", "yellow")]
    if ck.get("ok") is True:
        return [(" ✓ 已验证", "green")]
    if ck.get("ok") is None:
        return [(" 验证中…", "yellow")]
    return [(" ✗ " + failure_message(ck.get("kind", "unknown"), ck.get("status"), hint=False),
             "red")]


def _run_verify(values: dict, on_verify, draw) -> list[Seg]:
    """联网验证 API Key（阻塞数秒）：先画「验证中」，结果写入 values["_check"] 并返回说明区内容。"""
    values["_check"] = {"sig": _check_sig(values), "ok": None}
    draw(msg=[("正在验证 API Key：向模型端点发一次最小请求…", "yellow")])
    t0 = time.time()
    try:
        ok, kind, status, text = on_verify(_provider_from_form(values), values.get("proxy", ""))
    except Exception as e:   # 验证流程自身的意外也要变成一句人话，绝不让向导崩掉
        ok, kind, status, text = False, "unknown", None, f"验证请求异常：{type(e).__name__}: {e}"
    values["_check"].update(ok=ok, kind=kind, status=status)
    if ok:
        model = _provider_from_form(values)["model"]
        return [(f"✓ API Key 验证通过（{model}，{time.time() - t0:.1f}s）", "green")]
    head, _, tail = text.partition("\n")
    return [("✗ 验证失败：" + head, "red")] + ([(tail, "dim")] if tail else [])


def _gate(values: dict, on_verify, draw) -> tuple[bool, list[Seg], tuple[int, int]]:
    """「开始安装」的校验闸门 → (放行, 提示行, 应跳转过去的 (栏, 条目))。
    模型 / API 地址 / API Key 必须齐全，且 API Key 须针对当前配置联网验证通过。"""
    missing = _missing_field(values)
    if missing:
        return False, [(f"✗ 自定义厂商必须填写{missing}", "red")], (0, 2 if missing == "模型名" else 3)
    if not values["api_key"]:
        return False, [("✗ API Key 为必填项：填写后自动联网验证，通过后才能开始安装", "red")], (0, 1)
    msg: list[Seg] = []
    if not _api_ready(values):
        msg = _run_verify(values, on_verify, draw)
        if not _api_ready(values):
            return False, [("✗ API Key 未通过验证，不能开始安装", "red")] + msg, (0, 1)
    return True, msg, (0, 0)


class _FormTooLarge(Exception):
    """终端装不下完整表单帧：立即降级顺序问答。"""


def _form_tty(sections: list[dict], values: dict, on_verify) -> dict:
    try:
        return _form_tty_loop(sections, values, on_verify)
    except _FormTooLarge:
        print("\r\n终端尺寸不足，已切换为顺序问答模式")
        return _form_fallback(sections, values, on_verify)


def _form_tty_loop(sections: list[dict], values: dict, on_verify) -> dict:
    """TTY 多栏表单主循环：返回通过校验闸门的 values；用户选「退出」或 Ctrl+C 则 SystemExit。"""
    sec_i, item_i, drawn = 0, 0, 0
    edit: "_Edit | None" = None    # 非 None：框内输入框打开中
    msg: list[Seg] | None = None   # 说明区的提示 / 校验结果（下一次按键即清除）

    def draw(edit=None, msg=None) -> None:
        nonlocal drawn, item_i
        vis_ = _visible(sections[sec_i]["items"], values)
        item_i = max(0, min(item_i, len(vis_) - 1))
        lines = _render_frame(sections, values, sec_i, item_i, vis_, edit, msg)
        size = shutil.get_terminal_size((80, 24))
        too_wide = any(
            _dwidth(_ANSI_RE.sub("", line)) > size.columns - 1 for line in lines)
        if size.columns < 22 or too_wide or len(lines) > size.lines - 1:
            raise _FormTooLarge
        frame = "\r\n".join(lines)
        prefix = f"\r\x1b[{drawn - 1}A\x1b[0J" if drawn else "\x1b[0J"
        sys.stdout.write(prefix + frame)
        sys.stdout.flush()
        drawn = len(lines)

    while True:
        vis = _visible(sections[sec_i]["items"], values)
        draw(edit, msg)   # 内部夹紧 item_i
        k = _read_key()
        if edit is not None:
            res = _edit_key(edit, k)
            if res == "confirm":
                done, edit = edit, None
                _apply_edit(done, values)
                _save_install_draft(values)
                if done.item["key"] == "api_key" and values["api_key"]:
                    msg = _run_verify(values, on_verify, draw)
            elif res == "cancel":
                edit, msg = None, None
            continue
        if k == "ctrl-c":
            _save_install_draft(values)
            print()
            raise SystemExit(130)
        msg = None   # 导航 / 操作键：清掉上一条提示
        if k == "left":
            sec_i, item_i = (sec_i - 1) % len(sections), 0
        elif k == "right":
            sec_i, item_i = (sec_i + 1) % len(sections), 0
        elif k == "up" and vis:
            item_i = (item_i - 1) % len(vis)
        elif k == "down" and vis:
            item_i = (item_i + 1) % len(vis)
        elif k == "enter" and vis:
            item = vis[item_i]
            if item["type"] == "action":
                if item["key"] == "__quit":
                    _save_install_draft(values)
                    print()
                    raise SystemExit(0)
                ok, msg, jump = _gate(values, on_verify, draw)
                if ok:
                    _save_install_draft(values)
                    print()
                    return values
                sec_i, item_i = jump
            elif item["type"] == "bool":
                values[item["key"]] = not values.get(item["key"], item.get("default"))
            elif item["type"] == "choice":
                opts = item["options"](values) if callable(item["options"]) else item["options"]
                values[item["key"]] = (_choice_idx(values, item["key"], len(opts)) + 1) % len(opts)
            else:
                cur = values.get(item["key"]) or ""
                edit = _Edit(item, "" if item["type"] == "secret" else str(cur))
        _save_install_draft(values)


def _form_fallback(sections: list[dict], values: dict, on_verify) -> dict:
    """非 TTY：顺序问答，覆盖同一份表单定义；API Key 同样必填且须联网验证通过（最多 3 次）。"""
    print()
    print(c("bold", "  配置（顺序填写，回车取默认值）"))
    for sec in sections:
        if sec["title"] == "执行":
            continue
        print(f"\n  {c('cyan', '■')} {sec['title']}")
        items, i = sec["items"], 0
        while i < len(items):   # 动态可见性：开关翻转后其子项也要被问到
            item = items[i]
            i += 1
            if "show_if" in item and not item["show_if"](values):
                continue
            _edit_item(item, values)
            _save_install_draft(values)
            shown = "".join(text for text, _ in _fmt_value(item, values))
            print(f"      → {item['label']}: {shown}")
    flat = [it for s in sections for it in s["items"] if it["type"] in ("str", "secret")]
    for attempt in range(1, 4):
        missing = _missing_field(values)
        if missing or not values["api_key"]:
            err(f"自定义厂商必须填写{missing}" if missing else "API Key 为必填项")
        else:
            ok, _kind, _status, text = on_verify(_provider_from_form(values), values.get("proxy", ""))
            if ok:
                _save_install_draft(values)
                return values
            err("API Key 验证失败：" + text.replace("\n", " "))
        if attempt == 3:
            break
        key = "api_key" if not missing else ("model_manual" if missing == "模型名" else "base_url")
        _edit_item(next(it for it in flat if it["key"] == key), values)
        _save_install_draft(values)
    err("API Key 连续 3 次未通过验证，安装中止")
    raise SystemExit(1)


# ── 表单 → 步骤参数 ─────────────────────────────────────────────────────
def _provider_from_form(values: dict) -> dict:
    """表单当前值 → 厂商参数（预置模型按索引取；手动输入/自定义厂商取手填值）。"""
    p = dict(PROVIDERS[int(values["provider"])])
    if p["label"] == "自定义" or _model_is_manual(values):
        p["model"] = values.get("model_manual", "").strip()
    else:
        p["model"] = p["models"][_choice_idx(values, "model", len(_model_options(values)))]
    if p["label"] == "自定义":
        p["base_url"] = values["base_url"]
    p["key"] = values["api_key"]
    return p


# ── API Key 联网验证 ───────────────────────────────────────────────────
_PROTOCOLS = (
    # (协议, 路径, 鉴权头)：与运行时 SDK 实际发出的请求保持一致
    ("anthropic", "/v1/messages",
     lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    ("openai", "/chat/completions", lambda key: {"authorization": f"Bearer {key}"}),
)
_VERIFY_TIMEOUT = 20.0


class _NetFail(Exception):
    """连接层失败（连不上 / 超时），不是上游给的 HTTP 错误码。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


def _http_post(url: str, body: bytes, headers: dict, proxy: str) -> tuple[int, str]:
    """POST JSON → (状态码, 响应体)。HTTP 错误码原样返回（响应体就是上游的报错说明），
    连接层失败抛 _NetFail。未填代理时沿用系统 / 环境变量代理。"""
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if proxy else urllib.request.ProxyHandler())
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"content-type": "application/json", "accept": "application/json",
                 "user-agent": "goharu-install", **headers})
    try:
        with urllib.request.build_opener(handler).open(req, timeout=_VERIFY_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return e.code, text
    except urllib.error.URLError as e:
        low = str(e.reason or e).lower()
        timeout = isinstance(e.reason, TimeoutError) or "timed out" in low
        raise _NetFail("timeout" if timeout else "network", f"{url} - {e.reason or e}") from None
    except (TimeoutError, http.client.HTTPException, OSError) as e:
        timeout = isinstance(e, TimeoutError)
        raise _NetFail("timeout" if timeout else "network", f"{url} - {e}") from None


def _is_model_reply(text: str) -> bool:
    """HTTP 200 也可能是包着业务错误的假成功（部分网关）：必须真是一次补全。
    兼容端点差异：Kimi 对 max_tokens=1 返回 type=""、空 content（实测），
    只要带 id/model/usage 且非 error，就是有效的模型响应。"""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("type") == "error" or "error" in data:
        return False
    if data.get("type") == "message" or data.get("choices"):
        return True
    return bool(data.get("id") and data.get("model") and data.get("usage"))


def verify_provider(provider: dict, proxy: str = "") -> tuple[bool, str, int | None, str]:
    """向厂商端点发一次最小请求（max_tokens=1，与运行时启动探测同款），确认密钥、模型、地址可用。

    仅标准库：安装向导运行在 venv 建好之前，拿不到 anthropic / openai SDK。
    自定义厂商按运行时 auto 探测的顺序补试 OpenAI 协议（预置厂商都是 Anthropic 兼容端点）。
    → (ok, kind, 状态码, 用户可读说明)
    """
    base = provider["base_url"].rstrip("/")
    body = json.dumps({"model": provider["model"], "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
    protocols = _PROTOCOLS if provider["label"] == "自定义" else _PROTOCOLS[:1]
    for i, (_proto, path, auth) in enumerate(protocols):
        try:
            status, text = _http_post(base + path, body, auth(provider["key"]), proxy)
        except _NetFail as nf:
            return False, nf.kind, None, failure_message(nf.kind, None, nf.detail)
        if status == 200 and _is_model_reply(text):
            return True, "ok", 200, ""
        kind, st = classify_failure(status, text)
        if kind not in ("not_found", "bad_request") or i == len(protocols) - 1:
            return False, kind, st, failure_message(kind, st, _error_rules.upstream_detail(text))
    raise AssertionError("no verification protocol")   # pragma: no cover


# ── 重跑向导：带回上次安装的模型配置 ─────────────────────────────────────
def _read_env_file() -> dict:
    """读 .env 的 KEY=value（容忍引号与行尾注释）；不依赖第三方库。"""
    out: dict = {}
    path = os.path.join(REPO_DIR, ".env")
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
        if not m:
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        else:
            v = re.sub(r"\s+#.*$", "", v).strip()
        out[m.group(1)] = v
    return out


def _read_model_block() -> dict:
    """从 config.yaml 文本级读出 model.large-language-model 的 model / base_url / api_key_env。"""
    path = os.path.join(REPO_DIR, "config.yaml")
    if not os.path.exists(path):
        return {}
    out, in_block = {}, False
    for ln in open(path, encoding="utf-8").read().splitlines():
        if re.match(r"^\s*large-language-model:\s*$", ln):
            in_block = True
            continue
        if not in_block:
            continue
        if ln.strip() and len(ln) - len(ln.lstrip()) <= 2:
            break   # 块结束
        m = re.match(r"^\s+(model|base_url|api_key_env):\s*(.*)$", ln)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def _read_bootstrap_exploration(text: str) -> str:
    in_arch = in_bootstrap = False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            in_arch = line.startswith("architect_hub:")
            in_bootstrap = False
        elif in_arch and re.match(r"^  \S", line):
            in_bootstrap = line.startswith("  bootstrap:")
        elif in_bootstrap and re.match(r"^    initial_exploration\s*:", line):
            raw = line.split(":", 1)[1].strip()
            if raw.startswith('"'):
                try:
                    value = json.loads(raw)
                    return value if isinstance(value, str) else ""
                except json.JSONDecodeError:
                    return ""
            if raw.startswith("'") and raw.endswith("'"):
                return raw[1:-1].replace("''", "'")
            return raw.split(" #", 1)[0].strip()
    return ""


def _write_bootstrap_exploration(text: str, value: str) -> tuple[str, bool]:
    lines = text.splitlines()
    out = []
    in_arch = in_bootstrap = False
    insert_at = None
    new_line = "    initial_exploration: " + json.dumps(value, ensure_ascii=False)
    replaced = False
    for line in lines:
        if re.match(r"^\S", line):
            in_arch = line.startswith("architect_hub:")
            in_bootstrap = False
        elif in_arch and re.match(r"^  \S", line):
            in_bootstrap = line.startswith("  bootstrap:")
            if in_bootstrap:
                insert_at = len(out) + 1
        elif in_bootstrap and re.match(r"^    identity_path\s*:", line):
            insert_at = len(out) + 1
        if in_bootstrap and re.match(r"^    initial_exploration\s*:", line):
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if insert_at is None:
            return text, False
        out.insert(insert_at, new_line)
    result = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return result, result != text


def _save_install_draft(values: dict) -> None:
    """保存未完成的表单；退出/崩溃后重跑可继续。文件仅当前用户可读。"""
    path = _install_draft_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        data = {key: values.get(key) for key in _default_values()}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception as e:
        warn(f"安装草稿保存失败: {e}")


def _load_install_draft(values: dict) -> bool:
    path = _install_draft_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    for key in _default_values():
        if key in data:
            values[key] = data[key]
    return True


def _clear_install_draft() -> None:
    try:
        os.remove(_install_draft_path())
    except OSError:
        pass


def _load_existing(values: dict) -> bool:
    """重跑向导：带回已装配置，再覆盖上次未完成的表单草稿。"""
    cfg = _read_model_block()
    if cfg.get("model") or cfg.get("base_url"):
        values["provider"] = len(PROVIDERS) - 1   # 对不上预置厂商就是自定义
        for i, p in enumerate(PROVIDERS[:-1]):
            if p["base_url"].rstrip("/") == (cfg.get("base_url") or "").rstrip("/"):
                values["provider"] = i
                models = p.get("models") or []
                # 模型在预置列表里就选中对应项，否则落到「手动输入」带回原名
                values["model"] = models.index(cfg["model"]) if cfg.get("model") in models else len(models)
                break
        values["model_manual"] = cfg.get("model", "")
        values["base_url"] = cfg.get("base_url", "")
    key_env = cfg.get("api_key_env") or PROVIDERS[values["provider"]]["env"]
    values["api_key"] = _read_env_file().get(key_env, "")
    cfg_path = os.path.join(REPO_DIR, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            values["initial_exploration"] = _read_bootstrap_exploration(f.read())
    return _load_install_draft(values)


def _channels_from_form(values: dict) -> dict:
    ch = {"qq": None, "harunet": None, "proxy": values["proxy"],
          "initial_exploration": values["initial_exploration"]}
    if values["qq"] and values["qq_app_id"] and values["qq_secret"]:
        ch["qq"] = {"app_id": values["qq_app_id"], "secret": values["qq_secret"]}
    if values["harunet"]:
        ch["harunet"] = {"enabled": True, "url": values["harunet_url"],
                         "name": values["harunet_name"], "password": values["harunet_password"]}
    return ch


# ── 长步骤执行（实时滚动显示输出 + 成功✓/失败✗）────────────────────────
def run_step(label: str, cmd: str, cwd: str = REPO_DIR) -> bool:
    """跑一个步骤：实时滚动回显子进程输出（缩进 dim），完成打 ✓，失败打 ✗ 并回显错误尾部。
    pip/npm 的 Collecting/Downloading/Installing 行都会逐条可见。"""
    print(f"{c('cyan', '  ▶')} {c('bold', label)}")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if not line.strip():
            continue
        tail.append(line)
        if len(tail) > 80:
            tail.pop(0)
        if "━" in line:  # pip 进度条刷屏行（块字符）跳过，避免刷屏
            continue
        print("    " + c("dim", line))
    proc.wait()
    if proc.returncode != 0:
        err(f"{label} 失败（exit {proc.returncode}）")
        for line in tail[-12:]:
            print("      " + c("red", line))
        return False
    print(c("green", f"  ✓ {label}"))
    return True


def run_pip_step(label: str, pip_args: str, proxy: str = "") -> bool:
    """pip 步骤带镜像回退：依次尝试 PIP_MIRRORS（清华→阿里→中科大），
    单源网络失败自动换源重跑（pip 幂等，已装包自动跳过）；全部失败才报错。
    --isolated：忽略宿主机环境变量与用户级 pip 配置（如 cert=/dev/null 毒配置），
    本次安装用干净配置、不改宿主机任何文件；代理经 --proxy 显式传递。"""
    for i, mirror in enumerate(PIP_MIRRORS):
        if i > 0:
            info(f"切换 pip 镜像源：{mirror}")
        cmd = f"{_venv_py()} -m pip --isolated {pip_args} -i {mirror}"
        if proxy:
            cmd += f' --proxy "{proxy}"'
        if run_step(label, cmd):
            return True
    return False


# ── 步骤实现 ─────────────────────────────────────────────────────────
def _find_node():
    """遍历 PATH 全部候选 + 常见安装位置，返回第一个 >= 20 的 (node 路径, 版本)。
    不因 PATH 里第一个是旧版就误判缺失。"""
    import glob
    exe = "node.exe" if IS_WIN else "node"
    cands = [os.path.join(d, exe) for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    home = os.path.expanduser("~")
    if IS_WIN:
        cands += glob.glob(os.path.join(home, "node*", "node.exe"))
        cands += [r"C:\Program Files\nodejs\node.exe"]
    else:
        cands += glob.glob(os.path.join(home, "node*", "bin", "node"))
        cands += ["/usr/local/bin/node"]
        cands += glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", "node"))
        cands += glob.glob("/opt/node*/bin/node")
    seen = set()
    for p in cands:
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        try:
            ver = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            continue
        m = re.match(r"v?(\d+)", ver)
        if m and int(m.group(1)) >= 20:
            return p, ver
    return None, ""


def step_check_env() -> bool:
    title("环境探测")
    print(f"    系统: {platform.system()} {platform.release()} ({platform.machine()})")

    ok = True

    # Python（向导自身已在跑，直接读 sys）
    pv = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info >= (3, 10):
        info(f"Python {pv}（>= 3.10）")
    else:
        err(f"Python {pv} 太旧，需要 >= 3.10")
        ok = False

    # Git
    git = shutil.which("git")
    if git:
        info(f"git: {git}")
    else:
        err("未找到 git")
        if IS_WIN:
            print("      安装: winget install Git.Git   或  https://git-scm.com/downloads")
        else:
            print("      Debian/Ubuntu: sudo apt install git   macOS: xcode-select --install / brew install git")
        ok = False

    # Node / npm（TUI 依赖 ink/string-width 用了 /v 正则旗标，需 Node >= 20）
    # 全候选探测：找到合格即用，并注入 PATH 供后续 npm 构建使用
    node, node_ver = _find_node()
    if node:
        os.environ["PATH"] = os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")
        info(f"Node {node_ver}（{node}）")
    else:
        err("需要 Node.js >= 20（TUI 依赖 ink/string-width 的 /v 正则旗标）")
        if IS_WIN:
            print("      安装: winget install OpenJS.NodeJS.LTS   或  重跑 bootstrap.ps1 自动安装")
        else:
            print("      重跑 bootstrap.sh 可自动安装到 ~/node22，或 nvm install 20")
        ok = False

    return ok


def _venv_py() -> str:
    return os.path.join("venv", "Scripts", "python.exe") if IS_WIN else os.path.join("venv", "bin", "python")


def step_install_deps(with_embedding: bool, proxy: str = "") -> bool:
    title("安装 Python 依赖")
    if not os.path.isdir(os.path.join(REPO_DIR, "venv")):
        # 用当前解释器建 venv，保证与运行向导的 Python 版本一致
        if not run_step("创建虚拟环境 venv", f'"{sys.executable}" -m venv venv'):
            err("venv 创建失败。Debian/Ubuntu 先: sudo apt install python3-venv")
            return False
    else:
        info("venv 已存在，跳过创建")

    if not run_pip_step("升级 pip", "install --upgrade pip -q --progress-bar off", proxy):
        return False
    if not run_pip_step("安装核心依赖", "install --progress-bar off -r requirements.txt", proxy):
        return False
    if with_embedding:
        if not run_pip_step(
            "安装 embedding 重依赖（torch 等，>2GB，请耐心）",
            "install --progress-bar off -r requirements-embedding.txt", proxy,
        ):
            return False
    else:
        info("跳过 embedding 重依赖（向量检索降级为 FTS5+BM25）")
    return True


def step_build_tui() -> bool:
    title("构建 TUI")
    if not os.path.isfile(os.path.join(REPO_DIR, "tui-ts", "package.json")):
        warn("未发现 tui-ts，跳过 TUI 构建")
        return True
    tui_dir = os.path.join(REPO_DIR, "tui-ts")
    npm = "npm.cmd" if IS_WIN else "npm"
    return run_step("npm install + tsc 构建", f"{npm} install --silent && {npm} run build --silent", cwd=tui_dir)


def step_write_config(provider: dict) -> bool:
    title("写入配置")

    # 1) .env：确保从模板生成，再写入所选密钥
    env_path = os.path.join(REPO_DIR, ".env")
    if not os.path.exists(env_path):
        src = os.path.join(REPO_DIR, ".env.example")
        if os.path.exists(src):
            shutil.copyfile(src, env_path)
            info("已从 .env.example 生成 .env")
        else:
            open(env_path, "w", encoding="utf-8").write("")
            info("已创建空 .env")

    if provider.get("key"):
        lines = open(env_path, encoding="utf-8").read().splitlines()
        lines = _set_env_line(lines, provider["env"], provider["key"])
        open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        info(f"已写入 {provider['env']}")

    # 2) config.yaml：把 model.large-language-model 指向所选厂商（行级替换，保持其余配置不动）
    cfg_path = os.path.join(REPO_DIR, "config.yaml")
    if provider.get("model") and os.path.exists(cfg_path):
        lines = open(cfg_path, encoding="utf-8").read().splitlines()
        out = []
        in_block = False
        for ln in lines:
            if re.match(r"^\s*large-language-model:\s*$", ln):
                in_block = True
                out.append(ln)
                continue
            if in_block:
                indent = len(ln) - len(ln.lstrip())
                stripped = ln.strip()
                if stripped and indent <= 2:
                    in_block = False  # 块结束
                else:
                    m = re.match(r"^(\s*)(model|api_key_env|base_url):\s*(.*)$", ln)
                    if m:
                        key, ind = m.group(2), m.group(1)
                        if key == "model":
                            out.append(f"{ind}model: {provider['model']}")
                            continue
                        if key == "base_url":
                            out.append(f"{ind}base_url: {provider['base_url']}")
                            continue
                        if key == "api_key_env":
                            out.append(f"{ind}api_key_env: {provider['env']}")
                            continue
            out.append(ln)
        open(cfg_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
        info(f"config.yaml 模型已设为 {provider['model']}")
    return True


def step_setup_systemd() -> bool:
    if IS_WIN or not shutil.which("systemctl"):
        return True
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    unit = f"""[Unit]
Description=Goharu Agent Gateway (headless daemon)
After=network.target

[Service]
WorkingDirectory={REPO_DIR}
ExecStart=/bin/bash -c 'tail -f /dev/null | venv/bin/python Gateway/gateway_entry.py'
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    with open(os.path.join(unit_dir, "goharu.service"), "w", encoding="utf-8") as f:
        f.write(unit)
    run_step("注册 systemd user 服务", "systemctl --user daemon-reload && systemctl --user enable goharu")
    warn("如需无登录开机自启：sudo loginctl enable-linger $USER")
    return True


def _posix_launcher_content(repo: str, venv_py: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "# Goharu 全局启动器（install.py 生成）\n"
        f'GOHARU_HOME="{repo}"\n'
        'cd "$GOHARU_HOME" || exit 1\n'
        # gateway 子进程要用 venv python（带依赖），否则落到系统 python3 崩在 import
        f'export PYTHON="{venv_py}"\n'
        'set -a; [ -f "$GOHARU_HOME/.env" ] && . "$GOHARU_HOME/.env"; set +a\n'
        f'exec "{venv_py}" "$GOHARU_HOME/goharu_cli.py" "$@"\n'
    )


def step_register_command() -> bool:
    """注册全局 `goharu` 命令到 PATH，装完任意目录敲 `goharu` 即启动（无需再跑 start 脚本）。"""
    title("注册全局命令 goharu")
    repo = REPO_DIR
    if IS_WIN:
        bin_dir = os.path.join(os.path.expanduser("~"), ".goharu", "bin")
        os.makedirs(bin_dir, exist_ok=True)
        launcher = os.path.join(bin_dir, "goharu.cmd")
        venv_py = os.path.join(repo, "venv", "Scripts", "python.exe")
        # .cmd 用 CRLF，Windows 批处理才稳
        content = (
            "@echo off\r\n"
            f'cd /d "{repo}"\r\n'
            f'set "PYTHON={venv_py}"\r\n'
            f'"{venv_py}" "{os.path.join(repo, "goharu_cli.py")}" %*\r\n'
        )
        with open(launcher, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        # 写入用户 PATH（走注册表，规避 setx 的 1024 截断 bug）
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            if bin_dir.lower() not in cur.lower():
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, (cur + ";" + bin_dir) if cur else bin_dir)
            winreg.CloseKey(key)
            # 广播环境变更，让新终端立即拿到
            try:
                import ctypes

                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0, 1000, None)
            except Exception:
                pass
            info(f"已注册 goharu → {bin_dir}（已加入用户 PATH，新开终端生效）")
        except Exception as e:
            warn(f"写入 PATH 失败: {e}。请手动把 {bin_dir} 加入 PATH")
            return False
        return True

    bin_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(bin_dir, exist_ok=True)
    launcher = os.path.join(bin_dir, "goharu")
    venv_py = os.path.join(repo, "venv", "bin", "python")
    content = _posix_launcher_content(repo, venv_py)
    with open(launcher, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.chmod(launcher, 0o755)
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        snippet = '\n# Goharu 全局命令\nexport PATH="$HOME/.local/bin:$PATH"\n'
        for rc in (".bashrc", ".zshrc", ".profile"):
            rcp = os.path.expanduser(f"~/{rc}")
            if os.path.exists(rcp):
                with open(rcp, "a", encoding="utf-8") as f:
                    f.write(snippet)
        info("已注册 goharu → ~/.local/bin（已写入 shell 配置，新开终端或 source 后生效）")
    else:
        info("已注册 goharu → ~/.local/bin")
    return True


def _set_env_line(lines: list, key: str, value: str) -> list:
    out, found = [], False
    for ln in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", ln):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    return out


def step_write_channels(ch: dict) -> None:
    """把 QQ/HaruNet/代理/Arch 初始探索方向写入 .env 与 config.yaml。"""
    env_path = os.path.join(REPO_DIR, ".env")
    # QQ → .env（channel qq_main 已在默认 config 里）
    if ch.get("qq"):
        lines = (open(env_path, encoding="utf-8").read().splitlines()
                 if os.path.exists(env_path) else [])
        lines = _set_env_line(lines, "QQBOT_APP_ID", ch["qq"]["app_id"])
        lines = _set_env_line(lines, "QQBOT_CLIENT_SECRET", ch["qq"]["secret"])
        open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        info("已写入 QQ Bot 凭据到 .env")
    cfg_path = os.path.join(REPO_DIR, "config.yaml")
    # HaruNet 接入开关：.env HARUNET_ENABLED=true/false（机器级覆盖；config 默认 false）
    har = ch.get("harunet")
    if har is not None:
        enabled = bool(har.get("enabled", True))
        lines = (open(env_path, encoding="utf-8").read().splitlines()
                 if os.path.exists(env_path) else [])
        lines = _set_env_line(lines, "HARUNET_ENABLED",
                              "true" if enabled else "false")
        if enabled:
            if har.get("name"):
                lines = _set_env_line(lines, "HARUNET_NAME", har["name"])
            if har.get("password"):
                lines = _set_env_line(lines, "HARUNET_PASSWORD", har["password"])
        open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        info("已写入 HARUNET_ENABLED=%s 到 .env" % ("true" if enabled else "false"))
    if not os.path.exists(cfg_path):
        return
    txt = open(cfg_path, encoding="utf-8").read()
    changed = False
    # HaruNet → harunet.url（config 里唯一 ws:// 的那行）
    if har and har.get("url"):
        t2 = re.sub(r"(?m)^(\s*url:\s*)ws://\S+",
                    lambda m: m.group(1) + har["url"], txt, count=1)
        if t2 != txt:
            txt, changed = t2, True
            info(f"harunet.url → {har['url']}")
    # Arch 初始探索方向 → architect_hub.bootstrap.initial_exploration
    if "initial_exploration" in ch:
        txt, exploration_changed = _write_bootstrap_exploration(
            txt, ch["initial_exploration"])
        if exploration_changed:
            changed = True
            info("architect_hub.bootstrap.initial_exploration 已更新")
    # 代理 → network.proxy.url（4 空格缩进、空引号的那行）
    if ch.get("proxy"):
        t2 = re.sub(r'(?m)^(    url:\s*)""',
                    lambda m: m.group(1) + '"' + ch["proxy"] + '"', txt, count=1)
        if t2 != txt:
            txt, changed = t2, True
            info(f"network.proxy.url → {ch['proxy']}")
    if changed:
        open(cfg_path, "w", encoding="utf-8").write(txt)


# ── 主流程 ───────────────────────────────────────────────────────────
def main() -> int:
    print(c("bold", c("magenta", r"""
   ____       _                     ____
  / ___| ___ | |__   __ _ _ __ _   / ___|
 | |  _ / _ \| '_ \ / _` | '__| | | |  _
 | |_| | (_) | | | | (_| | |  | |_| | |_| |
  \____|\___/|_| |_|\__,_|_|   \___(_)____/
""")))
    print(c("bold", "  Goharu 安装向导  —  一条命令，一气呵成"))
    print(c("dim", f"  仓库: {REPO_DIR}"))

    if not step_check_env():
        err("环境检查未通过。请按上方提示补齐依赖后重跑本向导。")
        return 1

    # ── 多栏配置表单（←→ 切栏 / ↑↓ 选条目 / Enter 编辑；API Key 必填且联网验证；非 TTY 顺序问答）──
    sections = _form_sections()
    values = _default_values()
    draft_loaded = _load_existing(values)
    if draft_loaded:
        info("已恢复上次未完成的安装内容（密钥仅保存在本机安装草稿）")
    if _is_tty():
        print()
        print(c("dim", "  下方为配置表单：←→ 切换栏，↑↓ 选择条目，Enter 编辑；到「执行」栏开始安装"))
        values = _form_tty(sections, values, on_verify=verify_provider)
    else:
        values = _form_fallback(sections, values, on_verify=verify_provider)

    provider = _provider_from_form(values)
    channels = _channels_from_form(values)
    with_embedding = bool(values["embedding"])
    with_systemd = bool(values["systemd"])

    if not step_install_deps(with_embedding, values.get("proxy", "")):
        err("依赖安装失败，可重跑本向导（已完成步骤会自动跳过）")
        return 1

    if not step_build_tui():
        warn("TUI 构建失败，可稍后手动: cd tui-ts && npm install && npm run build")

    step_write_config(provider)
    step_write_channels(channels)
    if with_systemd:
        step_setup_systemd()
    step_register_command()
    _clear_install_draft()

    title("安装完成")
    if IS_WIN:
        print(f"    启动: 新开一个终端，输入 {c('green', 'goharu')}（任意目录均可）")
    else:
        print(f"    启动: 当前终端先执行 {c('green', 'source ~/.bashrc')} 再敲 {c('green', 'goharu')}；")
        print(f"          或直接重开一个终端敲 {c('green', 'goharu')}（PATH 已写入 shell 配置）")
    if with_systemd and not IS_WIN:
        print(f"    守护: {c('green', 'systemctl --user start goharu')}")
    if channels.get("harunet"):
        print(f"    组网: {c('green', 'HaruNet 已启用')}（首次握手若被拒，检查接入密码或让智能体 harunet_reconnect）")

    if confirm("现在立即启动 Goharu？", default=False):
        vp = _venv_py()
        print(c("dim", "正在启动 TUI…（Ctrl+C 退出）"))
        try:
            subprocess.call(f'"{os.path.abspath(vp)}" run_tui_ts.py', cwd=REPO_DIR, shell=True)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        err("已取消")
        raise SystemExit(130)
