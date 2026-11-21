"""Upgrader —— 版本升级的 staging 拉取、diff 摘要与远端轮询唤醒源。

用 `git worktree add` 把远端最新代码检出到独立 staging 目录：**不动生产
工作区、不动当前分支**。审计 FAIL 时 `cleanup()` 移除 staging，生产零影响。
git 操作可注入（run_git），契约测试用假实现驱动。

GitUpdateSource：拉式唤醒源（WakeSource 协议首个实现）——定期 fetch 远端
并比对本地 HEAD，发现新提交时产出 code_update WakeRequest。上下文保持
精简（每提交一行：时间 sha 主题），不占 token。
"""

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class Upgrader:
    def __init__(self, repo_dir: str = ".",
                 staging_dir: str = "runtime_memory/upgrade_staging",
                 remote: str = "origin", branch: str = "master",
                 run_git=None):
        self.repo_dir = str(repo_dir)
        self.staging_dir = str(staging_dir)
        self.remote = str(remote)
        self.branch = str(branch)
        self._run_git = run_git or self._default_run_git

    # ------------------------------------------------------------------

    def fetch_staging(self) -> dict:
        """fetch 远端并检出到 staging worktree。幂等：已存在先移除重建。"""
        r = self._git("fetch", self.remote, self.branch)
        if not r["ok"]:
            return {"ok": False, "error": f"git fetch: {r['output']}"}

        staging = Path(self.staging_dir)
        if staging.exists():
            self.cleanup()
        r = self._git("worktree", "add", "--detach", self.staging_dir,
                      f"{self.remote}/{self.branch}")
        if not r["ok"]:
            return {"ok": False, "error": f"git worktree add: {r['output']}"}
        logger.info("upgrade staging ready: %s (%s/%s)",
                    self.staging_dir, self.remote, self.branch)
        return {"ok": True, "staging_dir": self.staging_dir}

    def diff_summary(self, max_chars: int = 20000) -> str:
        """生产 HEAD 与 staging（远端分支）的差异：stat + 完整 diff（截断）。"""
        ref = f"{self.remote}/{self.branch}"
        stat = self._git("diff", "--stat", "HEAD", ref)
        full = self._git("diff", "HEAD", ref)
        text = ""
        if stat["ok"]:
            text += f"== diff --stat ==\n{stat['output']}\n\n"
        if full["ok"]:
            text += full["output"][:max_chars]
        return text

    def cleanup(self) -> dict:
        """移除 staging worktree（审计结束/失败后调用）。"""
        return self._git("worktree", "remove", "--force", self.staging_dir)

    # ------------------------------------------------------------------

    def _git(self, *args) -> dict:
        return self._run_git(self.repo_dir, *args)

    @staticmethod
    def _default_run_git(repo_dir: str, *args) -> dict:
        try:
            proc = subprocess.run(
                ["git", "-C", repo_dir, *args],
                capture_output=True, text=True, timeout=120,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return {"ok": proc.returncode == 0, "output": out.strip()}
        except Exception as exc:
            return {"ok": False, "output": str(exc)}


# ----------------------------------------------------------------------
# 拉式唤醒源：定期轮询远端分支新提交
# ----------------------------------------------------------------------

class GitUpdateSource:
    """定期检查远端分支有无新提交，有则产出 code_update WakeRequest。

    去重语义：受理制——poll 发现新提交即报，但不自标已报；hub 真正启动
    更新流程后回掉 `note_accepted(sha)`，该 sha 才停止重报（用户未确认/
    门控推迟时，源会在下个周期再次提醒）。自身带 check_interval 节流，
    调度器高频 tick 也不会打爆 git fetch。
    """

    name = "git_update"
    _MAX_COMMITS = 10          # 上下文精简：最多列 10 个新提交

    def __init__(self, repo_dir: str = ".", remote: str = "origin",
                 branch: str = "master", check_interval_seconds: int = 1800,
                 run_git=None):
        self.repo_dir = str(repo_dir)
        self.remote = str(remote)
        self.branch = str(branch)
        self.check_interval = int(
            check_interval_seconds if check_interval_seconds is not None else 1800)
        self._run_git = run_git or Upgrader._default_run_git
        self._last_check = 0.0
        self._accepted_sha = None

    def note_accepted(self, sha: str):
        """hub 受理更新后回掉：该 sha 停止重报。"""
        self._accepted_sha = str(sha)
        logger.info("git_update: sha %s 已受理，停止重报", sha[:8])

    def poll(self):
        """WakeSource 协议：到新检出点且有新提交时返回 WakeRequest。"""
        from Architect.wake import KIND_AUDIT, REASON_CODE_UPDATE, WakeRequest

        now = time.time()
        if now - self._last_check < self.check_interval:
            return None
        self._last_check = now

        ref = f"{self.remote}/{self.branch}"
        r = self._run_git(self.repo_dir, "fetch", self.remote, self.branch)
        if not r["ok"]:
            logger.warning("git_update fetch failed: %s", r["output"][:200])
            return None

        local = self._run_git(self.repo_dir, "rev-parse", "HEAD")
        remote = self._run_git(self.repo_dir, "rev-parse", ref)
        if not (local["ok"] and remote["ok"]):
            return None
        local_sha, remote_sha = local["output"].strip(), remote["output"].strip()
        if local_sha == remote_sha:
            self._accepted_sha = remote_sha      # 已同步，无需再报
            return None
        if remote_sha == self._accepted_sha:
            return None

        # 精简提交清单：每行 "MM-DD HH:MM sha 主题"，最多 _MAX_COMMITS 条
        log = self._run_git(
            self.repo_dir, "log", "--no-merges",
            "--format=%h %ad %s", "--date=format:%m-%d %H:%M",
            f"-{self._MAX_COMMITS}", f"HEAD..{ref}")
        commits = log["output"] if log["ok"] else "(log 获取失败)"
        behind = self._run_git(self.repo_dir, "rev-list", "--count",
                               f"HEAD..{ref}")
        count = behind["output"].strip() if behind["ok"] else "?"

        logger.info("git_update: %s 有新提交（落后 %s 个）", ref, count)
        return WakeRequest(
            reason=REASON_CODE_UPDATE, kind=KIND_AUDIT, priority=1,
            context={
                "ref": ref,
                "local_head": local_sha[:8],
                "remote_head": remote_sha[:8],
                "behind": count,
                "new_commits": commits,
            },
        )
