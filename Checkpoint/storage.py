"""Checkpoint 持久化：manifest 原子写、内容寻址 blob 库、SEQ 计数器、retention GC。

目录布局：
  <root>/SEQ
  <root>/_blobs/<sha[:2]>/<sha>
  <root>/<seq:04d>_<label>/manifest.json

blob 跨 checkpoint 去重（同一内容只存一份，Edit 链里 new=下个 op 的 old 天然命中）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .models import sha256_of

logger = logging.getLogger(__name__)

_SEQ_FILE = "SEQ"
_BLOBS = "_blobs"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class CheckpointStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / _BLOBS).mkdir(exist_ok=True)

    # ------------------------------------------------------------------ seq
    def next_seq(self) -> int:
        seq_path = self.root / _SEQ_FILE
        try:
            current = int(seq_path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            current = 0
        nxt = current + 1
        _atomic_write(seq_path, str(nxt).encode("ascii"))
        return nxt

    # ------------------------------------------------------------------ blob
    def _blob_path(self, sha: str) -> Path:
        return self.root / _BLOBS / sha[:2] / sha

    def write_blob(self, data: bytes) -> str:
        sha = sha256_of(data)
        path = self._blob_path(sha)
        if not path.exists():
            _atomic_write(path, data)
        return sha

    def read_blob(self, sha: str) -> Optional[bytes]:
        try:
            return self._blob_path(sha).read_bytes()
        except OSError:
            logger.warning("checkpoint blob 缺失: %s", sha)
            return None

    # -------------------------------------------------------------- manifest
    def checkpoint_dir(self, checkpoint_id: str) -> Path:
        return self.root / checkpoint_id

    def save_manifest(self, manifest: dict) -> Path:
        path = self.checkpoint_dir(manifest["checkpoint_id"]) / "manifest.json"
        _atomic_write(path, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
        logger.info("checkpoint 已写入: %s", path)
        return path

    def load_manifest(self, checkpoint_id: str) -> Optional[dict]:
        try:
            return json.loads((self.checkpoint_dir(checkpoint_id) / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("manifest 读取失败 %s: %s", checkpoint_id, e)
            return None

    def list_manifests(self) -> list[dict]:
        """按 seq 升序返回全部 manifest（损坏条目跳过并记日志）。"""
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or d.name == _BLOBS:
                continue
            m = self.load_manifest(d.name)
            if m:
                out.append(m)
        out.sort(key=lambda m: m.get("seq") or 0)
        return out

    # ------------------------------------------------------------- retention
    def apply_retention(
        self,
        max_count: int,
        retention_days: int,
        max_total_mb: int,
        on_drop_seq: Optional[Callable[[int], None]] = None,
    ) -> list[str]:
        """裁剪超限 checkpoint 目录并回收无引用 blob。返回被删的 checkpoint_id。"""
        manifests = self.list_manifests()
        now = time.time()
        total_bytes = sum(
            f.stat().st_size
            for f in self.root.rglob("*")
            if f.is_file() and f.name != _SEQ_FILE
        )
        max_bytes = max_total_mb * 1024 * 1024
        dropped: list[str] = []

        def over_budget() -> bool:
            live = len(manifests) - len(dropped)
            return live > max_count or total_bytes > max_bytes

        for m in manifests:
            if not over_budget():
                break
            ended = m.get("ended_at") or ""
            too_old = False
            if ended:
                try:
                    from datetime import datetime

                    too_old = (now - datetime.fromisoformat(ended).timestamp()) > retention_days * 86400
                except ValueError:
                    pass
            if not over_budget() and not too_old:
                continue
            cid = m["checkpoint_id"]
            size = sum(f.stat().st_size for f in self.checkpoint_dir(cid).rglob("*") if f.is_file())
            import shutil

            shutil.rmtree(self.checkpoint_dir(cid), ignore_errors=True)
            total_bytes -= size
            dropped.append(cid)
            if on_drop_seq and m.get("seq"):
                try:
                    on_drop_seq(int(m["seq"]))
                except Exception as e:
                    logger.warning("drop git ref 失败 seq=%s: %s", m["seq"], e)
            logger.info("checkpoint retention 裁剪: %s", cid)

        if dropped:
            self._gc_blobs()
        return dropped

    def _gc_blobs(self) -> None:
        live: set[str] = set()
        for m in self.list_manifests():
            for f in m.get("files") or []:
                for side in ("old", "new"):
                    ref = f.get(side) or {}
                    if ref.get("sha256"):
                        live.add(ref["sha256"])
        removed = 0
        for sub in (self.root / _BLOBS).iterdir():
            if not sub.is_dir():
                continue
            for blob in sub.iterdir():
                if blob.name not in live:
                    try:
                        blob.unlink()
                        removed += 1
                    except OSError:
                        pass
        if removed:
            logger.info("checkpoint blob GC: 回收 %d 个无引用 blob", removed)
