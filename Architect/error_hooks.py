"""Architect 错误 Hook 规则引擎。

Arch 可自行增删/调参自定义规则；内置安全底线规则恒存在且不可禁用/删除。
规则存储 runtime_memory/architect/error_hooks.json，运行态（冷却/尝试次数）
存储 error_hooks_state.json。本模块不 import Gateway/Tools，纯规则判断。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"debug": 0, "info": 1, "warning": 2, "error": 3, "fatal": 4}


def _rule(rid, name, match, *, cooldown=600, max_attempts=2,
          prompt_extra="", repair=True, severity_min="error"):
    return {
        "id": rid, "name": name, "enabled": True, "protected": True,
        "match": {"severity_min": severity_min, **match},
        "action": {"cooldown_seconds": int(cooldown),
                   "max_attempts": int(max_attempts),
                   "repair": bool(repair), "prompt_extra": prompt_extra},
    }


def protected_rules() -> list[dict]:
    """安全底线：Arch 不可关闭（可在其上追加更细的自定义规则）。"""
    return [
        _rule("builtin:repair_failed", "修复失败通知（不再自动重派）",
              {"scope": "architect_repair"},
              cooldown=1800, max_attempts=1, repair=False,
              severity_min="error",
              prompt_extra="这是上一次自动修复失败的通知：不要立即再次修改代码；"
                           "先复核回滚状态，向主人报告根因与建议。"),
        _rule("builtin:fatal_process", "进程级致命错误",
              {"blocking": "fatal"}, cooldown=600, max_attempts=2,
              prompt_extra="这是进程级致命错误（进程可能已经/即将退出）。"
                           "修复必须优先保证下一次启动成功。"),
        _rule("builtin:all_turns", "全部轮次阻塞",
              {"blocking": "all_turns"}, cooldown=600, max_attempts=2,
              prompt_extra="该错误会使每一轮对话都失败；请优先处理根因。"),
        _rule("builtin:runtime_logic", "运行期逻辑异常",
              {"message_regex": r"(?i)(traceback|AttributeError|TypeError|"
                                r"NameError|KeyError|IndexError|ValueError)"},
              cooldown=1800, max_attempts=1, severity_min="error"),
    ]


class ErrorHookEngine:
    def __init__(self, path: str | Path, state_path: str | Path | None = None,
                 clock=time.time):
        self.path = Path(path)
        self.state_path = Path(state_path) if state_path else self.path.with_name(
            "error_hooks_state.json")
        self.clock = clock
        self._lock = threading.RLock()
        self._rules: list[dict] = []
        self._state: dict = {"fingerprints": {}}
        self._load()

    # ---------------------------------------------------------------- 存储
    def _load(self) -> None:
        with self._lock:
            builtins = {r["id"]: r for r in protected_rules()}
            customs: list[dict] = []
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    for r in data.get("rules", []):
                        rid = str(r.get("id") or "")
                        if not rid or rid in builtins:
                            continue
                        r["protected"] = False
                        customs.append(r)
                except Exception:
                    logger.warning("error_hooks 规则文件损坏，使用内置规则", exc_info=True)
            if self.state_path.exists():
                try:
                    self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
                except Exception:
                    self._state = {"fingerprints": {}}
            # 自定义规则优先于安全底线：Arch 可对特定错误选择"只分析不修代码"，
            # 未覆盖时仍由 protected 底线兜底唤醒。
            self._rules = customs + list(builtins.values())

    def _save_rules(self) -> None:
        customs = [r for r in self._rules if not r.get("protected")]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rules": customs}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            logger.exception("保存 error_hooks 规则失败")

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            logger.exception("保存 error_hooks 状态失败")

    # ---------------------------------------------------------------- 规则管理
    def list_rules(self) -> list[dict]:
        with self._lock:
            return [json.loads(json.dumps(r)) for r in self._rules]

    def get_rule(self, rule_id: str) -> Optional[dict]:
        with self._lock:
            for r in self._rules:
                if r.get("id") == rule_id:
                    return json.loads(json.dumps(r))
        return None

    def add_rule(self, rule: dict) -> dict:
        with self._lock:
            r = dict(rule or {})
            rid = str(r.get("id") or f"custom:{uuid.uuid4().hex[:8]}")
            if any(x.get("id") == rid for x in self._rules):
                raise ValueError(f"规则已存在: {rid}")
            r.update({"id": rid, "protected": False, "enabled": bool(r.get("enabled", True))})
            r.setdefault("name", rid)
            r.setdefault("match", {})
            action = dict(r.get("action") or {})
            action.setdefault("cooldown_seconds", 600)
            action.setdefault("max_attempts", 2)
            action.setdefault("repair", True)
            action.setdefault("prompt_extra", "")
            r["action"] = action
            self._rules.append(r)
            self._save_rules()
            return json.loads(json.dumps(r))

    def update_rule(self, rule_id: str, **fields) -> dict:
        with self._lock:
            rule = next((r for r in self._rules if r.get("id") == rule_id), None)
            if rule is None:
                raise ValueError(f"未知规则: {rule_id}")
            if rule.get("protected"):
                raise PermissionError(f"受保护规则不可修改: {rule_id}")
            for k, v in fields.items():
                if k in ("id", "protected"):
                    continue
                if k == "match" and isinstance(v, dict):
                    rule["match"] = {**rule.get("match", {}), **v}
                elif k == "action" and isinstance(v, dict):
                    rule["action"] = {**rule.get("action", {}), **v}
                else:
                    rule[k] = v
            self._save_rules()
            return json.loads(json.dumps(rule))

    def remove_rule(self, rule_id: str) -> bool:
        with self._lock:
            rule = next((r for r in self._rules if r.get("id") == rule_id), None)
            if rule is None:
                return False
            if rule.get("protected"):
                raise PermissionError(f"受保护规则不可删除: {rule_id}")
            self._rules.remove(rule)
            self._save_rules()
            return True

    # ---------------------------------------------------------------- 匹配
    @staticmethod
    def _match(rule: dict, record: dict) -> bool:
        m = rule.get("match") or {}
        for key in ("scope", "blocking", "cause"):
            expected = m.get(key)
            if expected is None:
                continue
            values = expected if isinstance(expected, list) else [expected]
            if record.get(key) not in values:
                return False
        min_sev = m.get("severity_min")
        if min_sev and _SEVERITY_RANK.get(str(record.get("severity")), 0) < \
                _SEVERITY_RANK.get(str(min_sev), 3):
            return False
        for key in ("source_regex", "message_regex"):
            pattern = m.get(key)
            if not pattern:
                continue
            target = record.get("source" if key == "source_regex" else "message", "")
            try:
                if not re.search(str(pattern), str(target or "")):
                    return False
            except re.error:
                return False
        return True

    def evaluate(self, record: dict) -> Optional[dict]:
        """返回首个可唤醒规则；不修改状态（由 mark_wake 记账）。"""
        fp = str(record.get("fingerprint") or "")
        now = self.clock()
        with self._lock:
            state = self._state.setdefault("fingerprints", {}).setdefault(fp, {})
            for rule in self._rules:
                if not rule.get("enabled", True):
                    continue
                if not self._match(rule, record):
                    continue
                action = rule.get("action") or {}
                last = float(state.get("last_wake", 0) or 0)
                cooldown = float(action.get("cooldown_seconds", 600) or 0)
                if cooldown > 0 and now - last < cooldown:
                    continue
                attempts = int(state.get("attempts", 0) or 0)
                if attempts >= int(action.get("max_attempts", 2) or 2):
                    continue
                return {"rule": json.loads(json.dumps(rule)), "fingerprint": fp,
                        "attempts": attempts}
        return None

    def mark_wake(self, fingerprint: str, rule_id: str) -> None:
        with self._lock:
            fp = str(fingerprint or "")
            state = self._state.setdefault("fingerprints", {}).setdefault(fp, {})
            state["last_wake"] = self.clock()
            state["attempts"] = int(state.get("attempts", 0) or 0) + 1
            state["rule_id"] = rule_id
            self._prune_state()
            self._save_state()

    def _prune_state(self) -> None:
        cutoff = self.clock() - 7 * 86400
        fps = self._state.get("fingerprints", {})
        for fp in [k for k, v in fps.items()
                   if float((v or {}).get("last_wake", 0) or 0) < cutoff]:
            fps.pop(fp, None)

    def test(self, record: dict) -> dict:
        """只测试匹配，不检查/不消耗冷却与次数（ErrorHook 工具 test 动作）。"""
        with self._lock:
            for rule in self._rules:
                if rule.get("enabled", True) and self._match(rule, record):
                    return {"matched": True,
                            "rule": json.loads(json.dumps(rule))}
        return {"matched": False}


def fingerprint_of(message: str, scope: str, cause: str, source: str = "") -> str:
    raw = f"{scope}|{cause}|{source}|{message}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]
