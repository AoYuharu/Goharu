#!/usr/bin/env python3
"""Goharu 交互式安装向导（仅标准库，跨 Windows / Linux / macOS）。

交互形态（TTY）：**多栏配置表单**——←→ 切换栏（模型 / 检索 / 运行 / 通道 / 高级 / 执行），
↑↓ 选栏内条目，Enter 切换布尔与选项、或展开输入框；最后一栏「执行」里选「开始安装」。
非 TTY（脚本/CI/管道）自动退化为顺序问答。

引导流程：
    1. 环境探测（Python / Git / Node / npm）
    2. 多栏表单：模型厂商 + API Key、检索模式、运行方式、QQ Bot / HaruNet / 代理
    3. 创建 venv + 安装 Python 依赖
    4. 构建 TUI（npm install + tsc）
    5. 写入 .env 与 config.yaml
    6. 完成 → 可选立即启动

幂等：可重复执行，已完成的步骤自动跳过。无任何第三方依赖，
因此可在 venv 建好之前直接以系统 Python 运行（bootstrap 入口即此）。
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata

IS_WIN = os.name == "nt"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PIP_MIRROR = os.environ.get("PIP_MIRROR", "https://pypi.tuna.tsinghua.edu.cn/simple")

# ── 模型厂商表（与 config.yaml 的 model_history 对齐）────────────────
PROVIDERS = [
    {
        "label": "DeepSeek",
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/anthropic",
        "env": "ZHIPU_API_KEY",
        "hint": "国内直连，便宜快速",
    },
    {
        "label": "Kimi",
        "model": "kimi-k3",
        "base_url": "https://api.kimi.com/coding/",
        "env": "ZHIPU_API_KEY",
        "hint": "Moonshot coding 端点",
    },
    {
        "label": "智谱 GLM",
        "model": "glm-5.3-Flash",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "env": "ZHIPU_API_KEY",
        "hint": "智谱 Anthropic 兼容端点",
    },
    {
        "label": "MiniMax",
        "model": "MiniMax-M2.7",
        "base_url": "https://api.minimaxi.com/anthropic",
        "env": "ANTHROPIC_API_KEY",
        "hint": "MiniMax 官方 Anthropic 兼容端点",
    },
    {
        "label": "自定义",
        "model": "",
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


# ── 键盘读取（跨平台，归一化为 up/down/left/right/enter/ctrl-c/字符）────
def _read_key() -> str:
    if IS_WIN:
        import msvcrt

        k = msvcrt.getwch()
        if k in ("\x00", "\xe0"):  # 特殊键前缀
            k2 = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(k2, "")
        if k in ("\r", "\n"):
            return "enter"
        if k == "\x03":
            return "ctrl-c"
        if k == "\t":
            return "right"
        return k
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # 转义序列（方向键 \x1b[A/B/C/D）
            nxt = sys.stdin.read(1)
            if nxt == "[":
                code = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(code, "")
            return "escape"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\t":
            return "right"
        if ch == "\x03":
            return "ctrl-c"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


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


def _plain(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _dwidth(s: str) -> int:
    """显示宽度（CJK 全角按 2 列），忽略 ANSI 转义。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in _plain(s))


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dwidth(s))


def _fit(text: str, width: int) -> str:
    """按显示宽度截断（去 ANSI 后切），超出以 … 结尾——防窄终端撑破边框。"""
    if _dwidth(text) <= width:
        return text
    out, w = "", 0
    for ch in _plain(text):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def _mask_secret(secret: str) -> str:
    if not secret:
        return "未设置"
    return "****" + secret[-4:] if len(secret) > 4 else "****"


def _default_values() -> dict:
    return {
        "provider": 0, "api_key": "", "model": "", "base_url": "",
        "embedding": False,
        "systemd": False,
        "qq": False, "qq_app_id": "", "qq_secret": "",
        "harunet": False, "harunet_url": "ws://159.75.26.204:6000",
        "harunet_name": "", "harunet_password": "",
        "proxy": "",
    }


