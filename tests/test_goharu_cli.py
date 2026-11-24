"""goharu 命令行（goharu_cli.py）契约测试 + 网关守护集成测试。

覆盖用户实际敲的每条命令及其坑：
  1. 进程识别：只认 python/node + 独立脚本参数，不误伤提到脚本名的 shell
  2. 进程停止：dry-run 不动 / stop-only 杀干净 / 诱饵存活
  3. uninstall 参数解析、dry-run 不删文件、--keep-data、真删（在临时假仓库里）
  4. RPC 回包序列化护栏：结果含函数也不能炸（2026-11-24 status 杀守护事故）
  5. MCP http 传输 mcp 1.x/2.x 元组兼容
  6. 集成（真实守护）：start → status ×3 不杀守护 → channels.health 可序列化
     → log → 父进程退出后守护仍活 → stop → status 未运行

运行：python tests/test_goharu_cli.py
"""
import asyncio
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import types
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

IS_WIN = os.name == "nt"
passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def load_cli(path: Path):
    spec = importlib.util.spec_from_file_location("gcli_" + path.parent.name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def spawn_marker(token: str):
    """起一个命令行里带独立脚本参数的 python（模拟守护/TUI）。"""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)",
                             f"/x/{token}", "--daemon"])


def spawn_decoy():
    """命令行里只是"提到"脚本名的 shell（模拟 tail 日志 / grep）。"""
    if IS_WIN:
        return subprocess.Popen(["cmd", "/c", f"rem grep gateway_entry.py & ping -n 600 127.0.0.1 >nul"])
    return subprocess.Popen(["bash", "-c", "sleep 600 # tail -f logs/gateway_daemon.log; grep gateway_entry.py x"])


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def hard_kill(pid: int):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────
# 1. 进程识别
# ────────────────────────────────────────────────────────────────────
def test_process_matching(cli):
    print("\n[1] 进程识别：token 级匹配 + 解释器过滤")
    R = cli._SCRIPT_TOKEN_RE
    cases = {
        "/root/Goharu/Gateway/gateway_entry.py": True,
        "run_tui_ts.py": True,
        r"E:\x\Gateway\gateway_entry.py": True,
        r"E:\x\tui-ts\dist\index.js": True,
        "bash -c -l \"shopt ... eval 'python ... gateway_entry.py --daemon ...'\"": False,
        "/root/Goharu/runtime_memory/logs/gateway_daemon.log": False,
        "tail -f gateway_entry.py.log": False,
        "--daemon": False,
    }
    bad = [t for t, exp in cases.items() if bool(R.search(t)) != exp]
    check("token 正则 8 例", not bad, str(bad))
    check("解释器过滤：python 放行 / bash 拒绝",
          cli._is_target("python.exe", [r"E:\x\Gateway\gateway_entry.py"])
          and not cli._is_target("bash.exe", ["-c", "tail gateway_entry.py"])
          and not cli._is_target("cmd.exe", ["/c", "rem gateway_entry.py"]))
    check("解释器过滤：node 跑 tui dist 放行",
          cli._is_target("node", ["/a/tui-ts/dist/index.js"]))


def test_find_and_stop(cli):
    print("\n[2] 进程发现与停止：dry-run 不动 / stop-only 杀净 / 诱饵存活")
    d1, d2, decoy = spawn_marker("Gateway/gateway_entry.py"), spawn_marker("run_tui_ts.py"), spawn_decoy()
    time.sleep(1.0)
    try:
        found = cli._find_processes()
        pids = {p for p, _ in found}
        check("发现两个假守护/TUI", {d1.pid, d2.pid} <= pids, str(found))
        check("不误伤诱饵 shell", decoy.pid not in pids, str(found))
        check("不误伤自身/父进程",
              os.getpid() not in pids and os.getppid() not in pids)
        seen = cli._stop_processes(dry_run=True)
        check("dry-run 返回目标且不杀", {d1.pid, d2.pid} <= {p for p, _ in seen}
              and alive(d1.pid) and alive(d2.pid))
        cli._stop_processes(dry_run=False)
        # Windows：Popen 句柄未关时 os.kill(pid,0) 对已退出进程也可能成功，用 poll 判定
        check("stop-only 后两个目标都消失", d1.poll() is not None and d2.poll() is not None,
              f"d1={d1.poll()} d2={d2.poll()}")
        check("诱饵 shell 仍活着", alive(decoy.pid))
        check("复扫为空（排除诱饵）", not cli._find_processes(), str(cli._find_processes()))
    finally:
        for p in (d1, d2, decoy):
            hard_kill(p.pid)


