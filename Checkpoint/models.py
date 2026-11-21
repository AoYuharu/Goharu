"""Checkpoint 数据模型。

FileOp 描述一次文件变更（含改前/改后 blob 引用），MemoryOp 描述一次记忆变更。
两者按发生顺序追加进 manifest，回退时按路径/原子坍缩后逆序应用。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

OP_CREATE = "create"
OP_MODIFY = "modify"
OP_DELETE = "delete"

MEM_INSERT = "insert"
MEM_UPDATE = "update"
MEM_DEACTIVATE = "deactivate"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BlobRef:
    """指向 blob 库的引用。present=False 表示该侧文件不存在（create 的 old / delete 的 new）。"""

    present: bool
    sha256: Optional[str] = None
    size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BlobRef":
        if not d:
            return cls(present=False)
        return cls(present=bool(d.get("present")), sha256=d.get("sha256"), size=int(d.get("size") or 0))


@dataclass
class FileOp:
    path: str            # 规范化绝对路径
    rel_path: str        # 相对 repo_root 的正斜杠路径（不在仓库内时为绝对路径）
    op: str              # create | modify | delete
    source: str          # hook:Write | hook:Edit | hook:pdf | hook:music | hook:scene | hook:persona | git
    old: BlobRef = field(default_factory=lambda: BlobRef(present=False))
    new: BlobRef = field(default_factory=lambda: BlobRef(present=False))
    oversized: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "rel_path": self.rel_path,
            "op": self.op,
            "source": self.source,
            "old": self.old.to_dict(),
            "new": self.new.to_dict(),
            "oversized": self.oversized,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FileOp":
        return cls(
            path=d["path"],
            rel_path=d.get("rel_path") or d["path"],
            op=d["op"],
            source=d.get("source", "unknown"),
            old=BlobRef.from_dict(d.get("old")),
            new=BlobRef.from_dict(d.get("new")),
            oversized=bool(d.get("oversized")),
        )


@dataclass
class MemoryOp:
    layer: str           # 当前仅 "l1"
    op: str              # insert | update | deactivate
    atom_id: int
    old_row: Optional[dict] = None
    new_row: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "op": self.op,
            "atom_id": self.atom_id,
            "old_row": self.old_row,
            "new_row": self.new_row,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryOp":
        return cls(
            layer=d.get("layer", "l1"),
            op=d["op"],
            atom_id=int(d["atom_id"]),
            old_row=d.get("old_row"),
            new_row=d.get("new_row"),
        )


def collapse_file_ops(ops: list[FileOp]) -> list[FileOp]:
    """把一个 checkpoint 内的文件 op 流按路径坍缩：每路径保留首条 old 与末条 new/op。

    回退语义：若首条为 create → 撤销即删除；否则恢复首条的 old。
    返回顺序按路径首次出现排序（调用方负责整体逆序）。
    """
    first: dict[str, FileOp] = {}
    last: dict[str, FileOp] = {}
    order: list[str] = []
    for op in ops:
        if op.path not in first:
            first[op.path] = op
            order.append(op.path)
        last[op.path] = op
    collapsed: list[FileOp] = []
    for path in order:
        f, l = first[path], last[path]
        collapsed.append(
            FileOp(
                path=path,
                rel_path=f.rel_path,
                op=l.op if f.op != OP_CREATE else OP_CREATE,
                source=f.source,
                old=f.old,
                new=l.new,
                oversized=f.oversized or l.oversized,
            )
        )
    return collapsed


def manifest_summary(m: dict) -> dict[str, Any]:
    """从 manifest 提取状态栏/列表用摘要。"""
    files = m.get("files") or []
    mem = (m.get("memory") or {}).get("l1") or []
    return {
        "seq": m.get("seq"),
        "checkpoint_id": m.get("checkpoint_id"),
        "kind": m.get("kind"),
        "outcome": m.get("outcome"),
        "ended_at": m.get("ended_at"),
        "files": len(files),
        "l1_ops": len(mem),
        "git_caught": (m.get("reconcile") or {}).get("git_caught", 0),
        "degraded": bool((m.get("git") or {}).get("degraded")),
        "warnings": m.get("warnings") or [],
    }
