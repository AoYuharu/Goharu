"""Git 状态提供器。

为主 Agent 和 Verify Agent 提供当前工作区的 Git 状态快照，
避免两者各自通过 run_cmd 重复执行 git 命令。
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional


# Windows 上 core.fscache / core.fsmonitor 依赖常驻守护进程，从 Python 子进程里
# 调 git 时该守护进程可能挂起，导致 git status 卡满 subprocess 的 timeout。
# 关闭这两个特性后 git 走纯索引扫描（本仓库仅 200+ 跟踪文件，实测 0.04s），
# 结果确定、绝无守护进程卡死风险。
_GIT_SAFE_FLAGS = ["-c", "core.fscache=false", "-c", "core.fsmonitor=false"]


class GitStatusProvider:
    """带短期缓存的 Git 状态提供者（非阻塞）。"""

    _cache: Optional[str] = None
    _cache_time: float = 0
    _ttl: float = 30.0  # 秒
    _fetching: bool = False

    @classmethod
    def get_status(cls, repo_path: Optional[str] = None) -> str:
        """获取格式化的 git status + diff --stat 文本。

        git 状态只是提示词的点缀信息，绝不能阻塞对话主链路——运行时进程里
        git 可能挂起（实测 >8s），所以完全非阻塞：缓存有效期内直接返回；
        过期或无缓存就后台刷新、立即返回旧缓存或 refreshing 占位。
        """
        now = time.time()
        if cls._cache is not None and now - cls._cache_time < cls._ttl:
            return cls._cache

        cls._start_refresh(repo_path)
        return cls._cache or "Git status unavailable: refreshing"

    @classmethod
    def _start_refresh(cls, repo_path: Optional[str]) -> None:
        """后台刷新缓存（非阻塞）。并发刷新合并，同一时刻只跑一个取数线程。"""
        if cls._fetching:
            return
        cls._fetching = True

        def _fetch():
            try:
                text = cls._fetch_uncached(repo_path)
            except Exception as exc:
                text = f"Git status unavailable: {exc}"
            cls._cache = text
            cls._cache_time = time.time()
            cls._fetching = False

        threading.Thread(target=_fetch, daemon=True, name="git-status-refresh").start()

    @classmethod
    def _fetch_uncached(cls, repo_path: Optional[str]) -> str:
        cwd = Path(repo_path).resolve() if repo_path else Path.cwd()
        status = cls._run(["git", "status", "--short"], cwd)
        diff_stat = cls._run(["git", "diff", "--stat"], cwd)
        head = cls._run(["git", "rev-parse", "--short", "HEAD"], cwd)
        branch = cls._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)

        parts = [
            f"Branch: {branch}",
            f"HEAD: {head}",
            "",
            "[Status --short]",
            status if status.strip() else "(working tree clean)",
            "",
            "[Diff --stat]",
            diff_stat if diff_stat.strip() else "(no changes)",
        ]
        return "\n".join(parts)

    @classmethod
    def get_verify_context(cls, repo_path: Optional[str] = None, touched_files: Optional[list] = None) -> str:
        """为 Verify 子 agent 预填充的 Git 上下文：status --short + 完整 diff。

        与 get_status 的差异：包含完整 diff（而非仅 --stat），供 Verify 静态审查
        改动并据此设计测试用例。touched_files 传入主 Agent 本轮 Write/Edit 过的
        文件绝对路径列表，diff 收敛到这些文件，避免脏仓库里混入无关历史改动。
        同样用守护线程做硬超时，绝不阻塞主链路。
        """
        holder: dict = {}

        def _fetch():
            try:
                holder["text"] = cls._fetch_verify_uncached(repo_path, touched_files)
            except Exception as exc:
                holder["text"] = f"Git context unavailable: {exc}"

        t = threading.Thread(target=_fetch, daemon=True, name="git-verify-context")
        t.start()
        t.join(timeout=2)
        if "text" not in holder:
            return "Git context unavailable: timeout"
        return holder["text"]

    @classmethod
    def _fetch_verify_uncached(cls, repo_path: Optional[str], touched_files: Optional[list]) -> str:
        cwd = Path(repo_path).resolve() if repo_path else Path.cwd()
        status = cls._run(["git", "status", "--short"], cwd)

        # 把主 Agent 本轮触及的绝对路径转成相对仓库根的正斜杠路径，再交给 git diff 做路径过滤
        scoped = None
        if touched_files:
            rel_paths = []
            for p in touched_files:
                try:
                    rel_paths.append(Path(p).resolve().relative_to(cwd).as_posix())
                except ValueError:
                    pass  # 不在仓库内的路径（如系统临时目录）跳过
            scoped = rel_paths or None

        if scoped:
            diff = cls._run(["git", "diff", "HEAD", "--", *scoped], cwd)
            diff_label = "[Diff（仅本轮触及文件）]"
        else:
            diff = cls._run(["git", "diff", "HEAD"], cwd)
            diff_label = "[Full diff]"

        head = cls._run(["git", "rev-parse", "--short", "HEAD"], cwd)
        branch = cls._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)

        # 完整 diff 可能很大，截断到合理长度，避免塞爆 Verify 上下文
        max_diff = 15000
        if len(diff) > max_diff:
            diff = diff[:max_diff] + "\n... [diff 过长已截断；可用 Read 工具查看具体文件]"

        parts = [
            f"Branch: {branch}",
            f"HEAD: {head}",
            "",
            "[Status --short]",
            status if status.strip() else "(working tree clean)",
            "",
            diff_label,
            diff if diff.strip() else "(no changes to tracked files)",
        ]
        return "\n".join(parts)

    @classmethod
    def invalidate(cls):
        """手动清除缓存。"""
        cls._cache = None
        cls._cache_time = 0

    @staticmethod
    def _run(cmd: list, cwd: Path) -> str:
        # 不用 PIPE：Windows 上子进程的孙进程会继承管道句柄，git 退出后管道不 EOF，
        # reader 线程永不结束，communicate 的 join 永久卡死且 timeout 管不到它。
        # 输出重定向到临时文件则完全绕开管道继承问题。
        import tempfile
        full_cmd = [cmd[0], *_GIT_SAFE_FLAGS, *cmd[1:]]
        # stdin 接 DEVNULL：Gateway 的 stdin 是 TUI 的 JSON-RPC 管道，git 子进程
        # 继承后可能在上面等待输入导致挂起；GIT_TERMINAL_PROMPT=0 禁止任何交互提示
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as tmp:
            subprocess.run(
                full_cmd,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=tmp,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env=env,
            )
            tmp.seek(0)
            return tmp.read().strip()


def get_git_status_text(repo_path: Optional[str] = None) -> str:
    """便捷函数：获取当前 Git 状态文本。"""
    return GitStatusProvider.get_status(repo_path)


def get_git_verify_context(repo_path: Optional[str] = None, touched_files: Optional[list] = None) -> str:
    """便捷函数：获取 Verify 子 agent 的预填充 Git 上下文（status + 完整 diff）。"""
    return GitStatusProvider.get_verify_context(repo_path, touched_files)