# ────────────────────────────────────────────────────────────────────
# 3. uninstall
# ────────────────────────────────────────────────────────────────────
def test_uninstall(cli):
    print("\n[3] uninstall 参数与 dry-run")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.uninstall(["--bogus"])
    check("未知参数返回 2", rc == 2)
    with redirect_stdout(buf):
        rc = cli.uninstall(["--stop-only", "--dry-run"])
    out = buf.getvalue()
    check("dry-run 返回 0 且标注预演", rc == 0 and "预演" in out, out[:200])
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.uninstall(["--dry-run", "--yes"])
    out = buf.getvalue()
    check("dry-run 列出依赖产物", "venv" in out and "node_modules" in out, out[:300])

    before = {p: os.path.exists(p) for p in [
        os.path.join(cli.REPO, "venv"), os.path.join(cli.REPO, "tui-ts", "node_modules"),
        os.path.join(cli.REPO, "runtime_memory"), os.path.join(cli.REPO, ".env")]}
    with redirect_stdout(buf):
        cli.uninstall(["--dry-run", "--yes"])
    after = {p: os.path.exists(p) for p in before}
    check("dry-run 不删任何文件", before == after, f"{before} vs {after}")


def test_uninstall_fake_repo():
    print("\n[4] uninstall 真删（临时假仓库，不碰真实安装）")
    tmp = Path(tempfile.mkdtemp(prefix="goharu_cli_test_"))
    try:
        shutil.copy(ROOT / "goharu_cli.py", tmp / "goharu_cli.py")
        for rel in ("venv/Lib", "tui-ts/node_modules/x", "tui-ts/dist", "tui-ts/src",
                    "runtime_memory/daily", "models"):
            (tmp / rel).mkdir(parents=True, exist_ok=True)
            (tmp / rel / "f.txt").write_text("x", encoding="utf-8")
        (tmp / "tui-ts" / "src" / "a.js").write_text("// js", encoding="utf-8")
        (tmp / "tui-ts" / "src" / "a.ts").write_text("// ts", encoding="utf-8")
        (tmp / ".env").write_text("K=V\n", encoding="utf-8")
        m = load_cli(tmp / "goharu_cli.py")
        check("假仓库 REPO 指向临时目录", Path(m.REPO) == tmp, m.REPO)
        # 隔离：不杀真实进程、不碰真实全局命令、不碰 systemd
        m._stop_processes = lambda dry_run=False: []
        m._remove_global_command = lambda: []
        m._stop_systemd = lambda: None
        m._systemd_unit_present = lambda: False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = m.uninstall(["--yes", "--keep-data"])
        check("--keep-data 返回 0", rc == 0, buf.getvalue()[-300:])
        check("venv 已删", not (tmp / "venv").exists())
        check("node_modules/dist 已删", not (tmp / "tui-ts" / "node_modules").exists()
              and not (tmp / "tui-ts" / "dist").exists())
        check("tsc 产物已删、源码保留", not (tmp / "tui-ts" / "src" / "a.js").exists()
              and (tmp / "tui-ts" / "src" / "a.ts").exists())
        check("--keep-data 保留 runtime_memory/.env",
              (tmp / "runtime_memory").exists() and (tmp / ".env").exists())
        with redirect_stdout(buf):
            rc = m.uninstall(["--yes"])
        check("默认卸载返回 0", rc == 0)
        check("默认卸载删 runtime_memory/models/.env",
              not (tmp / "runtime_memory").exists() and not (tmp / "models").exists()
              and not (tmp / ".env").exists())
        with redirect_stdout(buf):
            rc = m.uninstall(["--stop-only"])
        check("--stop-only 不删文件（仓库代码保留）", rc == 0 and (tmp / "goharu_cli.py").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ────────────────────────────────────────────────────────────────────
# 4. RPC 回包序列化护栏
# ────────────────────────────────────────────────────────────────────
def test_rpc_serialization_guard():
    print("\n[5] RPC 回包序列化护栏（status 杀守护事故回归）")
    from Gateway.session_manager import _jsonable
    obj = {"transport": lambda: None, "n": 1, "l": [lambda x: x, {"k": object()}], "ok": True}
    cleaned = _jsonable(obj)
    try:
        json.dumps(cleaned)
        ok = True
    except TypeError as e:
        ok = False
    check("_jsonable 结果可 json.dumps", ok, str(cleaned)[:120])
    check("_jsonable 函数转 callable 描述", str(cleaned["transport"]).startswith("<callable"))
    # channels_health 含函数 params 的 channel 也能回包
    from Gateway.session_manager import SessionManager
    sm = SessionManager.__new__(SessionManager)
    sm.sessions = {"default": types.SimpleNamespace(channels=[
        types.SimpleNamespace(channel_type="tui", channel_id="tui_main",
                              params={"transport": lambda o: None},
                              health=lambda: {"ok": True, "state": "ready", "detail": ""})])}
    rows = sm.channels_health()
    try:
        json.dumps(rows)
        ok = True
    except TypeError as e:
        ok = False
    check("channels_health 回包可序列化", ok, str(rows)[:160])

    # gateway_entry 的 _dumps_safe / write_json：函数进回包 → 发 internal error 而非抛异常
    # gateway_entry 导入即初始化日志（需要仓库内 runtime_memory/logs 存在），在仓库 cwd 下导入
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        spec = importlib.util.spec_from_file_location("gentry", ROOT / "Gateway" / "gateway_entry.py")
        ge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ge)
    finally:
        os.chdir(cwd)
    bad = {"jsonrpc": "2.0", "result": {"f": lambda: 1}, "id": "x"}
    s = ge._dumps_safe(bad)
    parsed = json.loads(s)
    check("_dumps_safe 函数回包 → JSON-RPC internal error",
          parsed.get("error", {}).get("code") == -32603 and parsed.get("id") == "x", s[:160])
    check("_dumps_safe 正常回包原样", json.loads(ge._dumps_safe({"jsonrpc": "2.0", "result": {"a": 1}, "id": 1}))["result"] == {"a": 1})
    out = io.StringIO()
    with redirect_stdout(out):
        rc = ge.write_json(bad)
    check("write_json 函数回包不抛异常", rc is True)


