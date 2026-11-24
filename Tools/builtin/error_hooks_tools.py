"""ErrorHook —— Architect 自管理"哪些错误唤醒我"的规则工具。

layering 说明：Architect 是产品层模块，此处仅函数内惰性 import + 优雅降级
（与 architect_tools.py 的 ConsultArchitect 同模式）。内置安全底线规则不可
禁用/删除，由 Architect.error_hooks 强制保护。
"""

import json
import logging

from Tools.registry import registry

logger = logging.getLogger(__name__)


def _engine():
    try:
        from Architect.hub import get_default_hub

        hub = get_default_hub()
        engine = getattr(hub, "error_engine", None)
        if engine is None:
            raise RuntimeError("ErrorHookEngine 未初始化")
        return engine
    except Exception as e:
        raise RuntimeError(f"Architect 错误规则不可用: {e}") from e


def _build_rule(kwargs: dict) -> dict:
    match = {}
    for key in ("scope", "blocking", "cause", "source_regex",
                "message_regex", "severity_min"):
        val = kwargs.get(key)
        if val not in (None, ""):
            match[key] = val
    action = {}
    for key in ("cooldown_seconds", "max_attempts", "repair", "prompt_extra"):
        val = kwargs.get(key)
        if val not in (None, ""):
            action[key] = val
    rule = {
        "id": kwargs.get("rule_id") or "",
        "name": kwargs.get("name") or kwargs.get("rule_id") or "custom rule",
        "enabled": bool(kwargs.get("enabled", True)),
        "match": match,
        "action": action,
    }
    return rule


def ErrorHook(action: str, rule_id: str = "", name: str = "",
              scope: str = "", blocking: str = "", cause: str = "",
              source_regex: str = "", message_regex: str = "",
              severity_min: str = "", cooldown_seconds: int = 0,
              max_attempts: int = 0, repair: bool | None = None,
              prompt_extra: str = "", enabled: bool | None = None,
              sample_message: str = "") -> str:
    """管理错误唤醒规则：list / add / update / remove / test。"""
    try:
        engine = _engine()
        if action == "list":
            return json.dumps({"status": "ok", "rules": engine.list_rules()},
                              ensure_ascii=False, default=str)
        if action == "add":
            rule = _build_rule({
                "rule_id": rule_id, "name": name, "scope": scope,
                "blocking": blocking, "cause": cause,
                "source_regex": source_regex, "message_regex": message_regex,
                "severity_min": severity_min,
                "cooldown_seconds": cooldown_seconds or None,
                "max_attempts": max_attempts or None,
                "repair": repair, "prompt_extra": prompt_extra,
                "enabled": True if enabled is None else enabled,
            })
            if not rule["match"]:
                return json.dumps({"status": "error",
                                   "error": "至少提供一条 match 条件（scope/blocking/cause/regex）"},
                                  ensure_ascii=False)
            saved = engine.add_rule(rule)
            logger.info("ErrorHook: 新增规则 %s", saved.get("id"))
            return json.dumps({"status": "ok", "rule": saved},
                              ensure_ascii=False, default=str)
        if action == "update":
            fields = {}
            if name:
                fields["name"] = name
            if enabled is not None:
                fields["enabled"] = enabled
            match = _build_rule({
                "scope": scope, "blocking": blocking, "cause": cause,
                "source_regex": source_regex, "message_regex": message_regex,
                "severity_min": severity_min,
            })["match"]
            if match:
                fields["match"] = match
            act = {k: v for k, v in {
                "cooldown_seconds": cooldown_seconds or None,
                "max_attempts": max_attempts or None,
                "repair": repair, "prompt_extra": prompt_extra or None,
            }.items() if v is not None}
            if act:
                fields["action"] = act
            saved = engine.update_rule(rule_id, **fields)
            return json.dumps({"status": "ok", "rule": saved},
                              ensure_ascii=False, default=str)
        if action == "remove":
            removed = engine.remove_rule(rule_id)
            return json.dumps({"status": "ok" if removed else "error",
                               "removed": removed}, ensure_ascii=False)
        if action == "test":
            record = {
                "fingerprint": "test", "scope": scope or "test",
                "blocking": blocking or "request", "cause": cause or "runtime",
                "severity": severity_min or "error",
                "source": source_regex or "", "message": sample_message or "",
            }
            return json.dumps({"status": "ok", "result": engine.test(record)},
                              ensure_ascii=False, default=str)
        return json.dumps({"status": "error",
                           "error": f"未知 action: {action}（list/add/update/remove/test）"},
                          ensure_ascii=False)
    except PermissionError as e:
        return json.dumps({"status": "error", "error": str(e),
                           "note": "内置安全底线规则不可修改/禁用/删除"},
                          ensure_ascii=False)
    except Exception as e:
        logger.exception("ErrorHook %s 失败", action)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


registry.register(
    name="ErrorHook",
    description=(
        "Manage which errors wake the Architect. Rules are persisted in "
        "runtime_memory/architect/error_hooks.json. Matches on scope / blocking "
        "(fatal|all_turns|channel|feature|request) / cause (config|runtime|external) / "
        "source regex / message regex / severity_min. Built-in safety-floor rules "
        "(fatal_process, all_turns, runtime_logic, repair_failed) are protected and "
        "cannot be disabled or removed; add narrower custom rules on top. "
        "action='repair' controls whether a matched error may trigger direct "
        "production-code repair (default true; guarded by snapshot + self-check + "
        "rollback). Use list before add/update to see existing rules."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["list", "add", "update", "remove", "test"]},
            "rule_id": {"type": "string", "description": "update/remove 的规则 id"},
            "name": {"type": "string"},
            "scope": {"type": "string"},
            "blocking": {"type": "string",
                         "enum": ["fatal", "all_turns", "channel", "feature", "request"]},
            "cause": {"type": "string", "enum": ["config", "runtime", "external"]},
            "source_regex": {"type": "string"},
            "message_regex": {"type": "string"},
            "severity_min": {"type": "string",
                             "enum": ["info", "warning", "error", "fatal"]},
            "cooldown_seconds": {"type": "integer"},
            "max_attempts": {"type": "integer"},
            "repair": {"type": "boolean",
                       "description": "命中后是否允许直接改生产代码（默认 true）"},
            "prompt_extra": {"type": "string",
                             "description": "命中后附带给 Architect 的额外要求"},
            "enabled": {"type": "boolean"},
            "sample_message": {"type": "string", "description": "test 动作的样本文本"},
        },
        "required": ["action"],
    },
    handler=ErrorHook,
    group="agent",
)
