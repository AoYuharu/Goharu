"""ReloadTools 热重载测试 —— 离线可跑。

覆盖：
1. 临时目录造假工具模块（写入 sys.path）→ import 注册 v1 → 改写文件 →
   ReloadTools 重载 → registry 里是新行为（description + handler 返回值）
2. 语法错误模块进 failed 列表，旧工具仍可用（status=error）
3. 部分失败 → status=partial
4. 缺省参数 → 走注入配置 tools.builtin_modules
5. runtime.call_tool("ReloadTools") 后 last_tool_definitions 已刷新

运行：python tests/test_reload_tools.py
"""

import asyncio
import importlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Tools.api import reset_config, set_config  # noqa: E402
from Tools.registry import registry  # noqa: E402
from Tools.runtime import InProcessToolRuntime  # noqa: E402
from Tools.builtin.reload_tools import reload_tools  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


MODULE_V1 = '''import json
from Tools.registry import registry

def fake_tool() -> str:
    return json.dumps({{"version": "v1"}}, ensure_ascii=False)

registry.register(
    name="{tool_name}",
    description="fake tool v1",
    arguments_schema={{"type": "object", "properties": {{}}}},
    handler=fake_tool,
    group="custom",
)
'''

MODULE_V2 = '''import json
from Tools.registry import registry

def fake_tool() -> str:
    return json.dumps({{"version": "v2", "reloaded": True}}, ensure_ascii=False)

registry.register(
    name="{tool_name}",
    description="fake tool v2 HOT RELOADED",
    arguments_schema={{"type": "object", "properties": {{}}}},
    handler=fake_tool,
    group="custom",
)
'''

MODULE_BROKEN = "this is not valid python syntax ((("


def write_module(tmpdir, module_name, source):
    path = Path(tmpdir) / f"{module_name}.py"
    path.write_text(source, encoding="utf-8")
    # 清掉可能命中旧源码的字节码缓存，保证 reload 重新编译
    pyc = importlib.util.cache_from_source(str(path))
    Path(pyc).unlink(missing_ok=True)
    importlib.invalidate_caches()
    return path


