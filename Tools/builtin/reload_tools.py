"""ReloadTools 工具：热重载内置工具模块，agent 修改工具源码后无需重启即可生效。

用法：模型调用 ReloadTools（可选传 modules 列表），按顺序对每个模块——
已加载的 importlib.reload，未加载的（新加进配置的）importlib.import_module。
模块级 registry.register(...) 重跑即完成重注册（register 同名覆盖）。

已知边界（重载前请知悉）：
- reload 抛异常（如语法错误）时模块对象可能处于半更新状态，但 registry 里
  的旧 handler 引用仍可用——旧定义继续生效，工具不会消失。
- 其他模块若以 `from X import Y` 形式引用了被重载模块的内部符号，旧引用仍
  指向旧对象，不会自动跟随新实现。
- file_tools 重载后 Read-before-Edit 权限状态重置（模块级 _file_state 重建）。
- 宿主注入的引用（memory_tools 的 MemoryManager、task_tools 的变更监听器）
  会跨 reload 自动保存并恢复（_INJECTABLES 表）。

线程安全：模块级锁包住整个重载过程，避免与并发的工具分发互相踩踏。
"""

import importlib
import importlib.util
import json
import logging
import sys
import threading

from Tools.loader import get_builtin_module_names
from Tools.registry import registry

logger = logging.getLogger(__name__)

_reload_lock = threading.Lock()

# 接受宿主注入引用的模块：重载会重跑模块级代码把注入值重置为 None，
# 必须跨 reload 保存/恢复（2026-10-14 事故：ReloadTools 后 memory_search
# 全部报 "no MemoryManager injected"——_memory_manager 被重置）。
# 结构：模块名 → (存放注入值的模块属性, 恢复用 setter)
_INJECTABLES = {
    "Tools.builtin.memory_tools": ("_memory_manager", "set_memory_manager"),
    "Tools.builtin.task_tools": ("_change_listener", "set_change_listener"),
    # 2026-11-22 事故：ReloadTools 重跑模块级代码把 _handle 置 None，
    # harunet_* 全部动作报"未接线"
    "Tools.builtin.harunet_tools": ("_handle", "set_harunet_handle"),
    "Tools.builtin.floor_tools": ("_handle", "set_floor_handle"),
}


def _snapshot_injections(module_name, module_obj):
    attr, _ = _INJECTABLES.get(module_name, (None, None))
    if attr is None:
        return None
    return getattr(module_obj, attr, None)


def _restore_injection(module_name, injected_value):
    if injected_value is None:
        return
    _, setter_name = _INJECTABLES[module_name]
    setter = getattr(sys.modules.get(module_name), setter_name, None)
    if callable(setter):
        try:
            setter(injected_value)
            logger.info("ReloadTools: 恢复 %s 的注入引用", module_name)
        except Exception as exc:
            logger.warning("ReloadTools: 恢复 %s 注入失败: %s", module_name, exc)


def reload_tools(modules: list | None = None) -> str:
    """热重载内置工具模块；缺省时读取配置 tools.builtin_modules 全量重载。"""
    if modules is None:
        modules = get_builtin_module_names()
    elif isinstance(modules, str):
        modules = [modules]
    modules = [str(m).strip() for m in modules if str(m).strip()]

    reloaded = []
    failed = []
    with _reload_lock:
        for module_name in modules:
            try:
                existing = sys.modules.get(module_name)
                if existing is not None:
                    injected = _snapshot_injections(module_name, existing)
                    importlib.reload(existing)
                    _restore_injection(module_name, injected)
                    logger.info("ReloadTools 重载模块成功: %s", module_name)
                else:
                    if importlib.util.find_spec(module_name) is None:
                        raise ModuleNotFoundError(f"No module named '{module_name}'")
                    importlib.import_module(module_name)
                    logger.info("ReloadTools 加载新模块成功: %s", module_name)
                reloaded.append(module_name)
            except Exception as exc:
                logger.warning(
                    "ReloadTools 重载模块失败: %s (%s: %s)",
                    module_name, type(exc).__name__, exc,
                )
                failed.append({"module": module_name, "error": f"{type(exc).__name__}: {exc}"})

    tool_count = len(registry.list_entries())
    if failed and reloaded:
        status = "partial"
    elif failed:
        status = "error"
    else:
        status = "ok"

    logger.info(
        "ReloadTools 完成: status=%s, reloaded=%d, failed=%d, tool_count=%d",
        status, len(reloaded), len(failed), tool_count,
    )
    return json.dumps(
        {
            "status": status,
            "reloaded": reloaded,
            "failed": failed,
            "tool_count": tool_count,
            "note": "file_tools 等模块的文件权限状态已重置；若某模块 import 了被重载模块的内部符号，旧引用仍指向旧对象",
        },
        ensure_ascii=False,
    )


registry.register(
    name="ReloadTools",
    description=(
        "Hot-reload builtin tool modules after editing their source code, without restarting the agent. "
        "Pass 'modules' (array of module names like 'Tools.builtin.file_tools') to reload specific modules; "
        "omit it to reload all modules listed in config tools.builtin_modules. "
        "Already-loaded modules are reloaded in place; new modules are imported. "
        "Returns JSON with status/reloaded/failed/tool_count. "
        "A failed module keeps its previous tool behavior (old handlers stay registered)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Module names to reload (e.g. ['Tools.builtin.file_tools']). Omit to reload all configured builtin modules.",
            },
        },
    },
    handler=reload_tools,
    group="core",
)
