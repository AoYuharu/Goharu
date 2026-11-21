"""跨平台修复的验证测试（Windows 可跑，POSIX 分支用 mock 模拟）。

覆盖 4 个修复：
  A. core_tools.run_cmd 的 Popen start_new_session 标志（Windows=False / 模拟 POSIX=True）
  B. platform_utils.kill_process_tree 的 POSIX killpg 分支（模拟）
     —— A+B 组合不变量：start_new_session=True 使子进程自立进程组，
        killpg(getpgid(child)) 只杀子进程组，不再误杀 agent 自身
  C. run_tui_ts.py 自动构建调用（Windows shell=True / 模拟 POSIX shell=False + argv 完整）
  D. HybridRetriever FTS5 特殊 token 查询不崩溃（真实 SQLite，临时目录）
  E. run_cmd 真实执行 git --version（验证 start_new_session 参数在 Windows 不破坏真实链路）
"""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 工具：假 Popen ─────────────────────────────────────────────────────

class _FakeStream:
    def readline(self):
        return ""


class _FakePopen:
    """捕获 Popen kwargs，立即 EOF、立即退出。"""

    captured = []

    def __init__(self, *args, **kwargs):
        _FakePopen.captured.append({"args": args, "kwargs": kwargs})
        self.pid = 12345
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass

    def communicate(self, timeout=None):
        return "", ""


def _run_cmd_capture(cmd, is_windows_value):
    """在指定 is_windows 下执行 run_cmd（Popen 打桩），返回捕获的 kwargs。"""
    from Tools.builtin import core_tools

    _FakePopen.captured = []
    with mock.patch.object(core_tools, "is_windows", return_value=is_windows_value), \
         mock.patch.object(core_tools.subprocess, "Popen", _FakePopen):
        asyncio.run(core_tools.run_cmd(cmd))
    assert _FakePopen.captured, "Popen 未被调用"
    return _FakePopen.captured[0]["kwargs"]


# ── A. run_cmd Popen start_new_session 标志 ────────────────────────────

def test_a_start_new_session_flag():
    kw_win = _run_cmd_capture("git --version", True)
    assert kw_win.get("start_new_session") is False, \
        f"Windows 下应为 False，实际 {kw_win.get('start_new_session')}"

    kw_posix = _run_cmd_capture("git --version", False)
    assert kw_posix.get("start_new_session") is True, \
        f"POSIX 下应为 True，实际 {kw_posix.get('start_new_session')}"
    print("[A] run_cmd start_new_session: Windows=False / POSIX=True OK")


# ── B. kill_process_tree POSIX 分支（模拟） ────────────────────────────

def test_b_kill_process_tree_posix():
    import Tools.platform_utils as pu

    calls = {"killpg": [], "kill": []}
    fake_getpgid = lambda pid: pid  # start_new_session 后子进程即组长：pgid == pid
    fake_killpg = lambda pgid, sig: calls["killpg"].append((pgid, sig))
    fake_kill = lambda pid, sig: calls["kill"].append((pid, sig))

    with mock.patch.object(pu, "is_windows", return_value=False), \
         mock.patch.object(pu.signal, "SIGKILL", 9, create=True), \
         mock.patch.object(pu.os, "killpg", fake_killpg, create=True), \
         mock.patch.object(pu.os, "getpgid", fake_getpgid, create=True), \
         mock.patch.object(pu.os, "kill", fake_kill):
        assert pu.kill_process_tree(12345) is True

    assert calls["killpg"] == [(12345, 9)], \
        f"应 killpg(子进程组, SIGKILL)，实际 {calls}"
    assert not calls["kill"], "killpg 成功时不应回退单杀"
    print("[B] kill_process_tree POSIX: killpg(子进程组) OK，无误杀 agent 自身进程组")

    # killpg 失败（进程组不存在）→ 回退 os.kill 单杀
    def raise_lookup(pgid, sig):
        raise ProcessLookupError

    calls = {"killpg": [], "kill": []}
    with mock.patch.object(pu, "is_windows", return_value=False), \
         mock.patch.object(pu.signal, "SIGKILL", 9, create=True), \
         mock.patch.object(pu.os, "killpg", raise_lookup, create=True), \
         mock.patch.object(pu.os, "getpgid", fake_getpgid, create=True), \
         mock.patch.object(pu.os, "kill", fake_kill):
        assert pu.kill_process_tree(12345) is True

    assert calls["kill"] == [(12345, 9)], f"回退单杀失败: {calls}"
    print("[B2] killpg 失败回退 os.kill 单杀 OK")