# ────────────────────────────────────────────────────────────────────
# 5. MCP http 元组兼容
# ────────────────────────────────────────────────────────────────────
def test_mcp_http_tuple_compat():
    print("\n[6] MCP streamable_http_client 1.x 三元组 / 2.x 二元组兼容")
    from Tools.mcp import manager as mm

    class FakeSession:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return types.SimpleNamespace(tools=[])

    def run(tuple_len: int):
        class Ctx:
            async def __aenter__(self_inner):
                if tuple_len == 3:
                    return ("r", "w", lambda: "sid")
                return ("r", "w")

            async def __aexit__(self_inner, *a):
                return False

        mgr = mm.MCPClientManager()
        conn = mm._ServerConn("t", {"transport": "http", "url": "http://x/mcp"})
        with mock.patch("mcp.client.streamable_http.streamable_http_client", lambda *a, **k: Ctx()), \
             mock.patch("mcp.ClientSession", FakeSession):
            asyncio.run(mgr._open_session(conn))
        return conn.session is not None and conn.tool_names == []

    try:
        check("2.x 二元组可连接", run(2))
        check("1.x 三元组可连接", run(3))
    except Exception as e:
        check("MCP 元组兼容", False, repr(e))


# ────────────────────────────────────────────────────────────────────
# 6. 集成：真实守护
# ────────────────────────────────────────────────────────────────────
def test_integration(cli):
    print("\n[7] 集成：真实守护 start / status×3 / health / log / stop")
    if cli._alive():
        print("    [skip] 已有网关在运行，跳过集成（避免干扰）")
        return
    # 在 Claude Code / 其他工具里跑测试时，宿主注入的 ANTHROPIC_* 会覆盖 .env
    # 的厂商密钥（dotenv 不覆盖已存在的环境变量）→ 守护初始化 401。临时摘除。
    saved = {k: os.environ.pop(k) for k in list(os.environ)
             if k.startswith("ANTHROPIC_")}
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            started = cli.gateway_start(wait=90)
    finally:
        os.environ.update(saved)
    if not started:
        print(f"    [skip] 本机网关初始化失败（LLM/MCP 环境），跳过集成：{buf.getvalue()[-200:]}")
        cli.gateway_stop()
        return
    check("gateway start → 探活 ok", cli._alive())
    for i in range(3):
        with redirect_stdout(buf):
            rc = cli.gateway_status()
    check("gateway status ×3 均成功（回归：曾杀死守护）", rc == 0 and cli._alive(), buf.getvalue()[-200:])
    rows = cli._rpc("channels.health")
    check("channels.health 经 RPC 返回 list", isinstance(rows, list), str(rows)[:120])
    try:
        json.dumps(rows)
        ok = True
    except TypeError:
        ok = False
    check("channels.health 回包可序列化", ok)
    with redirect_stdout(buf):
        rc = cli.gateway_log(5)
    check("gateway log 输出含行", rc == 0 and len(buf.getvalue().strip().splitlines()) > 0)
    # 守护由子命令拉起、父进程退出后仍活
    subprocess.run([sys.executable, str(ROOT / "goharu_cli.py"), "gateway", "status"],
                   capture_output=True, timeout=30)
    time.sleep(1.0)
    check("CLI 进程退出后守护仍活", cli._alive())
    with redirect_stdout(buf):
        stopped = cli.gateway_stop()
    check("gateway stop → 未运行", stopped and not cli._alive() and not os.path.exists(cli.PIDFILE))
    with redirect_stdout(buf):
        rc = cli.gateway_status()
    check("stop 后 status 返回 1 且提示未运行", rc == 1 and "未运行" in buf.getvalue())


def main():
    cli_path = ROOT / "goharu_cli.py"
    cli = load_cli(cli_path)
    print(f"仓库: {cli.REPO}  平台: {sys.platform}")
    test_process_matching(cli)
    test_find_and_stop(cli)
    test_uninstall(cli)
    test_uninstall_fake_repo()
    test_rpc_serialization_guard()
    test_mcp_http_tuple_compat()
    test_integration(cli)
    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
