"""心跳写入 —— 每个 session 的看门狗线程每跳写一次，进程外哨兵读它判活。

文件内容：{session_id: {ts, step, armed_since}}。ts 持续刷新 = 进程活着且
线程能跑（事件循环假死时看门狗线程仍在跳，所以哨兵判据是"armed_since
过久"而非单纯 ts 停滞——ts 停滞意味着更严重的整个进程卡死）。
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def heartbeat_path(base_dir: str = "./runtime_memory") -> Path:
    return Path(base_dir) / "heartbeat.json"


def beat(session_id: str, step=None, armed_since=None,
         base_dir: str = "./runtime_memory"):
    """写一次心跳。永不允许抛异常（看门狗线程调用）。"""
    try:
        path = heartbeat_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[session_id] = {
                "ts": time.time(),
                "step": step,
                "armed_since": armed_since,
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
    except Exception as e:
        logger.debug("heartbeat beat failed: %s", e)
