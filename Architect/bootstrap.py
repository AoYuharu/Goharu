"""Bootstrap：裸机首次部署的初始化编排。

identity 缺失时同步完成 L1→L5 哑探测、HaruNet 最小注册和 identity 落盘，
随后按 gaps 与 initial_exploration 决定是否只唤醒一次 Arch 探索。
gateway 通过 daemon 线程调用，启动主链路不被阻塞。
"""

import logging
import platform
import threading
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_PATH = "runtime_memory/identity.yaml"


def _initial_exploration(cfg):
    value = cfg.get("initial_exploration", "")
    return " ".join(str(value).split()) if value is not None else ""


def _layered_gaps(identity, gaps):
    """按 probe_report 分层；注入的旧式 fake probe 归入 unclassified。"""
    path_layers = {}
    for layer, report in identity.get("probe_report", {}).items():
        for path in report.get("fields", {}):
            path_layers[path] = layer
    layered = {}
    for path in gaps:
        layered.setdefault(path_layers.get(path, "unclassified"), []).append(path)
    return layered


def _verified_facts(identity):
    facts = {}
    for layer, report in identity.get("probe_report", {}).items():
        for path, field in report.get("fields", {}).items():
            if field.get("status") != "verified":
                continue
            category, key = path.split(".", 1)
            facts.setdefault(layer, {})[path] = identity.get(category, {}).get(key)
    return facts


def _platform_readonly_commands():
    system = platform.system()
    if system == "Windows":
        return [
            "powershell -NoProfile -Command \"Get-CimInstance <只读查询>\"",
            "powershell -NoProfile -Command \"Get-PnpDevice -Class <设备类>\"",
            "ipconfig /all",
        ]
    if system == "Darwin":
        return [
            "system_profiler <SPUSBDataType/SPCameraDataType/...>",
            "sysctl -n <只读键>",
            "ioreg -l（只读，按需过滤输出）",
        ]
    return [
        "cat /proc/<只读文件>；cat /sys/<只读文件>",
        "ip -brief addr；ip route show default",
        "lsusb",
        "systemctl status <服务>（仅查询，禁止 start/stop/restart）",
    ]


def bootstrap_if_needed(hub=None, config: dict | None = None,
                        register_fn=None, force: bool = False,
                        run_probe_fn=None) -> dict | None:
    """identity 缺失时执行首次部署编排。返回结果 dict；跳过返回 None。

    Args:
        hub: ArchitectHub（探索派发；None 时跳过 Arch 步骤）
        config: dict（读 architect_hub.bootstrap.*）
        register_fn: HaruNet 最小注册 Callable[[identity], dict]（未接线跳过）
        force: True 时无视已有 identity 强制重跑
        run_probe_fn: 探测函数注入（测试用；默认 probe.run_probe）
    """
    cfg = (config or {}).get("architect_hub", {}).get("bootstrap", {}) \
        if isinstance(config, dict) else {}
    identity_path = Path(cfg.get("identity_path", DEFAULT_IDENTITY_PATH))
    initial_exploration = _initial_exploration(cfg)

    if identity_path.exists() and not force:
        logger.info("bootstrap: identity 已存在（%s），跳过", identity_path)
        return None

    if run_probe_fn is None:
        from Architect.probe import run_probe
        run_probe_fn = run_probe

    started = time.time()
    result = {"identity_path": str(identity_path), "steps": {}}

    # 先完成全部本地规则探测，再决定是否唤醒 Arch。
    identity, gaps = run_probe_fn()
    identity["probed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    identity["gaps_pending"] = list(gaps)
    result["steps"]["probe"] = {
        "gaps": len(gaps),
        "initial_exploration": bool(initial_exploration),
    }
    logger.info("bootstrap: probe done, %d gaps, initial_exploration=%s",
                len(gaps), bool(initial_exploration))

    # HaruNet 最小注册保持原语义：未接线或失败只标记待重试，不阻塞本地流程。
    if register_fn is not None:
        try:
            reg = register_fn(identity) or {}
            identity["harunet"] = {"registered": True,
                                   "agent_id": reg.get("agent_id")}
            result["steps"]["harunet"] = "registered"
        except Exception as exc:
            logger.warning("bootstrap: HaruNet 注册失败（标记待重试）: %s", exc)
            identity["harunet"] = {"registered": False, "pending_retry": True}
            result["steps"]["harunet"] = f"failed: {exc}"
    else:
        identity["harunet"] = {"registered": False, "pending_retry": True}
        result["steps"]["harunet"] = "not_wired"

    # 先落盘，Arch 只能补写这个 identity 文件。
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = identity_path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(identity, allow_unicode=True,
                                  sort_keys=False), encoding="utf-8")
    tmp.replace(identity_path)
    logger.info("bootstrap: identity written → %s", identity_path)

    should_explore = bool(gaps or initial_exploration)
    if should_explore and hub is not None:
        from Architect.wake import KIND_EXPLORE, WakeRequest

        layered_gaps = _layered_gaps(identity, gaps)
        hub.request_wake(WakeRequest(
            reason="bootstrap", kind=KIND_EXPLORE, priority=3,
            context={
                "trigger": "bootstrap_exploration",
                "identity_path": str(identity_path),
                "os": identity.get("os", {}),
                "gaps": ", ".join(gaps),
                "layered_gaps": layered_gaps,
                "verified_facts": _verified_facts(identity),
                "initial_exploration": initial_exploration,
                "platform_readonly_commands": _platform_readonly_commands(),
                "instruction": (
                    "执行首次部署只读探索。先阅读 verified_facts 和 identity，"
                    "不要重复已验证探测。initial_exploration 仅表示探索优先级，"
                    "不是必须完成的操作指令。禁止安装软件、修改源码或配置、驱动或"
                    "控制真实设备；唯一允许写入的是上述 identity_path。补齐字段时，"
                    "同步把 probe_report 对应字段更新为 verified 并写精简证据；确认"
                    "不可用标 unavailable，确认不适用标 not_applicable，仍无法确认"
                    "则保留 unknown。每解决一个 unknown 就从 gaps_pending 删除对应"
                    "路径，全部解决后清空。不得在 identity 中保存敏感原始命令输出。"
                ),
            },
        ))
        suffix = ", initial_exploration" if initial_exploration else ""
        result["steps"]["arch_gapfill"] = (
            f"dispatched ({len(gaps)} gaps{suffix})")
    elif should_explore:
        result["steps"]["arch_gapfill"] = "no_hub"
    else:
        result["steps"]["arch_gapfill"] = "no_gaps"

    result["elapsed_seconds"] = round(time.time() - started, 1)
    logger.info("bootstrap done in %.1fs: %s", result["elapsed_seconds"],
                result["steps"])
    return result


def bootstrap_async(**kwargs):
    """守护线程入口：gateway 启动时调用，绝不阻塞主链路。"""
    threading.Thread(target=_bootstrap_safe, kwargs=kwargs, daemon=True,
                     name="architect-bootstrap").start()


def _bootstrap_safe(**kwargs):
    try:
        bootstrap_if_needed(**kwargs)
    except Exception as exc:
        logger.warning("bootstrap crashed (non-fatal): %s", exc)
