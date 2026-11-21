"""定时提醒存储 — 到点后把一句话注入下一轮上下文。

设计要点：
- **无后台线程**：到期判定在读取时按当前时间计算。Agent 每轮装配上下文时调用
  `consume_due()`，到点的提醒被取出并投递，未到点的不产生任何输出。
- **一次性 / 周期**：一次性提醒触发后删除；周期提醒把 fire_at 推进到下一个未来
  时间点（停机期间错过多次也只补一条，不刷屏）。
- **原子写**：写临时文件后 os.replace，读改写全程持锁，避免并发损坏。
- **持久化**：runtime_memory/scheduled_reminders.json，重启不丢。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_PATH = "./runtime_memory/scheduled_reminders.json"

_LOCK = threading.RLock()

_MIN_REPEAT_SECONDS = 60


def _now():
    return datetime.now()


def _parse_iso(value):
    """ISO 串 → datetime，失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def parse_when(value, now=None):
    """把时间描述解析成本地 datetime。

    支持：
    - "YYYY-MM-DD HH:MM[:SS]" / "YYYY-MM-DD"
    - "HH:MM[:SS]"（当天已过则顺延到明天）
    - ISO 8601（2026-11-01T09:00:00）
    """
    now = now or _now()
    text = str(value or "").strip()
    if not text:
        raise ValueError("时间不能为空")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt)
        except ValueError:
            continue
        target = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    iso = _parse_iso(text)
    if iso is not None:
        return iso

    raise ValueError(f"无法解析时间：{value}（用 'YYYY-MM-DD HH:MM' 或 'HH:MM'）")