# ── C. run_tui_ts 自动构建调用 ─────────────────────────────────────────

def test_c_run_tui_ts_build_invocation():
    import run_tui_ts

    for simulated_os, expected_shell in (("nt", True), ("posix", False)):
        check_call_calls = []
        fake_dist = mock.Mock()
        fake_dist.exists.return_value = False
        fake_run_result = mock.Mock(returncode=0)

        with mock.patch.object(run_tui_ts.os, "name", simulated_os), \
             mock.patch.object(run_tui_ts, "DIST_ENTRY", fake_dist), \
             mock.patch.object(run_tui_ts.shutil, "which",
                               side_effect=lambda n: f"/fake/{n}" if n in ("node", "npm") else None), \
             mock.patch.object(run_tui_ts.subprocess, "check_call",
                               side_effect=lambda *a, **kw: check_call_calls.append((a, kw))), \
             mock.patch.object(run_tui_ts.subprocess, "run", return_value=fake_run_result):
            try:
                run_tui_ts.main()
            except SystemExit:
                pass

        assert check_call_calls, f"[{simulated_os}] check_call 未被调用"
        args, kwargs = check_call_calls[0]
        argv = args[0]
        assert list(argv) == ["/fake/npm", "run", "build"], \
            f"[{simulated_os}] argv 必须完整 [npm, run, build]，实际 {argv}"
        assert kwargs.get("shell") is expected_shell, \
            f"[{simulated_os}] shell 应为 {expected_shell}，实际 {kwargs.get('shell')}"
        print(f"[C] run_tui_ts 构建调用: os={simulated_os} → shell={expected_shell}, "
              f"argv=[npm, run, build] 完整 OK")


# ── D. FTS5 特殊 token 查询（真实 SQLite） ─────────────────────────────

def test_d_fts_special_tokens():
    from Memory.bridge import MemoryBridge

    tmp = tempfile.mkdtemp(prefix="fts_test_")
    try:
        mem = MemoryBridge(overrides={"memory.root_dir": str(Path(tmp) / "rm")})
        mem.append({"role": "user", "content": "工具退出码 exit_code 为 0 表示成功"})
        mem.ingest_atoms([{
            "atom_type": "fact", "subject": "tools",
            "canonical_text": "工具退出码 exit_code 为 0 表示执行成功",
            "salience": 0.8,
        }])

        # 旧实现会抛 OperationalError 的查询：列过滤语法、引号、百分号、下划线
        nasty_queries = [
            "exit_code 是什么",
            "a: b 列过滤语法",
            '"双引号" 注入',
            "100% 完成度",
            "tool_use {json: true}",
        ]
        for q in nasty_queries:
            pack = mem.retrieve(q)  # 不抛异常即通过
            assert isinstance(pack, dict) and "memories" in pack

        # 语义命中验证：exit_code 查询应命中刚写入的原子（FTS 分词一致）
        pack = mem.retrieve("exit_code 是什么")
        hits = [m.get("text", "") for m in pack["memories"]]
        assert any("exit_code" in t for t in hits), f"应命中 exit_code 原子，实际 {hits}"
        print(f"[D] FTS5 特殊 token 查询: {len(nasty_queries)} 条 nast query 无崩溃 + 语义命中 OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── E. run_cmd 真实执行（Windows 真实链路） ────────────────────────────

def test_e_run_cmd_real_execution():
    from Tools.builtin import core_tools

    result = json.loads(asyncio.run(core_tools.run_cmd("git --version")))
    assert "error" not in result, f"真实执行失败: {result}"
    assert result["exit_code"] == 0, f"exit_code 应为 0: {result}"
    assert "git version" in result["stdout"], f"stdout 异常: {result['stdout'][:100]}"
    print(f"[E] run_cmd 真实执行 git --version OK（{result['stdout'].strip()}）")


if __name__ == "__main__":
    test_a_start_new_session_flag()
    test_b_kill_process_tree_posix()
    test_c_run_tui_ts_build_invocation()
    test_d_fts_special_tokens()
    test_e_run_cmd_real_execution()
    print("\n=== 跨平台修复验证：全部通过 ===")