def main():
    print("=" * 60)
    print("ReloadTools 热重载测试")
    print("=" * 60)

    tmpdir = tempfile.mkdtemp(prefix="reload_tools_test_")
    sys.path.insert(0, tmpdir)
    mod_ok = "fake_reload_ok_mod"
    mod_bad = "fake_reload_bad_mod"
    tool_ok = "ReloadFakeOk"
    tool_bad = "ReloadFakeBad"

    try:
        # --------------------------------------------------------------
        # 1. v1 注册 → 改写 → 重载 → 新行为生效
        # --------------------------------------------------------------
        print("\n[1] 正常重载：v1 → v2")
        write_module(tmpdir, mod_ok, MODULE_V1.format(tool_name=tool_ok))
        importlib.import_module(mod_ok)
        entry = registry.get_entry(tool_ok)
        check("v1 已注册", entry is not None and entry.description == "fake tool v1",
              str(entry))

        raw = asyncio.run(registry.dispatch(tool_ok, {}))
        check("v1 handler 返回 v1", json.loads(raw).get("version") == "v1", raw)

        write_module(tmpdir, mod_ok, MODULE_V2.format(tool_name=tool_ok))
        result = json.loads(reload_tools(modules=[mod_ok]))
        check("重载 status=ok", result.get("status") == "ok", result)
        check("reloaded 含目标模块", mod_ok in result.get("reloaded", []), result)
        check("failed 为空", result.get("failed") == [], result)
        check("tool_count 是整数", isinstance(result.get("tool_count"), int), result)
        check("返回带 note 提醒", isinstance(result.get("note"), str) and result["note"], result)

        entry = registry.get_entry(tool_ok)
        check("registry description 更新为 v2",
              entry is not None and entry.description == "fake tool v2 HOT RELOADED",
              str(entry))
        raw = asyncio.run(registry.dispatch(tool_ok, {}))
        check("handler 返回 v2（新行为生效）",
              json.loads(raw).get("version") == "v2", raw)

        # --------------------------------------------------------------
        # 2. 语法错误模块 → failed，旧工具仍在
        # --------------------------------------------------------------
        print("\n[2] 语法错误：进 failed 且旧工具保留")
        write_module(tmpdir, mod_bad, MODULE_V1.format(tool_name=tool_bad))
        importlib.import_module(mod_bad)
        write_module(tmpdir, mod_bad, MODULE_BROKEN)

        result = json.loads(reload_tools(modules=[mod_bad]))
        check("全部失败 status=error", result.get("status") == "error", result)
        check("failed 含目标模块",
              any(f.get("module") == mod_bad for f in result.get("failed", [])),
              result)
        check("failed 带 error 信息",
              bool(result["failed"][0].get("error")), result)

        raw = asyncio.run(registry.dispatch(tool_bad, {}))
        check("旧 handler 仍可用（返回 v1）",
              json.loads(raw).get("version") == "v1", raw)

        # --------------------------------------------------------------
        # 3. 部分失败 → partial，成功的不被中断
        # --------------------------------------------------------------
        print("\n[3] 部分失败：status=partial")
        result = json.loads(reload_tools(modules=[mod_bad, mod_ok]))
        check("status=partial", result.get("status") == "partial", result)
        check("好的模块仍被处理", mod_ok in result.get("reloaded", []), result)
        check("坏的模块进 failed",
              any(f.get("module") == mod_bad for f in result.get("failed", [])),
              result)

        # 未加载过的模块走 import_module 分支
        mod_new = "fake_reload_new_mod"
        write_module(tmpdir, mod_new, MODULE_V1.format(tool_name="ReloadFakeNew"))
        result = json.loads(reload_tools(modules=[mod_new]))
        check("新模块被 import 加载",
              mod_new in result.get("reloaded", []) and mod_new in sys.modules,
              result)
        check("新模块的工具已注册",
              registry.get_entry("ReloadFakeNew") is not None)

        # --------------------------------------------------------------
        # 4. 缺省参数 → 走配置 tools.builtin_modules
        # --------------------------------------------------------------
        print("\n[4] 缺省参数走配置")
        set_config({"tools.builtin_modules": [mod_ok]})
        try:
            result = json.loads(reload_tools())
            check("缺省时重载配置里的模块",
                  result.get("reloaded") == [mod_ok] and result.get("status") == "ok",
                  result)
        finally:
            reset_config()

        # --------------------------------------------------------------
        # 5. runtime.call_tool 后刷新 last_tool_definitions
        # --------------------------------------------------------------
        print("\n[5] runtime 刷新工具定义缓存")
        write_module(tmpdir, mod_ok, MODULE_V1.format(tool_name=tool_ok))
        sys.modules.pop(mod_ok, None)  # 强制 initialize 重新 import 拿到 v1
        runtime = asyncio.run(
            InProcessToolRuntime(modules=[mod_ok, "Tools.builtin.reload_tools"]).initialize()
        )
        defs = asyncio.run(runtime.list_tools())
        desc_before = {d["name"]: d["description"] for d in defs}.get(tool_ok)
        check("初始定义是 v1", desc_before == "fake tool v1", str(defs))

        write_module(tmpdir, mod_ok, MODULE_V2.format(tool_name=tool_ok))
        res = asyncio.run(runtime.call_tool("ReloadTools", {"modules": [mod_ok]}))
        check("call_tool 返回含 reloaded", "reloaded" in str(res.content),
              str(res.content)[:200])
        desc_after = {d["name"]: d["description"] for d in runtime.last_tool_definitions}.get(tool_ok)
        check("last_tool_definitions 已刷新为 v2",
              desc_after == "fake tool v2 HOT RELOADED",
              f"before={desc_before!r} after={desc_after!r}")
    finally:
        # 清理：sys.path / sys.modules / 注册表 / 配置
        if tmpdir in sys.path:
            sys.path.remove(tmpdir)
        for name in (mod_ok, mod_bad, "fake_reload_new_mod"):
            sys.modules.pop(name, None)
        for tname in (tool_ok, tool_bad, "ReloadFakeNew"):
            registry.unregister(tname)
        reset_config()

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
