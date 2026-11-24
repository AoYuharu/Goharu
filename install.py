#!/usr/bin/env python3
"""Goharu 交互式安装向导（仅标准库，跨 Windows / Linux / macOS）。

引导流程：
    1. 环境探测（Python / Git / Node / npm）
    2. 选择模型厂商 + 粘贴 API Key（不可见输入）
    3. 选择检索模式（是否安装 embedding/torch，>2GB）
    4. 选择运行方式（交互 TUI / Linux systemd 无头守护）
    5. 创建 venv + 安装 Python 依赖
    6. 构建 TUI（npm install + tsc）
    7. 写入 .env 与 config.yaml（所选厂商）
    8. 完成 → 可选立即启动

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
        "label": "自定义（Anthropic 兼容）",
        "model": "",
        "base_url": "",
        "env": "ANTHROPIC_API_KEY",
        "hint": "自行填写 base_url 与模型名",
    },
]


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


# ── 方向键菜单（TTY 下 ↑↓ 选择 + 回车确认；非 TTY 退回数字输入）──────────
def _read_key() -> str:
    """跨平台读单键，归一化为 up/down/enter/ctrl-c/字符。返回 '' 表示无关键。"""
    if IS_WIN:
        import msvcrt

        k = msvcrt.getwch()
        if k in ("\x00", "\xe0"):  # 特殊键前缀
            k2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(k2, "")
        if k in ("\r", "\n"):
            return "enter"
        if k == "\x03":
            return "ctrl-c"
        return k
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # 转义序列（方向键 \x1b[A / \x1b[B）
            if sys.stdin.read(1) == "[":
                return {"A": "up", "B": "down"}.get(sys.stdin.read(1), "")
            return ""
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _menu_select(msg: str, options: list[str], default: int) -> int:
    """TTY 方向键菜单：↑↓ 移动高亮、回车确认。用光标上移重绘实现，非全屏。"""
    idx = default
    n = len(options)
    print(f"{c('cyan', '?')} {msg}  {c('dim', '（↑↓ 选择，回车确认）')}")
    for _ in options:
        print()  # 占位行

    def render() -> None:
        sys.stdout.write(f"\x1b[{n}A")  # 光标上移 n 行
        for i, opt in enumerate(options):
            sel = i == idx
            marker = c("green", "❯") if sel else " "
            line = f"{marker} {c('bold', opt) if sel else opt}"
            sys.stdout.write("\x1b[2K" + line + "\n")  # 清行后重写
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


def choose(msg: str, options: list[str], default: int = 0) -> int:
    """选择菜单：TTY 用方向键，非 TTY（脚本/CI/管道）退回数字输入。"""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _menu_select(msg, options, default)
    print(f"{c('cyan', '?')} {msg}")
    for i, opt in enumerate(options, 1):
        marker = ">" if i - 1 == default else " "
        print(f"  {c('green', marker)} {c('bold', str(i))}. {opt}")
    while True:
        raw = ask(f"选择 (1-{len(options)})", str(default + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        err("无效选择，请重试")


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
    title("1/7 环境探测")
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


def step_choose_provider() -> dict:
    title("2/7 选择模型厂商")
    idx = choose(
        "选择一个 Anthropic 兼容端点（智能体以此模型思考）",
        [f"{p['label']}  —  {c('dim', p['hint'])}" for p in PROVIDERS],
        default=0,
    )
    p = dict(PROVIDERS[idx])
    if not p["model"]:  # 自定义
        p["model"] = ask("模型名", "claude-opus-4-7")
        p["base_url"] = ask("base_url", "https://api.anthropic.com")
        p["env"] = ask("API Key 环境变量名", "ANTHROPIC_API_KEY")

    key = ask_secret(f"粘贴 {p['label']} 的 API Key（写入 {p['env']}）")
    p["key"] = key
    if not key:
        warn("未填密钥，已跳过——智能体无法思考。可稍后编辑 .env 补上")
    else:
        info(f"将写入 {p['env']}=***{key[-4:] if len(key) >= 4 else ''}")
    return p


def step_choose_embedding() -> bool:
    title("3/7 检索模式")
    idx = choose(
        "向量检索可提升记忆召回精度，但需安装 torch 等重依赖（>2GB，仅 CPU）。不装则自动降级为 FTS5+BM25，功能完整。",
        [
            "轻量（推荐）— 纯文本 FTS5+BM25，快速安装",
            "增强 — 额外安装 torch + embedding 模型（>2GB）",
        ],
        default=0,
    )
    return idx == 1


def step_choose_runmode() -> bool:
    title("4/7 运行方式")
    if IS_WIN:
        info("Windows 暂以交互 TUI 为主（无 systemd），安装后用 start.bat 启动")
        return False
    idx = choose(
        "如何常驻运行？",
        [
            "交互 TUI（推荐）— start.sh 启动，前台会话",
            "无头守护 — 注册 systemd user 服务（开机自启 + 崩溃自拉）",
        ],
        default=0,
    )
    return idx == 1


def _venv_py() -> str:
    return os.path.join("venv", "Scripts", "python.exe") if IS_WIN else os.path.join("venv", "bin", "python")


def step_install_deps(with_embedding: bool) -> bool:
    title("5/7 安装 Python 依赖")
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
    title("6/7 构建 TUI")
    if not os.path.isfile(os.path.join(REPO_DIR, "tui-ts", "package.json")):
        warn("未发现 tui-ts，跳过 TUI 构建")
        return True
    tui_dir = os.path.join(REPO_DIR, "tui-ts")
    npm = "npm.cmd" if IS_WIN else "npm"
    return run_step("npm install + tsc 构建", f"{npm} install --silent && {npm} run build --silent", cwd=tui_dir)


def step_write_config(provider: dict) -> bool:
    title("7/7 写入配置")

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
        env_var = provider["env"]
        lines = open(env_path, encoding="utf-8").read().splitlines()
        out, found = [], False
        for ln in lines:
            if re.match(rf"^\s*{re.escape(env_var)}\s*=", ln):
                out.append(f"{env_var}={provider['key']}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"{env_var}={provider['key']}")
        open(env_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
        info(f"已写入 {env_var}")

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
        f'GOHARU_HOME="{repo}"\n'
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


def step_choose_channels() -> dict:
    """Channel 配置：↑↓ 选择进入对应项，回车开始输入；高级选项独立成组。

    QQ Bot 与 HaruNet 在 Channel 一栏；出站代理放进「高级选项」（用户主动回车
    进入后才出现输入框），交互与系统设置界面一致。
    """
    title("通道与代理（可选，都可跳过）")
    out = {"qq": None, "harunet": None, "proxy": ""}
    while True:
        idx = choose("Channel 配置（↑↓ 选择，回车进入；选「完成」继续安装）", [
            "QQ Bot（官方频道机器人）",
            "HaruNet 组网（接入密码 + hub）",
            "高级选项（出站代理等）",
            "完成，继续安装",
        ], default=3)
        if idx == 0:
            if confirm("配置 QQ Bot channel？", default=False):
                app_id = ask("QQBOT_APP_ID")
                secret = ask_secret("QQBOT_CLIENT_SECRET")
                if app_id and secret:
                    out["qq"] = {"app_id": app_id, "secret": secret}
                else:
                    warn("QQ app_id / secret 不全，跳过 QQ 配置")
        elif idx == 1:
            if confirm("接入 HaruNet 组网？（默认不接入：未接入的智能体对组网一无所知）", default=False):
                url = ask("HaruNet hub 地址", "ws://159.75.26.204:6000")
                name = ask("网络昵称（回车用 config.yaml 的 agentnet.name）", "")
                password = ask_secret("接入密码（hub --join-password，留空=仅白名单模式）")
                out["harunet"] = {"url": url, "name": name, "password": password}
                info("接入 = 密码（或白名单）+ 开关：若握手被拒，检查接入密码，"
                     "或把日志里的 agent_id 加入 hub 白名单")
        elif idx == 2:
            adv = choose("高级选项（↑↓ 选择，回车进入）", ["出站代理", "返回"], default=1)
            if adv == 0:
                out["proxy"] = ask("出站代理地址（如 socks5://127.0.0.1:7890，留空跳过）")
        else:
            break
    return out


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
    # QQ → .env（channel qq_main 已在默认 config 里）
    if ch.get("qq"):
        env_path = os.path.join(REPO_DIR, ".env")
        lines = (open(env_path, encoding="utf-8").read().splitlines()
                 if os.path.exists(env_path) else [])
        lines = _set_env_line(lines, "QQBOT_APP_ID", ch["qq"]["app_id"])
        lines = _set_env_line(lines, "QQBOT_CLIENT_SECRET", ch["qq"]["secret"])
        open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        info("已写入 QQ Bot 凭据到 .env")
    cfg_path = os.path.join(REPO_DIR, "config.yaml")
    # HaruNet 接入开关：.env AGENTNET_ENABLED=true（机器级覆盖；config 默认 false）
    if ch.get("harunet"):
        env_path = os.path.join(REPO_DIR, ".env")
        lines = (open(env_path, encoding="utf-8").read().splitlines()
                 if os.path.exists(env_path) else [])
        lines = _set_env_line(lines, "AGENTNET_ENABLED", "true")
        if ch["harunet"].get("name"):
            lines = _set_env_line(lines, "AGENTNET_NAME", ch["harunet"]["name"])
        if ch["harunet"].get("password"):
            lines = _set_env_line(lines, "AGENTNET_PASSWORD", ch["harunet"]["password"])
        open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        info("已写入 AGENTNET_ENABLED=true 到 .env")
    if not os.path.exists(cfg_path):
        return
    txt = open(cfg_path, encoding="utf-8").read()
    changed = False
    # HaruNet → agentnet.url（config 里唯一 ws:// 的那行）
    if ch.get("harunet"):
        hub_url = ch["harunet"]["url"]
        t2 = re.sub(r"(?m)^(\s*url:\s*)ws://\S+",
                    lambda m: m.group(1) + hub_url, txt, count=1)
        if t2 != txt:
            txt, changed = t2, True
            info(f"agentnet.url → {hub_url}")
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

    provider = step_choose_provider()
    channels = step_choose_channels()
    with_embedding = step_choose_embedding()
    with_systemd = step_choose_runmode()

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