class ReminderStore:
    """定时提醒的持久化存储。实例只持有路径，数据每次从文件读，避免多实例状态漂移。"""

    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH

    # ── 文件 IO ────────────────────────────────────────────────

    @contextmanager
    def _file_lock(self, timeout=2.0, stale=30.0):
        """跨进程互斥（gateway 与 TUI 可能共享同一个 JSON）。

        O_CREAT|O_EXCL 抢锁文件；抢不到就短轮询。锁文件超过 stale 秒视为
        进程崩溃残留，直接清理后重试。拿不到锁时抛 TimeoutError——调用方
        自己决定降级（提醒投递失败好过整轮对话卡死）。
        """
        lock_path = self.path + ".lock"
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        deadline = time.time() + timeout
        fd = None
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock_path) > stale:
                        os.unlink(lock_path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"提醒库被占用：{lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(lock_path)
            except OSError:
                pass

    def _load(self):
        if not os.path.exists(self.path):
            return {"next_id": 1, "items": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("ReminderStore: 读取失败，按空库处理 (%s)", exc)
            return {"next_id": 1, "items": []}
        if not isinstance(data, dict):
            return {"next_id": 1, "items": []}
        data.setdefault("next_id", 1)
        items = data.get("items")
        # 非 dict 条目（手改 JSON 留下的毒丸）直接丢弃，避免整库失效
        data["items"] = [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []
        return data

    def _save(self, data):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".reminders_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── 对外接口 ───────────────────────────────────────────────

    def add(self, text, at=None, in_seconds=None, every_seconds=None, now=None):
        """新增提醒。at / in_seconds / every_seconds 三选一（every_seconds 自带首次触发时间）。"""
        now = now or _now()
        text = (text or "").strip()
        if not text:
            raise ValueError("提醒内容不能为空")

        given = [name for name, val in
                 (("at", at), ("in_seconds", in_seconds), ("every_seconds", every_seconds))
                 if val is not None and val != ""]
        if len(given) > 1:
            raise ValueError(f"at / in_seconds / every_seconds 只能给一个，收到：{given}")

        repeat = None
        if every_seconds is not None:
            repeat = int(every_seconds)
            if repeat < _MIN_REPEAT_SECONDS:
                raise ValueError(f"every_seconds 不得小于 {_MIN_REPEAT_SECONDS} 秒")

        if at:
            fire_at = parse_when(at, now)
        elif in_seconds is not None:
            fire_at = now + timedelta(seconds=int(in_seconds))
        elif repeat is not None:
            fire_at = now + timedelta(seconds=repeat)
        else:
            raise ValueError("必须给出 at / in_seconds / every_seconds 之一")

        with _LOCK, self._file_lock():
            data = self._load()
            item = {
                "id": int(data["next_id"]),
                "text": text,
                "fire_at": fire_at.isoformat(timespec="seconds"),
                "repeat_seconds": repeat,
                "enabled": True,
                "created_at": now.isoformat(timespec="seconds"),
                "fired_count": 0,
            }
            data["next_id"] = item["id"] + 1
            data["items"].append(item)
            self._save(data)
        logger.info("ReminderStore: 新增提醒 #%s fire_at=%s repeat=%s", item["id"], item["fire_at"], repeat)
        return item

    def list_all(self):
        with _LOCK, self._file_lock():
            return [dict(it) for it in self._load()["items"]]

    def cancel(self, reminder_id):
        with _LOCK, self._file_lock():
            data = self._load()
            keep = [it for it in data["items"] if int(it.get("id", -1)) != int(reminder_id)]
            removed = len(data["items"]) - len(keep)
            if removed:
                data["items"] = keep
                self._save(data)
            return removed > 0

    def claim_due(self, now=None, ttl=180):
        """认领到点的提醒：在**一次加锁内**打 in-flight 标记，避免并发重复投递。

        claim 只是「正在投递」的占位，不是消费：
        - 投递成功 → ack_due()（一次性删除 / 周期推进）
        - 投递失败或被打断 → release_due()（清标记，下一轮还能取到）
        - 进程崩溃 → 超过 ttl 秒后标记自动失效，条目回到可认领状态

        为什么不能「读到即删」：per-turn 上下文在工具循环的中间步骤也会被装配，
        那时最后一条消息是 tool_result（content 是块列表），提醒无处可贴；
        而且装配成功 ≠ 请求成功，LLM 重试会重建消息。提前删除 = 静默丢失。
        """
        now = now or _now()
        claimed = []
        with _LOCK, self._file_lock():
            data = self._load()
            changed = False
            for item in data["items"]:
                if not item.get("enabled", True):
                    continue
                fire_at = _parse_iso(item.get("fire_at"))
                if fire_at is None or fire_at > now:
                    continue
                claim = item.get("claim")
                if isinstance(claim, dict):
                    claimed_at = _parse_iso(claim.get("at"))
                    try:
                        claim_ttl = float(claim.get("ttl", ttl))
                    except (TypeError, ValueError):
                        claim_ttl = float(ttl)
                    if claimed_at is not None and (now - claimed_at).total_seconds() < claim_ttl:
                        continue  # 别的调用方正在投递
                item["claim"] = {"at": now.isoformat(timespec="seconds"), "ttl": int(ttl)}
                claimed.append(dict(item))
                changed = True
            if changed:
                self._save(data)
        if claimed:
            logger.info("ReminderStore: 认领 %d 条到点提醒 %s", len(claimed), [c.get("id") for c in claimed])
        return claimed

    def release_due(self, items):
        """投递失败/被打断：撤销 in-flight 标记，让提醒在下一轮重新可见。"""
        ids = {int(it.get("id", -1)) for it in (items or [])}
        if not ids:
            return 0
        released = 0
        with _LOCK, self._file_lock():
            data = self._load()
            changed = False
            for item in data["items"]:
                if int(item.get("id", -1)) in ids and "claim" in item:
                    item.pop("claim", None)
                    changed = True
                    released += 1
            if changed:
                self._save(data)
        if released:
            logger.info("ReminderStore: 释放 %d 条未投递成功的提醒 %s", released, sorted(ids))
        return released

    def ack_due(self, items, now=None):
        """投递成功后确认：一次性删除，周期项推进到下一个未来时间点。返回处理条数。"""
        now = now or _now()
        ids = {int(it.get("id", -1)) for it in (items or [])}
        if not ids:
            return 0
        handled = 0
        with _LOCK, self._file_lock():
            data = self._load()
            keep = []
            changed = False
            for item in data["items"]:
                if int(item.get("id", -1)) not in ids:
                    keep.append(item)
                    continue
                changed = True
                handled += 1
                item.pop("claim", None)
                repeat = item.get("repeat_seconds")
                fire_at = _parse_iso(item.get("fire_at"))
                if repeat and fire_at is not None:
                    nxt = fire_at
                    while nxt <= now:
                        nxt += timedelta(seconds=int(repeat))
                    item["fire_at"] = nxt.isoformat(timespec="seconds")
                    item["fired_count"] = int(item.get("fired_count", 0)) + 1
                    item["last_fired_at"] = now.isoformat(timespec="seconds")
                    keep.append(item)
                # 一次性 / 脏数据：不保留
            if changed:
                data["items"] = keep
                self._save(data)
        if handled:
            logger.info("ReminderStore: 投递 %d 条到点提醒 %s", handled, sorted(ids))
        return handled

    def consume_due(self, now=None):
        """认领并立即确认（claim + ack 的原子组合）。

        仅适用于「拿到就一定会被展示」的场景；per-turn 上下文装配请改用
        claim_due() + ack_due() / release_due()，避免投递失败后丢提醒。
        """
        now = now or _now()
        due = self.claim_due(now)
        if due:
            self.ack_due(due, now)
        return due


_default_store = None


def get_reminder_store():
    """进程级默认实例（路径固定，多实例共享同一文件也无状态漂移风险）。"""
    global _default_store
    if _default_store is None:
        _default_store = ReminderStore()
    return _default_store