def _form_sections() -> list[dict]:
    """栏 → 条目。type: bool / choice / str / secret / action；show_if 控制按需显示。"""
    return [
        {"title": "模型", "items": [
            {"key": "provider", "label": "厂商", "type": "choice",
             "options": PROVIDER_LABELS, "hint": "Enter 切换厂商（Anthropic 兼容端点）"},
            {"key": "api_key", "label": "API Key", "type": "secret",
             "hint": "粘贴密钥（输入不可见）；留空可稍后编辑 .env"},
            {"key": "model", "label": "模型名", "type": "str",
             "placeholder": "（自定义厂商时填写）", "hint": "Enter 输入模型名",
             "show_if": lambda v: v["provider"] == len(PROVIDERS) - 1},
            {"key": "base_url", "label": "API 地址", "type": "str",
             "placeholder": "（自定义厂商时填写）", "hint": "Enter 输入 base_url",
             "show_if": lambda v: v["provider"] == len(PROVIDERS) - 1},
        ]},
        {"title": "检索", "items": [
            {"key": "embedding", "label": "向量检索（embedding/torch，>2GB）", "type": "bool",
             "hint": "Enter 切换；关闭则用 FTS5+BM25（功能完整，安装快）"},
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
        ]},
        {"title": "执行", "items": [
            {"key": "__go", "label": "▶ 开始安装", "type": "action",
             "hint": "Enter 按当前配置开始安装"},
            {"key": "__quit", "label": "✕ 退出", "type": "action", "hint": "Enter 退出向导"},
        ]},
    ]


def _visible(items: list[dict], values: dict) -> list[dict]:
    return [it for it in items if "show_if" not in it or it["show_if"](values)]


def _fmt_value(item: dict, values: dict) -> str:
    v = values.get(item["key"], item.get("default"))
    t = item["type"]
    if t == "bool":
        return c("green", "是") if v else c("dim", "否")
    if t == "choice":
        return c("bold", item["options"][int(v)])
    if t == "secret":
        return c("green", _mask_secret(v)) if v else c("dim", "（未设置）")
    if t == "action":
        return c("bold", item.get("value", ""))
    if v:
        return str(v)
    return c("dim", item.get("placeholder", "（未设置）"))


def _render_frame(sections: list[dict], values: dict, sec_i: int, item_i: int,
                  vis: list[dict]) -> list[str]:
    cols = shutil.get_terminal_size((80, 24)).columns
    W = max(50, min(cols - 2, 70))
    inner = W - 2  # 两条竖边之间的内容宽度
    bar = "─" * (W - 2)
    L = [c("cyan", "╭" + bar + "╮"),
         c("cyan", "│") + " " + _pad(c("bold", "Goharu 安装向导"), inner - 1) + c("cyan", "│"),
         c("cyan", "├" + bar + "┤")]
    tabs = ""
    for i, s in enumerate(sections):
        t = f" {s['title']} "
        tabs += (c("reverse", c("bold", t)) if i == sec_i else c("dim", t)) + " "
    L.append(c("cyan", "│") + " " + _pad(_fit(tabs, inner - 1), inner - 1) + c("cyan", "│"))
    L.append(c("cyan", "├" + bar + "┤"))
    if not vis:
        L.append(c("cyan", "│") + " " + _pad(c("dim", "（无项目）"), inner - 1) + c("cyan", "│"))
    for j, it in enumerate(vis):
        sel = j == item_i
        row = ("  " + ("› " if sel else "  ") + _pad(it["label"], 26) + " "
               + _fit(_fmt_value(it, values), inner - 32))
        if sel:
            row = c("reverse", c("bold", _plain(row)))
        L.append(c("cyan", "│") + " " + _pad(row, inner - 1) + c("cyan", "│"))
    L.append(c("cyan", "├" + bar + "┤"))
    hint = vis[item_i].get("hint", "") if vis else ""
    L.append(c("cyan", "│") + " " + _pad(c("dim", _fit(hint, inner - 1)), inner - 1) + c("cyan", "│"))
    keys = "←→ 切换栏 · ↑↓ 选择 · Enter 编辑/切换 · Ctrl+C 退出"
    L.append(c("cyan", "│") + " " + _pad(c("dim", keys), inner - 1) + c("cyan", "│"))
    L.append(c("cyan", "╰" + bar + "╯"))
    return L


