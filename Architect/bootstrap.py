"""Bootstrap：裸机首次部署的初始化编排。

identity 缺失时同步完成 L1→L5 哑探测、HaruNet 最小注册和 identity 落盘，
随后按 gaps 与 initial_exploration 决定是否只唤醒一次 Arch 探索。
gateway 通过 daemon 线程调用，启动主链路不被阻塞。
"""

import json
import logging
import platform
import threading
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_PATH = "runtime_memory/identity.yaml"
DEFAULT_RESUME_PATH = "runtime_memory/architect/restart_resume.json"


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
            "powershell -NoProfile -Command \"Get-PnpDevice -PresentOnly\"",
            "powershell -NoProfile -Command \"Get-CimInstance Win32_PnPEntity\"",
            "powershell -NoProfile -Command \"Get-Service / Get-Process（查 RGB 套件）\"",
            "ipconfig /all",
        ]
    if system == "Darwin":
        return [
            "system_profiler <SPUSBDataType/SPCameraDataType/...>",
            "sysctl -n <只读键>",
            "ioreg -rc AppleHSMonitor（只读）",
            "ioreg -l（只读，按需过滤输出）",
        ]
    return [
        "cat /proc/<只读文件>；cat /sys/<只读文件>",
        "ls /sys/class/leds/；cat /sys/class/leds/<x>/trigger",
        "cat /sys/class/leds/<x>/{max_brightness,brightness,color,multi_intensity}",
        "cat /sys/class/leds/<x>/device/uevent（driver 名）",
        "ls /sys/class/hwmon/（风扇传感器，只读）",
        "ip -brief addr；ip route show default",
        "lsusb；gpioinfo（只读枚举）",
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
        runtime = identity.get("runtime", {}) or {}
        wake_result = hub.request_wake(WakeRequest(
            reason="bootstrap", kind=KIND_EXPLORE, priority=3,
            context={
                "trigger": "bootstrap_exploration",
                "identity_path": str(identity_path),
                "os": identity.get("os", {}),
                "gaps": ", ".join(gaps),
                "layered_gaps": layered_gaps,
                "verified_facts": _verified_facts(identity),
                "initial_exploration": initial_exploration,
                "voice_suite": {
                    "voice_sdk_present": runtime.get("voice_sdk_present"),
                    "voice_deps_ok": runtime.get("voice_deps_ok"),
                    "voice_asr_model_present": runtime.get("voice_asr_model_present"),
                },
                "actuation": identity.get("actuation", {}),
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
                    "LED/硬件探索硬约束：只读枚举 /sys、/dev、hwmon；禁止写 /sys；"
                    "禁止写 /dev；禁止 tee/gpioset/openrgb/pinctrl/raspi-gpio。只有"
                    "sysfs leds-class、brightness 可写、trigger 可保存恢复、非"
                    "regulator/非键盘背光/非锁定键的 LED 才能标 supported，否则"
                    "unsupported 或 unknown 并写证据。风扇永不作为指示器，永不写 PWM。"
                    "语音套件只记录状态，不安装、不下载模型、不激活。"
                ),
            },
        )) or {}
        suffix = ", initial_exploration" if initial_exploration else ""
        status = wake_result.get("status", "unknown")
        if status == "dispatched":
            result["steps"]["arch_gapfill"] = (
                f"dispatched ({len(gaps)} gaps{suffix})")
        else:
            # busy / cooldown / pending 都不能记成已派发
            result["steps"]["arch_gapfill"] = (
                f"{status} ({len(gaps)} gaps{suffix})")
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


def _default_voice_status():
    """未注入时的默认校验：惰性 import Gateway.voice_service（Architect 顶层不依赖 Gateway）。"""
    from Gateway import voice_service
    return {"listener_running": voice_service.listener_running(),
            "status": voice_service.get_status()}


def resume_after_restart(voice_status_fn=None, config: dict | None = None) -> dict | None:
    """重启后 resume 校验：确认唤醒监听已恢复。

    读取 restart_resume.json；成功则通知用户并删除标记，失败保留标记供 Arch
    诊断。返回结果 dict；无标记返回 None。
    """
    cfg = (config or {}).get("architect_hub", {}).get("bootstrap", {}) \
        if isinstance(config, dict) else {}
    path = Path(cfg.get("restart_resume_path", DEFAULT_RESUME_PATH))
    if not path.exists():
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("resume: 读取标记失败（%s）: %s", path, exc)
        marker = {}

    if voice_status_fn is None:
        voice_status_fn = _default_voice_status
    try:
        status = voice_status_fn() or {}
    except Exception as exc:
        logger.warning("resume: 语音状态校验异常: %s", exc)
        status = {"error": f"{type(exc).__name__}: {exc}"}

    running = bool(status.get("listener_running"))
    result = {"marker": marker, "listener_running": running, "voice": status,
              "status": "ready" if running else "not_ready"}
    if running:
        _notify("重启完成：语音已就绪（唤醒监听已恢复，说「你好」）")
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("resume: 删除标记失败: %s", exc)
        logger.info("resume: 语音监听已恢复，标记已清理")
    else:
        logger.warning("resume: 重启后语音监听未恢复，保留标记供 Arch 诊断")
    return result


def _notify(text: str) -> None:
    try:
        from Architect.hub import get_default_hub
        get_default_hub().notify_user(text)
    except Exception as exc:
        logger.info("resume: notify_user 不可用（%s），消息落日志: %s", exc, text)
