"""统一错误记录与审计（Core 层，仅 stdlib）。

职责边界：只负责"生成错误记录、持久化、更新、广播给注入的 listener"。
不认识 Gateway / AgentNet / Architect / Tools——唤醒、转发、UI 都是宿主注入。

错误记录字段（审计 schema）：
    id / fingerprint / ts / scope / severity / blocking / cause / source /
    session / message / traceback / retryable / status / wake_status /
    attempts / updated_ts / extra

blocking（后果）：fatal | all_turns | channel | feature | request
cause（根因）：config | runtime | external

存储：JSONL 追加（runtime_memory/errors/errors.jsonl，env GOHARU_ERRORS_PATH 可覆盖）。
每次 update 追加完整最新记录，读取时按 id 归并；同进程写锁 + O_APPEND 单次 write。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

BLOCKING_LEVELS = ("fatal", "all_turns", "channel", "feature", "request")
CAUSES = ("config", "runtime", "external")

_lock = threading.RLock()
_listeners: list[Callable[[dict], None]] = []


def errors_path() -> Path:
    override = os.environ.get("GOHARU_ERRORS_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "runtime_memory" / "errors" / "errors.jsonl"


def _norm_message(message: str) -> str:
    """fingerprint 归一化：首行、小写，折叠数字/hex/uuid，避免同类错误因 ID 变化重复唤醒。"""
    first = str(message or "").strip().splitlines()[0] if str(message or "").strip() else ""
    first = first.lower()
    first = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", first)
    first = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", first)
    first = re.sub(r"\d+", "<n>", first)
    return first[:300]


def make_fingerprint(message: str, *, scope: str, cause: str, source: str = "") -> str:
    raw = f"{scope}|{cause}|{source}|{_norm_message(message)}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def _new_id() -> str:
    return f"err_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _append(record: dict) -> None:
    path = errors_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with _lock:
            fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except Exception as exc:  # 审计失败绝不能拖垮主流程
        try:
            print(f"[error_report] 写错误审计失败: {exc}", file=sys.stderr)
        except Exception:
            pass


def _notify(record: dict, event: str) -> None:
    with _lock:
        listeners = list(_listeners)
    payload = dict(record)
    payload["_event"] = event   # created|updated（不落盘）
    for fn in listeners:
        try:
            fn(payload)
        except Exception as exc:
            try:
                print(f"[error_report] listener 失败: {exc}", file=sys.stderr)
            except Exception:
                pass


def register_error_listener(fn: Callable[[dict], None]) -> Callable[[], None]:
    """注册错误监听器（唤醒 Arch / 转发 HaruNet / TUI 广播），返回注销函数。"""
    with _lock:
        _listeners.append(fn)

    def _unregister() -> None:
        with _lock:
            try:
                _listeners.remove(fn)
            except ValueError:
                pass

    return _unregister


def report_error(
    message: str,
    *,
    scope: str = "runtime",
    severity: str = "error",
    blocking: str = "feature",
    cause: str = "runtime",
    source: str = "",
    session: str = "",
    traceback: str = "",
    retryable: bool = False,
    fingerprint: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """生成、持久化并广播一条错误记录；永不向调用方抛异常。"""
    blocking = blocking if blocking in BLOCKING_LEVELS else "feature"
    cause = cause if cause in CAUSES else "runtime"
    ts = _now_iso()
    record = {
        "id": _new_id(),
        "fingerprint": fingerprint or make_fingerprint(
            message, scope=scope, cause=cause, source=source),
        "ts": ts,
        "scope": str(scope)[:120],
        "severity": str(severity or "error")[:20],
        "blocking": blocking,
        "cause": cause,
        "source": str(source)[:160],
        "session": str(session)[:80],
        "message": str(message or "")[:4000],
        "traceback": str(traceback or "")[:20000],
        "retryable": bool(retryable),
        "status": "new",
        "wake_status": "none",
        "attempts": 0,
        "updated_ts": ts,
        "extra": dict(extra or {}),
    }
    _append(record)
    _notify(record, "created")
    return record


def report_fatal_early(message: str, **kwargs) -> dict:
    """logging / config 尚未就绪时也能写 fatal 记录（只依赖文件系统）。

    同一 fingerprint 的未解决致命错误在短窗口内合并为一条（attempts 累加），
    防止 systemd 崩溃循环每几秒刷一条新记录。
    """
    kwargs.setdefault("severity", "fatal")
    kwargs.setdefault("blocking", "fatal")
    scope = str(kwargs.get("scope") or "runtime")
    cause = str(kwargs.get("cause") or "runtime")
    source = str(kwargs.get("source") or "")
    fp = kwargs.get("fingerprint") or make_fingerprint(
        message, scope=scope, cause=cause, source=source)
    window = float(os.environ.get("GOHARU_FATAL_DEDUP_SECONDS", "120") or 120)
    now = time.time()
    latest = None
    for rec in _read_latest().values():
        if rec.get("fingerprint") != fp or rec.get("blocking") != "fatal":
            continue
        if rec.get("status") in ("resolved", "resolved_after_restart"):
            continue
        try:
            ts = time.mktime(time.strptime(str(rec.get("updated_ts") or rec.get("ts")),
                                           "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            ts = 0
        if now - ts <= window:
            latest = rec
            break
    if latest is not None:
        return update_error(
            latest["id"], message=str(message)[:4000],
            traceback=str(kwargs.get("traceback") or latest.get("traceback") or "")[:20000],
            attempts=int(latest.get("attempts") or 0) + 1,
        ) or latest
    return report_error(message, fingerprint=fp, **kwargs)


def _read_latest() -> dict[str, dict]:
    path = errors_path()
    latest: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                rid = str(obj.get("id") or "")
                if rid:
                    latest[rid] = obj
    except OSError:
        pass
    return latest


def load_error(error_id: str) -> Optional[dict]:
    return _read_latest().get(str(error_id or ""))


def iter_errors(limit: Optional[int] = None) -> list[dict]:
    records = sorted(_read_latest().values(),
                     key=lambda r: str(r.get("updated_ts") or r.get("ts") or ""))
    if limit is not None and limit > 0:
        records = records[-int(limit):]
    return records


def update_error(error_id: str, **fields) -> Optional[dict]:
    """更新记录状态（wake_status/status/attempts 等），追加最新快照并通知 listener。"""
    with _lock:
        record = _read_latest().get(str(error_id or ""))
        if record is None:
            return None
        record.update(fields)
        record["updated_ts"] = _now_iso()
        _append(record)
    _notify(dict(record), "updated")
    return record


def pending_fatals(max_attempts: int = 3) -> list[dict]:
    """下次启动时可补唤醒的致命记录（未解决且尝试次数未耗尽）。"""
    out = []
    for rec in iter_errors():
        if rec.get("blocking") != "fatal":
            continue
        if rec.get("status") in ("resolved", "resolved_after_restart"):
            continue
        if rec.get("wake_status") in ("repairing", "repair_done"):
            continue
        try:
            attempts = int(rec.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts < int(max_attempts):
            out.append(rec)
    return out
