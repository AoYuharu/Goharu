"""turn 末对账：挂钩层未捕获的文件变更，经 git 基线兜底发现。

候选 = 工作区相对 base_ref 内容有差异的已跟踪文件
     ∪ 本 turn 新建的未跟踪文件（status_end 中 ?? 且不在 status_start）
     ∪ 状态码翻转的路径
     − 挂钩层已捕获 − 排除规则
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

from .gitbase import GitBase

logger = logging.getLogger(__name__)


def make_excluder(patterns: list[str]):
    """gitignore 风格子集（fnmatch）。rel 路径与绝对路径任一命中即排除。"""

    def is_excluded(rel_path: str, abs_path: str) -> bool:
        rel = rel_path.replace("\\", "/")
        ab = abs_path.replace("\\", "/")
        for pat in patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(ab, pat):
                return True
        return False

    return is_excluded


def rel_to_root(path: str, repo_root: Path) -> str:
    """绝对路径 → 相对 repo_root 的正斜杠路径；不在仓库内返回规范化绝对路径。"""
    try:
        return Path(path).resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return str(Path(path)).replace("\\", "/")


def reconcile_candidates(
    hooked_abs: set[str],
    git: GitBase,
    base_ref: str,
    status_start: dict[str, str],
    is_excluded,
) -> list[str]:
    """返回挂钩层之外发生变更的 rel 路径列表（已排序）。"""
    status_end = git.status_porcelain()
    candidates: set[str] = set()

    candidates |= git.diff_names(base_ref)

    for rel, xy in status_end.items():
        if xy == "??" and rel not in status_start:
            candidates.add(rel)  # 本 turn 新建
        elif rel in status_start and status_start[rel] != xy:
            candidates.add(rel)  # 状态翻转（如 modified → staged）

    repo_root = git.repo_root
    out = []
    for rel in candidates:
        abs_path = str((repo_root / rel).resolve())
        if abs_path in hooked_abs:
            continue
        if is_excluded(rel, abs_path):
            continue
        out.append(rel)
    out.sort()
    if out:
        logger.info("checkpoint 对账捕获挂钩外变更: %s", out)
    return out
