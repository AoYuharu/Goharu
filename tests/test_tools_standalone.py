"""Tools 子系统独立接口测试 —— 外部 harness 视角。

验证 Tools.api 暴露的完整公共接口：
- set_config：dict 注入（扁平/嵌套）优先于全局 configurationLoader
- create_tools → list_tools → call_tool → close_tools 全生命周期
- registry.register 自定义工具 → dispatch（含 schema 校验错误、未知工具）
- check_command_safety 安全层（注入配置生效）

运行：python test_tools_standalone.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Tools.api import (
    check_command_safety,
    close_tools,
    create_tools,
    get_config,
    registry,
    reset_config,
    set_config,
)

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


def main():
    print("=" * 60)
    print("Tools 独立接口测试（外部 harness 视角）")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. 配置注入：扁平/嵌套 dict 都生效，注入优先于全局
    # ------------------------------------------------------------------
    print("\n[1] 配置代理")
    import configurationLoader  # 仓库内存在全局配置，用于验证注入优先级

    set_config({"tools": {"background_timeout": 7}})
    check("嵌套 dict 生效", get_config().get("tools.background_timeout") == 7)
    check("注入优先于全局", get_config() is not configurationLoader.config)
    set_config({"tools.background_timeout": 9})
    check("扁平点号键生效", get_config().get("tools.background_timeout") == 9)
    check("缺失键走默认值", get_config().get("tools.nope", "D") == "D")

    # ------------------------------------------------------------------
    # 2. 安全层：注入配置生效（先于任何 run_cmd，保证单例吃到注入配置）
    # ------------------------------------------------------------------
    print("\n[2] 安全层")
    set_config({
        "tools": {"security": {"enabled": True, "dangerous_commands": ["rm -rf"]}}
    })
    safe, reason = check_command_safety("rm -rf /")
    check("危险命令被拦截", safe is False and reason, f"safe={safe}")
    safe2, _ = check_command_safety("git status")
    check("正常命令放行", safe2 is True)
    set_config({"tools": {"security": {"enabled": True}}})

    # ------------------------------------------------------------------
    # 3. 运行时生命周期：create_tools → list_tools → call_tool → close
    # ------------------------------------------------------------------
    print("\n[3] create_tools 生命周期")
    tools = asyncio.run(
        create_tools(modules=["Tools.builtin.sleep_tool", "Tools.builtin.glob_tool"])
    )
    defs = asyncio.run(tools.list_tools())
    names = {d["name"] for d in defs}
    check("只加载指定模块", {"Sleep", "Glob"} <= names and "run_cmd" not in names,
          str(sorted(names)))
    check("工具定义含 inputSchema",
          all("name" in d and "description" in d and "inputSchema" in d
              for d in defs))

    result = asyncio.run(tools.call_tool("Sleep", {"ms": 10}))
    check("call_tool 返回 ToolResult.content 字符串",
          isinstance(result.content, str) and "Slept" in result.content,
          str(result.content)[:120])

    result = asyncio.run(tools.call_tool("Glob", {"pattern": "Tools/*.py"}))
    check("Glob 找到文件", "registry.py" in str(result.content),
          str(result.content)[:120])

    # ------------------------------------------------------------------
    # 4. 自定义工具注册 + dispatch 校验路径
    # ------------------------------------------------------------------
    print("\n[4] registry 自定义工具")
    registry.register(
        name="echo_tool",
        description="Echo text.",
        arguments_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=lambda text: json.dumps({"echo": text}, ensure_ascii=False),
        group="custom",
    )
    raw = asyncio.run(registry.dispatch("echo_tool", {"text": "你好"}))
    check("自定义工具 dispatch", json.loads(raw).get("echo") == "你好", raw)

    raw = asyncio.run(registry.dispatch("echo_tool", {}))
    check("缺必填参数 → 校验错误", "error" in json.loads(raw), raw)

    raw = asyncio.run(registry.dispatch("echo_tool", {"text": 123}))
    check("类型错误 → 校验错误", "error" in json.loads(raw), raw)

    raw = asyncio.run(registry.dispatch("no_such_tool", {}))
    check("未知工具 → error JSON", "Unknown tool" in json.loads(raw)["error"], raw)

    async def async_tool(text: str) -> str:
        await asyncio.sleep(0.01)
        return json.dumps({"async_echo": text}, ensure_ascii=False)

    registry.register(
        name="async_echo",
        description="Async echo.",
        arguments_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=async_tool,
    )
    raw = asyncio.run(registry.dispatch("async_echo", {"text": "异步"}))
    check("async handler 自动识别", json.loads(raw).get("async_echo") == "异步", raw)

    # ------------------------------------------------------------------
    # 5. close
    # ------------------------------------------------------------------
    print("\n[5] close_tools")
    asyncio.run(close_tools(tools))
    check("close_tools 无异常", True)
    reset_config()

    # ------------------------------------------------------------------
    # 6. 进程追踪器与命令翻译（安全链边缘件）
    # ------------------------------------------------------------------
    print("\n[6] tool_process_tracker / translate_command")
    from Tools.api import tool_process_tracker, translate_command

    tool_process_tracker.register(999999)  # 不可能存在的 PID
    check("register 后进程在册", 999999 in tool_process_tracker._pids)
    tool_process_tracker.unregister(999999)
    check("unregister 后移除", 999999 not in tool_process_tracker._pids)

    cmd_ls, _ = translate_command("dir")
    check("cmd 翻译 dir → ls", cmd_ls == "ls", cmd_ls)
    cmd_git, note_git = translate_command("git status")
    check("cmd 翻译不动安全命令", cmd_git == "git status" and note_git is None)

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
