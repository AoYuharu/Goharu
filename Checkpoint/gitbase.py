"""git 基线层：turn 开始冻结工作区基线 ref，turn 结束对账变更。

不 import Tools（双向无依赖）——_run_git 复制自 Tools/git_status_provider.py
的 `_run` 模式：tempfile 替代 PIPE（Windows 孙进程继承管道会挂死）、
stdin=DEVNULL（Gateway 的 stdin 是 JSON-RPC 管道）、GIT_TERMINAL_PROMPT=0。
所有方法失败时返回 None/空集合，绝不抛异常穿透——调用方据此降级。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GIT_SAFE_FLAGS = ["-c", "core.fscache=false", "-c", "core.fsmonitor=false"]
_REFS_PREFIX = "refs/goharu/cp"


def _run_git_text(args: list[str], cwd: Path, timeout: int = 10) -> Optional[str]:
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as tmp:
            subprocess.run(
                ["git", *_GIT_SAFE_FLAGS, *args],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=tmp,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=env,
            )
            tmp.seek(0)
            return tmp.read().strip()
    except Exception as e:
        logger.warning("gitbase: %s 失败: %s", args[:2], e)
        return None


def _run_git_bytes(args: list[str], cwd: Path, timeout: int = 10) -> Optional[bytes]:
    """二进制出版本（git show 恢复文件用）——文本模式会损坏非 UTF-8 内容。
    非零退出码（如文件在 ref 中不存在）返回 None。"""
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        with tempfile.TemporaryFile(mode="w+b") as tmp:
            proc = subprocess.run(
                ["git", *_GIT_SAFE_FLAGS, *args],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=tmp,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=env,
            )
            if proc.returncode != 0:
                return None
            tmp.seek(0)
            return tmp.read()
    except Exception as e:
        logger.warning("gitbase(bytes): %s 失败: %s", args[:2], e)
        return None


def parse_porcelain_z(text: Optional[str]) -> dict[str, str]:
    """解析 `git status --porcelain -z` 输出为 {path: xy}。

    rename 条目在 -z 下为 `R  <to>\\0<from>\\0`（顺序反转），取 to 作为路径。
    """
    result: dict[str, str] = {}
    if not text:
        return result
    # TemporaryFile 文本模式把 \0 保留；strip() 可能去掉尾部空 token
    raw = text
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        xy = entry[:2]
        path = entry[3:]
        if not path:
            continue
        if "R" in xy or "C" in xy:
            i += 1  # 跳过 from 路径
        result[path.replace("\\", "/")] = xy
    return result


class GitBase:
    """一个仓库的 git 基线操作。probe 失败（非 git 仓库/无 git/超时）后全部降级。"""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def probe(self) -> bool:
        out = _run_git_text(["rev-parse", "--is-inside-work-tree"], self.repo_root)
        ok = bool(out and out.strip() == "true")
        logger.info("gitbase probe: %s -> %s", self.repo_root, ok)
        return ok

    def create_base_ref(self) -> Optional[str]:
        """冻结当前工作区（含未提交改动）为 commit object，不动工作区/index。

        干净树时 `git stash create` 输出为空——回退到 HEAD。
        """
        ref = _run_git_text(["stash", "create"], self.repo_root)
        if not ref:
            ref = _run_git_text(["rev-parse", "HEAD"], self.repo_root)
        return ref or None

    def protect_ref(self, seq: int, ref: str) -> bool:
        """把基线 ref 挂到引用上，防 dangling 被 git gc 回收。"""
        out = _run_git_text(["update-ref", f"{_REFS_PREFIX}/{seq}", ref], self.repo_root)
        return out is not None

    def drop_ref(self, seq: int) -> None:
        _run_git_text(["update-ref", "-d", f"{_REFS_PREFIX}/{seq}"], self.repo_root)

    def status_porcelain(self) -> dict[str, str]:
        # --untracked-files=all：normal 模式会把全新目录折叠成 "dir/" 单条，
        # 导致排除规则（如 *.pyc）对目录内文件失效
        out = _run_git_text(["status", "--porcelain", "-z", "--untracked-files=all"], self.repo_root)
        return parse_porcelain_z(out)

    def diff_names(self, base_ref: str) -> set[str]:
        """工作区相对 base_ref 有内容差异的已跟踪文件（rel 路径集合）。"""
        out = _run_git_text(["diff", "--name-only", "-z", base_ref, "--"], self.repo_root)
        if not out:
            return set()
        return {t.replace("\\", "/") for t in out.split("\0") if t}

    def show_file(self, ref: str, rel_path: str) -> Optional[bytes]:
        """读取 ref 中某文件的内容；不存在或失败返回 None。"""
        return _run_git_bytes(["show", f"{ref}:{rel_path}"], self.repo_root)
