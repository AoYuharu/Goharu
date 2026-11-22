"""Bootstrap —— 裸机首次部署的初始化编排。

流程（全程 daemon 线程，不阻塞 gateway 启动）：
  1. HaruNet 最小注册（可注入 register_fn；未接线/失败降级为标记待重试）
  2. 哑探测层跑 12 类系统探测（probe.py，秒级，零 LLM）
  3. gaps 非空 → 唤醒 Arch 补漏扫描（Arch 把结果写回 identity.yaml）
  4. identity.yaml 落盘 = 完成标记（存在即跳过，删除可强制重跑）

HaruNet 注册拆两步的原因：网络不可达时 bootstrap 不能被卡死——
先本地探测出 identity，注册挂起待 HaruNet 恢复后补。
"""

import logging
import threading
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_PATH = "runtime_memory/identity.yaml"


def bootstrap_if_needed(hub=None, config: dict | None = None,
                        register_fn=None, force: bool = False,
                        run_probe_fn=None) -> dict | None:
    """identity 缺失时执行首次部署编排。返回结果 dict；跳过返回 None。

    Args:
        hub: ArchitectHub（gaps 补漏派发；None 时跳过 Arch 步骤）
        config: dict（读 architect_hub.bootstrap.*）
        register_fn: HaruNet 最小注册 Callable[[identity], dict]（未接线跳过）
        force: True 时无视已有 identity 强制重跑
        run_probe_fn: 探测函数注入（测试用；默认 probe.run_probe）
    """
    cfg = (config or {}).get("architect_hub", {}).get("bootstrap", {}) \
        if isinstance(config, dict) else {}
    identity_path = Path(cfg.get("identity_path", DEFAULT_IDENTITY_PATH))

    if identity_path.exists() and not force:
        logger.info("bootstrap: identity 已存在（%s），跳过", identity_path)
        return None

    if run_probe_fn is None:
        from Architect.probe import run_probe
        run_probe_fn = run_probe

    started = time.time()
    result = {"identity_path": str(identity_path), "steps": {}}

    # ── 2. 哑探测层（先本地探，网络无关）──
    identity, gaps = run_probe_fn()
    identity["probed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    identity["gaps_pending"] = list(gaps)
    result["steps"]["probe"] = {"gaps": len(gaps)}
    logger.info("bootstrap: probe done, %d gaps", len(gaps))

    # ── 1. HaruNet 最小注册（探测后；失败不阻塞）──
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

    # ── 4. identity 落盘（先落盘，Arch 补漏直接改这个文件）──
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = identity_path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(identity, allow_unicode=True,
                                  sort_keys=False), encoding="utf-8")
    tmp.replace(identity_path)
    logger.info("bootstrap: identity written → %s", identity_path)

    # ── 3. gaps → Arch 补漏 ──
    if gaps and hub is not None:
        from Architect.wake import WakeRequest
        hub.request_wake(WakeRequest(
            reason="bootstrap", kind="guardian", priority=1,
            context={
                "trigger": "bootstrap_gaps",
                "identity_path": str(identity_path),
                "gaps": ", ".join(gaps),
                "instruction": (
                    f"首次部署探测有 {len(gaps)} 个字段没探到（gaps 列表见上）。"
                    f"请用 run_cmd 在本机补探这些字段，把结果写回 "
                    f"{identity_path} 对应位置，并把 gaps_pending 清空。"
                    f"探不到的字段标注 \"unavailable\" 并说明原因。"),
            },
        ))
        result["steps"]["arch_gapfill"] = f"dispatched ({len(gaps)} gaps)"
    else:
        result["steps"]["arch_gapfill"] = "no_gaps" if not gaps else "no_hub"

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
