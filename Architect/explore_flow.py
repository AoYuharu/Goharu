"""bootstrap 初始探索的闭环编排：Arch 探索 → Verify 验收 → 失败回喂迭代。

由 hub 的 KIND_EXPLORE 派发链调用，拿结构化 request.context（不解析 task_text）。
本模块不 import Gateway/Tools/Agent 顶层依赖——SubAgent 与 configurationLoader
均在函数内惰性 import（遵守 Architect 模块解耦不变量）。

终止条件（防无尽循环）：① gaps 清空（硬规则）② verify 判 PASS（含"已尽力"）
③ 达 max_rounds。三者命中其一即停。
"""

import logging
import uuid
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_PATH = "runtime_memory/identity.yaml"
DEFAULT_MAX_ROUNDS = 3


def _compact(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _max_rounds(context: dict) -> int:
    raw = (context or {}).get("explore_max_rounds")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_ROUNDS


def _read_identity(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        logger.warning("explore: identity 不存在（%s），视作空", path)
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("explore: 读 identity 失败（%s）: %s", path, exc)
        return {}


def _hard_rule_pass(gaps) -> bool:
    """硬规则：gaps_pending 为空 = 五层全部解决（verified/unavailable/not_applicable）。"""
    return not gaps


def _verdict_of(verify_result) -> str | None:
    if isinstance(verify_result, dict):
        verdict = verify_result.get("verdict")
        if verdict:
            return str(verdict).upper()
    return None


def _feedback_text(verify_result) -> str:
    """从 verify 报告提取 FAIL 项，回喂下一轮 Arch。"""
    if isinstance(verify_result, dict):
        content = verify_result.get("content") or ""
        if content:
            return content[:2000]
    return "(verify 无输出)"


def _arch_task_text(round_no: int, context: dict, feedback: str) -> str:
    identity_path = context.get("identity_path", DEFAULT_IDENTITY_PATH)
    verified_facts = context.get("verified_facts", {})
    layered_gaps = context.get("layered_gaps", {})
    initial_exploration = _compact(context.get("initial_exploration", ""))
    platform_cmds = context.get("platform_readonly_commands", [])
    instruction = context.get("instruction", "")
    voice_suite = context.get("voice_suite") or {}
    actuation = context.get("actuation") or {}

    parts = [
        f"首次部署只读探索（第 {round_no} 轮）。先读 identity（{identity_path}），"
        "不要重复已验证探测。",
        f"identity 路径: {identity_path}",
    ]
    if verified_facts:
        parts.append("已验证事实（勿重复探测）:\n"
                     + yaml.safe_dump(verified_facts, allow_unicode=True, sort_keys=False))
    if layered_gaps:
        parts.append("分层缺口（需补齐）:\n"
                     + yaml.safe_dump(layered_gaps, allow_unicode=True, sort_keys=False))
    if voice_suite:
        parts.append("语音套件状态（只记录，不安装/不下载/不激活）:\n"
                     + yaml.safe_dump(voice_suite, allow_unicode=True, sort_keys=False))
    if actuation:
        parts.append("执行能力线索（LED/GPIO/风扇，只读）:\n"
                     + yaml.safe_dump(actuation, allow_unicode=True, sort_keys=False))
        parts.append(
            "LED 裁决约束：只有 sysfs leds-class + brightness 可写 + trigger 可保存"
            "恢复 + 非 regulator/非键盘背光/非锁定键才可标 supported；否则 "
            "unsupported/unknown 并写证据。禁止写 /sys、/dev，禁止 tee/gpioset/"
            "openrgb/pinctrl/raspi-gpio；风扇永不作为指示器，永不写 PWM。"
        )
    if initial_exploration:
        parts.append(f"初始探索方向（优先级提示，非必须完成指令）: {initial_exploration}")
    if platform_cmds:
        parts.append("平台只读命令参考:\n"
                     + "\n".join(f"- {c}" for c in platform_cmds))
    if feedback:
        parts.append("上一轮验收未通过的缺口（请针对性补齐）:\n" + feedback)
        parts.append(
            "约束强化：对确实无法只读确认的项，必须在 probe_report 对应 field 的 "
            "evidence 里写明凭据（尝试了什么只读手段 + 为何失败/不可得/不适用），"
            "不得留 unknown 无凭据。"
        )
    if instruction:
        parts.append("硬约束:\n" + instruction)
    return "\n\n".join(parts)


def _verify_task_text(identity_path: str, gaps: list, round_no: int) -> str:
    gaps_text = ", ".join(gaps) if gaps else "（空）"
    return (
        f"验收 bootstrap 环境探测结果（第 {round_no} 轮）。对象: {identity_path}\n\n"
        f"当前 gaps_pending: {gaps_text}\n\n"
        "请读 identity.yaml 的 probe_report 与画像字段，判断：\n"
        "1. L1-L5 五层是否都探索到位（gaps 清空，或每个 remaining unknown 的 "
        "evidence 都有\"已尽力\"凭据）；\n"
        "2. 这台机器能否给出具体定位（设备类型/OS/CPU/GPU/网络/感知/执行能力）。\n\n"
        "允许用只读命令交叉复核；禁止写文件/安装/驱动设备。\n"
        "输出 VERDICT: PASS/FAIL/PARTIAL；FAIL 时逐项列出缺凭据或阻碍定位的 层.字段。"
    )


def _make_subagent(agent_type: str, task_text: str, agent_id: str):
    """构造并执行子代理（惰性 import，独立部署时 ImportError 抛给调用方降级）。"""
    from Agent.SubAgent import SubAgent
    from Tools.builtin.agent_delegate import notify_output
    from Tools.registry import registry
    from Tools.subagent_config import get_subagent_llm

    subagent = SubAgent(
        agent_type=agent_type, task=task_text, agent_id=agent_id,
        tools_registry=registry, llm=get_subagent_llm(),
        output_callback=notify_output,
    )
    return subagent.execute()


def _dispatch_architect(round_no: int, context: dict, feedback: str) -> dict:
    try:
        agent_id = f"architect_explore_{uuid.uuid4().hex[:8]}"
        return _make_subagent(
            "architect", _arch_task_text(round_no, context, feedback), agent_id)
    except Exception as exc:
        logger.warning("explore: architect 派发失败（第 %d 轮）: %s", round_no, exc)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _dispatch_verify(identity_path: str, gaps: list, round_no: int) -> dict:
    try:
        agent_id = f"verify_explore_{uuid.uuid4().hex[:8]}"
        return _make_subagent(
            "verify_explore", _verify_task_text(identity_path, gaps, round_no), agent_id)
    except Exception as exc:
        logger.warning("explore: verify 派发失败（第 %d 轮）: %s", round_no, exc)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _summary(result: dict) -> str:
    verdict = result.get("verdict") or "UNKNOWN"
    rounds = result.get("rounds", [])
    remaining = result.get("remaining_gaps") or []
    lines = [
        f"bootstrap 探索验收结论: {verdict}",
        f"轮次: {len(rounds)}，剩余 unknown: {len(remaining)}",
    ]
    if remaining:
        lines.append("剩余 unknown 字段: " + ", ".join(remaining))
    return "\n".join(lines)


def run_explore_with_verify(context: dict | None = None,
                            dispatch_arch=None, dispatch_verify=None,
                            read_identity=None) -> dict:
    """Arch 探索 → Verify 验收闭环。返回含 verdict / rounds / content 的汇总 dict。

    dispatch_arch / dispatch_verify / read_identity 供测试注入；None 用真实实现。
    """
    context = context or {}
    identity_path = context.get("identity_path", DEFAULT_IDENTITY_PATH)
    max_rounds = _max_rounds(context)

    if dispatch_arch is None:
        dispatch_arch = _dispatch_architect
    if dispatch_verify is None:
        dispatch_verify = _dispatch_verify
    if read_identity is None:
        read_identity = _read_identity

    feedback = ""
    result = {
        "identity_path": identity_path,
        "rounds": [],
        "verdict": None,
        "remaining_gaps": None,
        "status": "success",
    }

    for round_no in range(1, max_rounds + 1):
        arch = dispatch_arch(round_no, context, feedback) or {}
        arch_status = arch.get("status") if isinstance(arch, dict) else "error"

        identity = read_identity(identity_path) or {}
        gaps = list(identity.get("gaps_pending", []))

        verify = dispatch_verify(identity_path, gaps, round_no) or {}
        verdict = _verdict_of(verify)

        result["rounds"].append({
            "round": round_no,
            "arch_status": arch_status,
            "gaps": list(gaps),
            "verdict": verdict,
        })
        logger.info("explore round %d: arch=%s gaps=%d verdict=%s",
                    round_no, arch_status, len(gaps), verdict)

        if _hard_rule_pass(gaps):
            result["verdict"] = "PASS"
            result["remaining_gaps"] = []
            break
        if verdict == "PASS":
            result["verdict"] = "PASS"
            result["remaining_gaps"] = list(gaps)
            break
        if round_no >= max_rounds:
            result["verdict"] = verdict or "PARTIAL"
            result["status"] = "best_effort"
            result["remaining_gaps"] = list(gaps)
            break
        feedback = _feedback_text(verify)

    result["content"] = _summary(result)
    return result