def _edit_item(item: dict, values: dict, tty: bool) -> None:
    """编辑一个条目。TTY：布尔/选项用 Enter 切换；非 TTY：布尔/选项真正询问。"""
    k = item["key"]
    t = item["type"]
    cur = values.get(k, item.get("default"))
    if t == "bool":
        values[k] = (not cur) if tty else confirm(item["label"], default=bool(cur))
    elif t == "choice":
        if tty:
            values[k] = (int(cur) + 1) % len(item["options"])
        else:
            values[k] = choose(item["label"], item["options"], default=int(cur))
    elif t == "secret":
        nv = ask_secret(f"{item['label']}（回车保持不变；输入 - 清空）")
        if nv == "-":
            values[k] = ""
        elif nv:
            values[k] = nv
    elif t == "str":
        nv = ask(item["label"], str(cur or item.get("default", "") or ""))
        values[k] = nv.strip()
    # action 由调用方处理


def _form_tty(sections: list[dict], values: dict) -> dict:
    """TTY 多栏表单主循环：返回填好的 values；用户选「退出」则 SystemExit。"""
    sec_i, item_i = 0, 0
    drawn = 0
    extra = 0   # 边框下方多出的行（编辑提示行），重绘时一并上移
    while True:
        vis = _visible(sections[sec_i]["items"], values)
        item_i = max(0, min(item_i, len(vis) - 1))
        lines = _render_frame(sections, values, sec_i, item_i, vis)
        up = drawn + extra
        if up:
            sys.stdout.write(f"\x1b[{up}A")
        extra = 0
        sys.stdout.write("\x1b[J")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        drawn = len(lines)

        k = _read_key()
        if k == "ctrl-c":
            print()
            raise SystemExit(130)
        if k in ("left",):
            sec_i = (sec_i - 1) % len(sections)
            item_i = 0
        elif k in ("right",):
            sec_i = (sec_i + 1) % len(sections)
            item_i = 0
        elif k == "up" and vis:
            item_i = (item_i - 1) % len(vis)
        elif k == "down" and vis:
            item_i = (item_i + 1) % len(vis)
        elif k == "enter" and vis:
            item = vis[item_i]
            if item["type"] == "action":
                if item["key"] == "__go":
                    print()
                    return values
                print()
                raise SystemExit(0)
            _edit_item(item, values, tty=True)
            extra = 1   # ask()/ask_secret() 在边框下留了一行提示
            # 条目显隐可能随值变化，夹紧选中项
            vis2 = _visible(sections[sec_i]["items"], values)
            item_i = max(0, min(item_i, len(vis2) - 1))


def _form_fallback(sections: list[dict], values: dict) -> dict:
    """非 TTY：顺序问答，覆盖同一份表单定义。"""
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
            _edit_item(item, values, tty=False)
            shown = _plain(_fmt_value(item, values))
            print(f"      → {item['label']}: {shown}")
    return values


# ── 表单 → 步骤参数 ─────────────────────────────────────────────────────
def _provider_from_form(values: dict) -> dict:
    p = dict(PROVIDERS[int(values["provider"])])
    if not p["model"]:  # 自定义
        p["model"] = values["model"] or "claude-opus-4-7"
        p["base_url"] = values["base_url"] or "https://api.anthropic.com"
    p["key"] = values["api_key"]
    return p


def _channels_from_form(values: dict) -> dict:
    ch = {"qq": None, "harunet": None, "proxy": values["proxy"]}
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


# ── 步骤实现 ─────────────────────────────────────────────────────────
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
    node = shutil.which("node")
    npm = shutil.which("npm")
    node_major = None
    node_ver = ""
    if node:
        node_ver = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
        mver = re.match(r"v?(\d+)", node_ver)
        node_major = int(mver.group(1)) if mver else None
    if node and npm and node_major and node_major >= 20:
        info(f"Node {node_ver} + npm")
    else:
        err(f"需要 Node.js >= 20（当前 {node_ver or '未安装'}；TUI 依赖 ink/string-width 的 /v 正则旗标）")
        if IS_WIN:
            print("      安装: winget install OpenJS.NodeJS.LTS   或  https://nodejs.org")
        else:
            print("      安装: https://nodejs.org  或  nvm install 20")
        ok = False

    return ok


