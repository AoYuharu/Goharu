"""HaruNet 接入边界契约测试：未接入 = 对组网一无所知。

覆盖四条边界：
1. 提示词组：base.md 零组网文本；组网段独立成 harunet.md，仅启用时叠加
2. 工具组：config 静态表不含组网工具；默认装配后 registry 里没有它们
3. 接入开关：config 默认关闭；HARUNET_ENABLED 环境变量覆盖
4. 安装器：接入问询写 HARUNET_ENABLED（静态检查）

运行：python tests/test_harunet_membership.py（独立进程——注册表是进程级单例）
"""

import asyncio
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

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


NET_WORDS = ("HaruNet", "AgentNet", "组网", "harunet_", "floor_",
             "话语权", "haru-box")


def main():
    base = (ROOT / "prompts" / "actor" / "base.md").read_text(encoding="utf-8")
    net_md = ROOT / "prompts" / "actor" / "harunet.md"

    print("\n[1] 提示词组：基础提示词零组网内容")
    leaks = [w for w in NET_WORDS if w in base]
    check("base.md 不含任何组网词", not leaks, str(leaks))
    check("组网段独立成文件", net_md.exists())
    if net_md.exists():
        net_text = net_md.read_text(encoding="utf-8")
        check("组网段含 HaruNet 成员规则", "HaruNet" in net_text
              and "harunet_reply" in net_text and "floor_handoff" in net_text)

    print("\n[2] 叠加层：只有组合根追加时组网段才进 system")
    from configurationLoader import config
    from Gateway.prompt_sections import PromptSectionsOverlay
    from Prompting.api import create_prompt_loader

    off = create_prompt_loader(PromptSectionsOverlay(config, actor=[]))
    on = create_prompt_loader(
        PromptSectionsOverlay(config, actor=["./prompts/actor/harunet.md"]))
    off_titles = [s.title for s in off.load_system_sections("actor")]
    on_titles = [s.title for s in on.load_system_sections("actor")]
    check("未启用 → 仅 base", off_titles == ["base"], str(off_titles))
    check("启用 → base + harunet", on_titles == ["base", "harunet"], str(on_titles))
    off_text = "\n".join(s.content for s in off.load_system_sections("actor"))
    check("未启用 system 零组网词",
          not [w for w in NET_WORDS if w in off_text],
          str([w for w in NET_WORDS if w in off_text]))
    check("叠加层其余键原样委托", PromptSectionsOverlay(config, actor=[]).get(
        "prompt.system.day_summary") == config.get("prompt.system.day_summary"))

    print("\n[3] 工具组：默认装配不含组网工具")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    mods = cfg["tools"]["builtin_modules"]
    check("config 静态表无 harunet_tools/floor_tools",
          not [m for m in mods if "harunet_tools" in m or "floor_tools" in m],
          str(mods))
    from Tools.api import create_tools
    from Tools.registry import registry
    asyncio.run(create_tools())
    names = {e.name for e in registry.list_entries()}
    check("registry 无 harunet_* 工具", not [n for n in names if n.startswith("harunet_")])
    check("registry 无 floor_* 工具", not [n for n in names if n.startswith("floor_")])

    print("\n[3.1] 启用时：条件工具在首次定义快照前注册")
    # 与 SessionManager 的真实顺序一致：先 import 条件模块，再 create_tools。
    from Tools.builtin import harunet_tools, floor_tools  # noqa: F401
    enabled_tools = asyncio.run(create_tools())
    enabled_defs = asyncio.run(enabled_tools.list_tools())
    enabled_names = {d["name"] for d in enabled_defs}
    check("启用快照含 harunet_* 工具",
          {"harunet_summary", "harunet_read", "harunet_reply",
           "harunet_presence", "harunet_update_profile",
           "harunet_reconnect", "harunet_set_password"} <= enabled_names, str(sorted(enabled_names)))
    check("启用快照含 floor_* 工具",
          {"floor_handoff", "floor_release", "floor_status"} <= enabled_names)
    check("始终注册 ErrorHook（Arch 自管理规则）", "ErrorHook" in enabled_names,
          str(sorted(enabled_names)))
    from Agent.SubAgent import SubAgentConfig
    check("Architect 白名单含 ErrorHook",
          "ErrorHook" in SubAgentConfig.get_allowed_tools("architect"))

    print("\n[4] 接入开关：默认关闭，环境变量覆盖")
    check("config 默认 harunet.enabled=false", cfg["harunet"]["enabled"] is False)
    from Gateway.session_manager import _harunet_enabled
    saved_old = os.environ.pop("AGENTNET_ENABLED", None)   # 本机 .env 可能带旧名
    os.environ.pop("HARUNET_ENABLED", None)
    check("无环境变量 → 跟随 config（false）", _harunet_enabled() is False)
    os.environ["HARUNET_ENABLED"] = "true"
    check("HARUNET_ENABLED=true → 接入", _harunet_enabled() is True)
    os.environ["HARUNET_ENABLED"] = "0"
    check("HARUNET_ENABLED=0 → 不接入", _harunet_enabled() is False)
    os.environ.pop("HARUNET_ENABLED", None)
    os.environ["AGENTNET_ENABLED"] = "true"
    check("旧名 AGENTNET_ENABLED 仍兼容", _harunet_enabled() is True)
    os.environ.pop("AGENTNET_ENABLED", None)
    if saved_old is not None:
        os.environ["AGENTNET_ENABLED"] = saved_old

    print("\n[5] 安装器：多栏表单落 HARUNET_*（默认不接入）")
    inst = (ROOT / "install.py").read_text(encoding="utf-8")
    check("step_write_channels 写 HARUNET_ENABLED",
          '_set_env_line(lines, "HARUNET_ENABLED"' in inst)
    check("表单默认不接入 HaruNet", '"harunet": False' in inst)
    check("表单含接入密码项", '"key": "harunet_password", "label": "接入密码"' in inst)
    check("安装器无旧名 AGENTNET_", "AGENTNET_" not in inst)

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
