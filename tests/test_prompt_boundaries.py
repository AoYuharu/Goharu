"""提示词与工具描述边界契约测试。

运行：python tests/test_prompt_boundaries.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    print("=" * 60)
    print("提示词与工具描述边界测试")
    print("=" * 60)

    base = (ROOT / "prompts" / "actor" / "base.md").read_text(encoding="utf-8")
    explore = (ROOT / "prompts" / "subagent" / "explore.md").read_text(encoding="utf-8")
    architect = (ROOT / "prompts" / "subagent" / "architect.md").read_text(encoding="utf-8")
    harunet = (ROOT / "prompts" / "actor" / "harunet.md").read_text(encoding="utf-8")
    delegate = (ROOT / "Tools" / "builtin" / "agent_delegate.py").read_text(encoding="utf-8")
    verify_explore = (ROOT / "prompts" / "subagent" / "verify_explore.md").read_text(
        encoding="utf-8")
    bootstrap_src = (ROOT / "Architect" / "bootstrap.py").read_text(encoding="utf-8")

    print("\n[1] 主 Actor 证据协议")
    check("含 Evidence Before Claims", "Evidence Before Claims" in base)
    check("要求区分 inference / unknown", "inference or unknown" in base)
    check("限制中间 text 只能写行动与已观察结果",
          "Intermediate text sent while tools are running" in base)
    check("直接回答以证据充分为前提", "Enough evidence for every decisive claim" in base)
    check("修正 no extra disclaimers 冲突", "express uncertainty only where evidence is incomplete" in base)

    print("\n[2] 广域扫描与 DiskUsage 路由")
    check("Explore 职责含 filesystem/disk usage/system metadata",
          "filesystem layout, disk usage, and system metadata" in base)
    check("要求非重叠分区", "non-overlapping" in base.lower())
    check("禁止主 Agent 原始广域扫描", "must not run the raw broad scan itself" in base)
    check("单根多子树仍算广域扫描",
          "A single root path still counts as broad" in base)
    check("DiskUsage 是权威扫描器", "DiskUsage` is the authoritative size scanner" in base)
    check("delegation_required 时禁止改用 run_cmd",
          "delegation_required" in base and "do not switch to `run_cmd`" in base)
    check("partial/error 不是确认事实", "Partial/error reports are not confirmed facts" in base)

    print("\n[3] Explore 提示词")
    check("Explore 含 filesystem/disk-usage",
          "filesystem" in explore and "disk usage" in explore)
    check("Explore 含 DiskUsage 工具", "DiskUsage" in explore)
    check("代码证据仍要求 path:line", "Code claims" in explore and "path:line" in explore)
    check("文件系统证据使用 path + tool + observed value",
          "observed value" in explore)
    check("完整扫描有覆盖度停止条件", "Exhaustive scan" in explore and "incomplete" in explore)

    print("\n[4] 工具描述与 Architect")
    check("AgentDelegate 含 DiskUsage 示例", "DiskUsage" in delegate)
    check("AgentDelegate 强调非重叠目标", "NON-OVERLAPPING" in delegate)
    check("AgentDelegate 不再把 Explore 限定为 codebase",
          "search/understand codebase" not in delegate)
    check("Architect 含证据纪律", "Evidence Discipline" in architect)
    check("HaruNet 无关群消息直接输出屏蔽词",
          "Output exactly `不回复`" in harunet)
    check("HaruNet 屏蔽词不用于主人或 @ 消息",
          "Never use this for owner messages, direct @mentions" in harunet)

    print("\n[5] 语音套件 Runbook 与安全边界")
    check("Architect 含 Voice Suite Runbook", "Voice Suite Runbook" in architect)
    check("Runbook 含激活 LED 规则",
          "voice.activated" in architect and "blink_support.status=supported" in architect)
    check("Runbook 禁止直接 restart 命令",
          "RequestRestart" in architect and "pkill -f" in architect)
    check("Runbook 注明 ASR 模型按需下载",
          "229MB" in architect and "only on explicit user request" in architect)
    check("bootstrap instruction 禁止写 /sys /dev",
          "禁止写 /sys" in bootstrap_src and "禁止写 /dev" in bootstrap_src)
    check("bootstrap instruction 约束不下载模型",
          "不下载模型" in bootstrap_src and "只读" in bootstrap_src)
    check("verify_explore 验收 LED/语音状态",
          "Voice / LED state" in verify_explore and "blink_support" in verify_explore)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
