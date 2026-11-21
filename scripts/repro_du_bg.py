"""复现 Goharu run_cmd 的 du 后台失败：同一命令分别走
A) 完整 handler 路径（CREATE_SUSPENDED + Job Object + resume + reader 线程）
B) 裸 Popen 对照
"""
import subprocess
import sys
import threading
import time

sys.path.insert(0, "E:/Goharu")
sys.stdout.reconfigure(encoding="utf-8")

CMD = "cd /e && du -sh -- */ 2>/dev/null | sort -rh"

from Tools.platform_utils import get_subprocess_creation_flags, get_subprocess_env, resolve_bash_path, is_windows
from Tools.process_sandbox import CommandJob


def run_full_path():
    """完全复刻 core_tools.run_cmd 的 spawn + 等待逻辑"""
    sandbox = CommandJob()
    env = get_subprocess_env()
    bash = resolve_bash_path()
    p = subprocess.Popen(
        [bash, "-c", CMD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=get_subprocess_creation_flags() | sandbox.creation_flags,
        start_new_session=not is_windows(),
    )
    t0 = time.time()
    if not sandbox.attach(p):
        return ("ATTACH_FAILED", "", "")
    out_lines, err_lines = [], []

    def _reader(stream, sink):
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
        except Exception:
            pass

    t_out = threading.Thread(target=_reader, args=(p.stdout, out_lines), daemon=True)
    t_err = threading.Thread(target=_reader, args=(p.stderr, err_lines), daemon=True)
    t_out.start()
    t_err.start()

    # 模拟后台 tracked 等待：30s 一片
    while True:
        try:
            p.wait(timeout=30.0)
            break
        except subprocess.TimeoutExpired:
            print(f"  [full] 已运行 {time.time()-t0:.0f}s，仍在跑...", flush=True)
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    sandbox.close()
    return (p.returncode, "".join(out_lines), "".join(err_lines))


def run_plain():
    bash = resolve_bash_path()
    p = subprocess.Popen(
        [bash, "-c", CMD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=get_subprocess_env(),
    )
    t0 = time.time()
    while True:
        try:
            p.wait(timeout=30.0)
            break
        except subprocess.TimeoutExpired:
            print(f"  [plain] 已运行 {time.time()-t0:.0f}s，仍在跑...", flush=True)
    out, err = p.communicate()
    return (p.returncode, out or "", err or "")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "full"
    fn = run_full_path if which == "full" else run_plain
    print(f"=== 模式: {which} ===", flush=True)
    rc, out, err = fn()
    print(f"exit_code={rc}", flush=True)
    print(f"stdout({len(out)} chars): {out[:200]!r}", flush=True)
    print(f"stderr({len(err)} chars): {err[:200]!r}", flush=True)