def _venv_py() -> str:
    return os.path.join("venv", "Scripts", "python.exe") if IS_WIN else os.path.join("venv", "bin", "python")


def step_install_deps(with_embedding: bool) -> bool:
    title("安装 Python 依赖")
    if not os.path.isdir(os.path.join(REPO_DIR, "venv")):
        # 用当前解释器建 venv，保证与运行向导的 Python 版本一致
        if not run_step("创建虚拟环境 venv", f'"{sys.executable}" -m venv venv'):
            err("venv 创建失败。Debian/Ubuntu 先: sudo apt install python3-venv")
            return False
    else:
        info("venv 已存在，跳过创建")

    vp = _venv_py()
    if not run_step("升级 pip", f"{vp} -m pip install --upgrade pip -q --progress-bar off -i {PIP_MIRROR}"):
        return False
    if not run_step("安装核心依赖", f"{vp} -m pip install --progress-bar off -r requirements.txt -i {PIP_MIRROR}"):
        return False
    if with_embedding:
        if not run_step(
            "安装 embedding 重依赖（torch 等，>2GB，请耐心）",
            f"{vp} -m pip install --progress-bar off -r requirements-embedding.txt -i {PIP_MIRROR}",
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
    content = (
        "#!/usr/bin/env bash\n"
        "# Goharu 全局启动器（install.py 生成）\n"
        f'GOHARU_HOME="{repo}\n'
        'cd "$GOHARU_HOME" || exit 1\n'
        # gateway 子进程要用 venv python（带依赖），否则落到系统 python3 崩在 import
        f'export PYTHON="{venv_py}"\n'
        'set -a; [ -f "$GOHARU_HOME/.env" ] && . "$GOHARU_HOME/.env"; set +a\n'
        f'exec "{venv_py}" "$GOHARU_HOME/goharu_cli.py" "$@"\n'
    )
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
    """把 QQ/HaruNet/代理 写入 .env 与 config.yaml（文本级，跨平台）。"""
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

    # ── 多栏配置表单（←→ 切栏 / ↑↓ 选条目 / Enter 编辑；非 TTY 顺序问答）──
    sections = _form_sections()
    values = _default_values()
    if _is_tty():
        print()
        print(c("dim", "  下方为配置表单：←→ 切换栏，↑↓ 选择条目，Enter 编辑；到「执行」栏开始安装"))
        values = _form_tty(sections, values)
    else:
        values = _form_fallback(sections, values)

    provider = _provider_from_form(values)
    channels = _channels_from_form(values)
    with_embedding = bool(values["embedding"])
    with_systemd = bool(values["systemd"])

    if not step_install_deps(with_embedding):
        err("依赖安装失败，可重跑本向导（已完成步骤会自动跳过）")
        return 1

    if not step_build_tui():
        warn("TUI 构建失败，可稍后手动: cd tui-ts && npm install && npm run build")

    step_write_config(provider)
    step_write_channels(channels)
    if with_systemd:
        step_setup_systemd()
    step_register_command()

    title("安装完成")
    if IS_WIN:
        print(f"    启动: 新开一个终端，输入 {c('green', 'goharu')}（任意目录均可）")
    else:
        print(f"    启动: 当前终端先执行 {c('green', 'source ~/.bashrc')} 再敲 {c('green', 'goharu')}；")
        print(f"          或直接重开一个终端敲 {c('green', 'goharu')}（PATH 已写入 shell 配置）")
    if with_systemd and not IS_WIN:
        print(f"    守护: {c('green', 'systemctl --user start goharu')}")
    if not provider.get("key"):
        print(f"    {c('yellow', '提醒: 尚未配置 LLM 密钥，编辑 .env 后再启动')}")
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
