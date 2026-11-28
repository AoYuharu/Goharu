"""ArchitectHub —— Architect 幕后助手的编排门面。

职责：遥测 digest 入口、唤醒请求路由（冷却过滤 + 派发）、审计管线装配、
版本升级编排。本模块不 import Gateway；channel.py 是唯一的 Gateway 适配点。
派发经 _dispatch_lock 串行（architect max_concurrent=1 的 hub 层兜底）；
background=True 时派发放守护线程——调用方是 turn 线程/看门狗线程，绝不能阻塞。
"""

import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from Architect.audit import AuditPipeline
from Architect.error_hooks import ErrorHookEngine
from Architect.telemetry import TelemetryDigest
from Architect.wake import (
    KIND_EXPLORE, KIND_GUARDIAN, KIND_REPAIR, REASON_CODE_UPDATE,
    REASON_ERROR_HOOK, REASON_FATAL, WakeRequest,
)

logger = logging.getLogger(__name__)

# 瞬态错误模式（网络/限流/服务端过载）：LLM 重试耗尽后报上来也不唤醒——
# 它们会自愈。除此之外的报错视为逻辑错误（代码 bug/消息结构 400/异常），
# 触发 fatal 级守护唤醒。
_TRANSIENT_PATTERNS = (
    "timeout", "timed out", "connection", "econnreset", "network",
    "429", "rate limit", "502", "503", "529", "overloaded",
    "temporarily", "超时", "重试",
)


def classify_error(message: str) -> str:
    """错误分级：transient（瞬态可自愈）/ logic（逻辑错误，需 Architect 介入）。"""
    low = str(message or "").lower()
    for pat in _TRANSIENT_PATTERNS:
        if pat in low:
            return "transient"
    return "logic"


class ArchitectHub:
    """Architect 幕后助手的运行时枢纽。

    Args:
        config: dict（读取 architect_hub.* 键，全部有默认值）
        dispatch: Callable[[kind, task_text], dict] 派发 Architect 子代理；
                  None = 未接线（唤醒请求进 pending 队列，审计退化为机械层）
        merge_fn: Callable[[staging_dir], dict] 审计 PASS 后的合并动作；
                  None = 只出报告不动目录
    """

    def __init__(self, config: dict | None = None, dispatch=None, merge_fn=None):
        cfg = config or {}
        hub_cfg = cfg.get("architect_hub", {}) if isinstance(cfg, dict) else {}
        self.digest = TelemetryDigest(hub_cfg.get("digest_window", 200))
        self._dispatch = dispatch
        self._cooldowns = dict(hub_cfg.get("wake", {}).get("cooldown_seconds", {}))
        self._last_wake_by_reason = {}
        self._pending = deque(maxlen=50)      # dispatch 未接线时的暂存队列
        self._wake_history = deque(maxlen=200)
        self._wake_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()   # architect 串行兜底（非阻塞获取）
        self._hub_cfg = hub_cfg
        self._merge_fn = merge_fn
        self._proxy_provider = None
        # Hero 桥（双向沟通）：Gateway 装配期注入
        #   notify_user(text)  —— 向用户推送报告（走 session 的 channel 广播）
        #   message_hero(text) —— 给主智能体递消息（进它的会话队列，下一轮处理）
        self._notify_user = None
        self._message_hero = None
        self._report_push = bool(hub_cfg.get("report_push", True))
        self._sources = []               # 拉式唤醒源（start_scheduler 轮询）
        self._scheduler_started = False
        # 更新状态机（UpdateFlow）与运行时钩子（Gateway 装配期注入）
        self._update_flow = None
        self._idle_probe = None          # () -> bool：主会话空闲？
        self._confirm_fn = None          # (cmd, desc, timeout) -> bool：确认链
        self._on_update_done = None      # 更新结束回调（解锁主会话）
        self.audit_pipeline = AuditPipeline(
            contract_tests=hub_cfg.get("audit", {}).get("contract_tests", []),
            dispatch=(lambda kind, text: dispatch(kind, text)) if dispatch else None,
            merge_fn=merge_fn,
            test_timeout=hub_cfg.get("audit", {}).get("test_timeout", 600),
        )
        # 错误 hook / 自修复：规则引擎默认可用；证据与回滚护栏由 Gateway 注入
        hook_cfg = hub_cfg.get("error_hooks", {}) or {}
        try:
            self.error_engine = ErrorHookEngine(
                hook_cfg.get("path", "runtime_memory/architect/error_hooks.json"))
        except Exception:
            logger.exception("ErrorHookEngine 初始化失败，错误唤醒降级为旧逻辑")
            self.error_engine = None
        self._error_evidence_fn = None
        self._error_status_fn = None
        self._repair_guard = None
        self._repair_cfg = hub_cfg.get("repair", {}) or {}
        # 安全重启（RequestRestart 工具 → Gateway 装配期注入的钩子）
        self._restart_hook = None

    # ------------------------------------------------------------------
    # 遥测入口（TelemetryChannel 调用）+ 逻辑错误自动唤醒（触发器 ②）
    # ------------------------------------------------------------------

    def ingest_event(self, event_type: str, payload: dict | None = None):
        self.digest.ingest(event_type, payload)
        payload = payload or {}
        if event_type == "agent.error":
            # 统一错误记录（带 fingerprint/id）→ 规则引擎；旧 payload 回落兼容路径
            if payload.get("fingerprint") and payload.get("id"):
                self.ingest_error(dict(payload))
                return
            msg = str(payload.get("message", ""))
            if classify_error(msg) == "logic":
                logger.warning("逻辑错误触发 Architect 守护唤醒: %s", msg[:200])
                self.request_wake(WakeRequest(
                    reason=REASON_FATAL, kind=KIND_GUARDIAN, priority=0,
                    context={"trigger": "logic_error", "error": msg[:500]},
                ))

    # ------------------------------------------------------------------
    # 错误 hook → 唤醒 / 自修复
    # ------------------------------------------------------------------

    def set_error_hooks(self, engine=None, evidence_fn=None, status_fn=None,
                        repair_guard=None) -> None:
        """Gateway 装配期注入：规则引擎（可替换）/证据读取/状态回写/修复护栏。"""
        if engine is not None:
            self.error_engine = engine
        self._error_evidence_fn = evidence_fn
        self._error_status_fn = status_fn
        self._repair_guard = repair_guard
        logger.info("error hooks wired: engine=%s evidence=%s status=%s repair=%s",
                    bool(self.error_engine), bool(evidence_fn), bool(status_fn),
                    bool(repair_guard))

    def _error_status(self, error_id: str, **fields) -> None:
        if not self._error_status_fn or not error_id:
            return
        try:
            self._error_status_fn(error_id, **fields)
        except Exception:
            logger.warning("error status 回写失败: %s", fields, exc_info=True)

    def _error_context(self, record: dict, rule: dict) -> dict:
        evidence = {}
        if self._error_evidence_fn is not None:
            try:
                evidence = self._error_evidence_fn(record) or {}
            except Exception:
                logger.warning("读取错误证据失败", exc_info=True)
        ctx = {
            "trigger": "error_hook",
            "error_id": record.get("id", ""),
            "fingerprint": record.get("fingerprint", ""),
            "scope": record.get("scope", ""),
            "severity": record.get("severity", ""),
            "blocking": record.get("blocking", ""),
            "cause": record.get("cause", ""),
            "message": str(record.get("message", ""))[:4000],
            "traceback": str(record.get("traceback", ""))[:12000],
            "rule": rule.get("id", ""),
        }
        for key, value in (evidence or {}).items():
            ctx[f"evidence.{key}"] = str(value)[:8000]
        prompt_extra = str((rule.get("action") or {}).get("prompt_extra") or "")
        ctx["修复要求"] = (
            "报错，请注意观察并修复。注意！你的修复是直接改变原始代码，"
            "你应该保证你自己的正常运行。先定位最小根因，不要顺手重构；"
            "修改后必须运行相关契约测试与自检；如果修复会让自身无法启动，"
            "必须回滚并报告原因。"
            + (f"\n规则附加要求：{prompt_extra}" if prompt_extra else "")
        )
        return ctx

    def ingest_error(self, record: dict) -> bool:
        """统一错误 → hook 匹配 → 唤醒（成功受理返回 True）。"""
        engine = self.error_engine
        if engine is None or not isinstance(record, dict):
            return False
        try:
            match = engine.evaluate(record)
        except Exception:
            logger.exception("错误 hook 匹配失败")
            return False
        if not match:
            return False
        rule = match["rule"]
        action = rule.get("action") or {}
        wants_repair = bool(action.get("repair", True))
        # 没有修复护栏时不允许直接改生产代码，降级为守护分析
        if wants_repair and self._repair_guard is None:
            wants_repair = False
        request = WakeRequest(
            reason=REASON_ERROR_HOOK,
            kind=KIND_REPAIR if wants_repair else KIND_GUARDIAN,
            priority=0,
            context=self._error_context(record, rule),
        )
        result = self.request_wake(request, background=True)
        status = result.get("status")
        if status in ("dispatched", "started"):
            engine.mark_wake(str(record.get("fingerprint") or ""), str(rule.get("id") or ""))
            self._error_status(
                str(record.get("id") or ""),
                wake_status="pending_repair" if wants_repair else "notified",
                status="woken",
                attempts=int((record.get("attempts") or 0)) + 1,
            )
            logger.warning("错误 hook 命中 %s → Arch 已唤醒（repair=%s, id=%s）",
                           rule.get("id"), wants_repair, record.get("id"))
            return True
        logger.info("错误 hook 命中但未唤醒（%s）: %s", status, rule.get("id"))
        return False

    def _run_repair(self, request: WakeRequest, task_text: str) -> dict:
        """错误修复派发：快照 → Arch 直接改生产代码 → 自检 → 失败回滚。"""
        ctx = request.context or {}
        error_id = str(ctx.get("error_id") or "")
        guard = self._repair_guard
        if guard is None:
            return {"type": "error", "error": "repair guard 未接线"}
        snapshot = {}
        try:
            snapshot = guard.snapshot()
        except Exception as e:
            logger.exception("修复前快照失败，拒绝修复")
            self._error_status(error_id, wake_status="snapshot_failed",
                               status="open", attempts=int(ctx.get("attempts") or 0))
            return {"type": "error", "error": f"snapshot failed: {e}"}
        if not snapshot.get("ok"):
            self._error_status(error_id, wake_status="snapshot_failed", status="open")
            return {"type": "error", "error": snapshot.get("error", "snapshot failed")}

        self._error_status(error_id, wake_status="repairing")
        try:
            result = self._dispatch(KIND_REPAIR, task_text) if self._dispatch else None
        except Exception as e:
            logger.exception("Architect 修复派发异常")
            result = {"type": "error", "error": f"{type(e).__name__}: {e}"}
        verify = {}
        try:
            verify = guard.verify()
        except Exception as e:
            verify = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        failed = (not verify.get("ok")) or (
            isinstance(result, dict) and result.get("status") == "error") or (
            isinstance(result, dict) and result.get("type") == "error") or result is None
        if not failed:
            try:
                guard.finish(snapshot)
            except Exception:
                logger.warning("RepairGuard 快照清理失败", exc_info=True)
            self._error_status(error_id, wake_status="repair_done", status="resolved")
            return result if isinstance(result, dict) else {"status": "success"}
        restore = {}
        try:
            restore = guard.restore(snapshot)
        except Exception as e:
            restore = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._error_status(error_id, wake_status="repair_failed", status="open",
                           extra={"verify": verify, "restore": restore})
        self.notify_user(
            f"⚠ Architect 自动修复失败，已尝试回滚（{error_id}）："
            f"{verify.get('error') or '自检未通过'} / 回滚={restore.get('ok')}")
        return {"type": "error", "error": "repair failed and rolled back",
                "verify": verify, "restore": restore}

    def _run_explore(self, request: WakeRequest, task_text: str) -> dict:
        """bootstrap 初始探索：Arch 探索 → Verify 验收闭环（explore_flow）。

        拿结构化 request.context（identity_path/gaps/initial_exploration 等），
        不解析 task_text 字符串。explore_flow 惰性 import SubAgent，独立部署时
        ImportError 降级为 error 报告（不崩派发线程）。
        """
        try:
            from Architect.explore_flow import run_explore_with_verify
        except Exception as exc:
            logger.warning("explore_flow 不可用（独立部署？）: %s", exc)
            return {"type": "error", "error": f"explore flow unavailable: {exc}"}
        context = dict(request.context or {})
        bootstrap_cfg = self._hub_cfg.get("bootstrap", {}) or {}
        context.setdefault("explore_max_rounds",
                           bootstrap_cfg.get("explore_max_rounds", 3))
        return run_explore_with_verify(context)

    def render_digest(self) -> str:
        return self.digest.render_text()

    # ------------------------------------------------------------------
    # Hero 桥（Architect ↔ 主智能体/用户 双向沟通）
    # ------------------------------------------------------------------

    def set_hero_bridge(self, notify_user=None, message_hero=None):
        """Gateway 装配期注入对外通道。None 的通道保持未接线（降级为仅日志）。"""
        self._notify_user = notify_user
        self._message_hero = message_hero
        logger.info("hero bridge wired: notify_user=%s message_hero=%s",
                    bool(notify_user), bool(message_hero))

    def set_restart_hook(self, restart_fn=None):
        """Gateway 装配期注入安全重启钩子 fn(reason) -> dict。None=未接线。"""
        self._restart_hook = restart_fn
        logger.info("restart hook wired: %s", bool(restart_fn))

    def request_restart(self, reason: str = "") -> dict:
        """请求重构网（只调度，不执行 restart 命令）。

        Returns:
            {"status": "scheduled"|"restart_required"|"unwired"|"error", ...}
            restart_required：有 supervisor 时钩子只做优雅退出，由 supervisor 拉起。
        """
        if self._restart_hook is None:
            logger.warning("request_restart: 未接线（独立部署？），需人工重启")
            return {"status": "unwired", "reason": reason,
                    "note": "未注入重启钩子；请人工重启网关"}
        try:
            result = self._restart_hook(reason) or {}
            if "status" not in result:
                result["status"] = "scheduled"
            return result
        except Exception as exc:
            logger.warning("request_restart failed: %s", exc)
            return {"status": "error", "reason": reason,
                    "error": f"{type(exc).__name__}: {exc}"}

    def notify_user(self, text: str) -> bool:
        """Architect → 用户：推送报告。未接线返回 False（调用方走日志）。"""
        if self._notify_user is None:
            logger.info("notify_user 未接线，报告落日志: %s", str(text)[:200])
            return False
        try:
            self._notify_user(str(text))
            return True
        except Exception as exc:
            logger.warning("notify_user failed: %s", exc)
            return False

    def message_hero(self, text: str) -> bool:
        """Architect → 主智能体：递消息进 Hero 的会话队列（下一轮处理）。"""
        if self._message_hero is None:
            logger.info("message_hero 未接线，消息落日志: %s", str(text)[:200])
            return False
        try:
            self._message_hero(str(text))
            return True
        except Exception as exc:
            logger.warning("message_hero failed: %s", exc)
            return False

    def _push_report(self, kind: str, result):
        """后台任务完成后把报告摘要推给用户（report_push 配置可关）。"""
        if not self._report_push:
            return
        content = ""
        if isinstance(result, dict):
            content = str(result.get("content") or result.get("error") or "")
        if not content:
            content = str(result or "(无输出)")
        self.notify_user(f"[Architect {kind} 报告]\n{content[:800]}")

    # ------------------------------------------------------------------
    # 唤醒（push 式）+ 拉式源调度（scheduler 守护线程）
    # ------------------------------------------------------------------

    def register_wake_source(self, source):
        """注册拉式唤醒源（GitUpdateSource 等），由调度循环周期 poll。"""
        self._sources.append(source)
        logger.info("wake source registered: %s", getattr(source, "name", source))

    def start_scheduler(self):
        """启动调度循环（守护线程，幂等）。

        线程必要性：定期轮询本质上需要一个定时器；线程 confined 在 hub 内，
        tick 间隔由 architect_hub.wake.scheduler_interval_seconds 控制。
        """
        if self._scheduler_started:
            return
        self._scheduler_started = True
        interval = float(self._hub_cfg.get("wake", {}).get(
            "scheduler_interval_seconds", 60) or 60)
        threading.Thread(target=self._scheduler_loop, args=(max(5.0, interval),),
                         daemon=True, name="architect-scheduler").start()
        logger.info("architect scheduler started (tick=%.0fs, sources=%d)",
                    interval, len(self._sources))

    def _scheduler_loop(self, interval: float):
        while True:
            time.sleep(interval)
            for source in list(self._sources):
                try:
                    req = source.poll()
                except Exception as exc:
                    logger.warning("wake source %s poll failed: %s",
                                   getattr(source, "name", source), exc)
                    continue
                if req is not None:
                    self.request_wake(req)

    def request_wake(self, request: WakeRequest, background: bool = True) -> dict:
        """唤醒 Architect。冷却内的重复请求被合并跳过；派发串行（一次一个）。

        background=True（默认）：派发放守护线程异步执行，调用方（turn 线程/
        看门狗）立即返回；background=False：同步等结果（测试与 ConsultArchitect
        的 wait 模式用）。
        """
        now = time.time()
        cooldown = float(self._cooldowns.get(request.reason, 0) or 0)
        with self._wake_lock:
            last = self._last_wake_by_reason.get(request.reason, 0)
            if cooldown > 0 and now - last < cooldown:
                logger.info("wake %s 冷却中（%.0fs < %.0fs），跳过",
                            request.reason, now - last, cooldown)
                return {"status": "cooldown_skip", "reason": request.reason}
            self._last_wake_by_reason[request.reason] = now
            self._wake_history.append((now, request.reason, request.kind))

        if self._dispatch is None:
            self._pending.append(request)
            logger.warning("wake %s/%s：dispatch 未接线，进 pending 队列（%d）",
                           request.reason, request.kind, len(self._pending))
            return {"status": "pending", "reason": request.reason}

        # code_update 走更新四态流（门控：足够多不同 + 合适的时机），
        # 不进通用派发；受理后 ack 源（否则源下个周期重报）
        if request.reason == REASON_CODE_UPDATE:
            result = self.start_update(source="auto",
                                       context=dict(request.context))
            return {"status": result.get("status", "started"),
                    "reason": request.reason, "update": result}

        if not self._dispatch_lock.acquire(blocking=False):
            logger.info("wake %s/%s：Architect 忙，跳过", request.reason, request.kind)
            return {"status": "busy", "reason": request.reason}

        task_text = self._build_task_text(request)
        logger.info("wake %s/%s：派发 Architect", request.reason, request.kind)

        def _run():
            try:
                if request.reason == REASON_ERROR_HOOK and request.kind == KIND_REPAIR:
                    return self._run_repair(request, task_text)
                if request.kind == KIND_EXPLORE:
                    return self._run_explore(request, task_text)
                return self._dispatch(request.kind, task_text)
            finally:
                self._dispatch_lock.release()

        if background:
            def _run_and_report():
                result = _run()
                self._push_report(request.kind, result)

            threading.Thread(target=_run_and_report, daemon=True,
                             name=f"architect-dispatch-{request.kind}").start()
            return {"status": "dispatched", "reason": request.reason,
                    "background": True}
        return {"status": "dispatched", "reason": request.reason,
                "result": _run()}

    @property
    def pending(self) -> list:
        return list(self._pending)

    # ------------------------------------------------------------------
    # 版本更新（四态状态机）：门控 + 启动 + 状态查询
    # ------------------------------------------------------------------

    def set_runtime_hooks(self, idle_probe=None, confirm_fn=None,
                          on_update_done=None):
        """Gateway 装配期注入：空闲探测 / 确认链 / 更新结束回调。"""
        self._idle_probe = idle_probe
        self._confirm_fn = confirm_fn
        self._on_update_done = on_update_done
        logger.info("update hooks wired: idle_probe=%s confirm=%s kick=%s",
                    bool(idle_probe), bool(confirm_fn), bool(on_update_done))

    def is_update_active(self) -> bool:
        flow = self._update_flow
        return bool(flow and flow.active)

    def update_state(self) -> str:
        return self._update_flow.state if self._update_flow else "idle"

    def _is_suitable_moment(self) -> bool:
        """合适的时机：主会话空闲 + 用户安静期（最近无用户消息）。"""
        if self._idle_probe is not None:
            try:
                if not self._idle_probe():
                    return False
            except Exception:
                return False
        quiet_s = float(self._hub_cfg.get("upgrade", {}).get(
            "quiet_seconds", 300) or 300)
        last_user = self.digest.snapshot()["last_by_type"].get("user.message")
        if last_user and time.time() - last_user < quiet_s:
            return False
        return True

    def start_update(self, source: str = "manual", context: dict | None = None,
                     force: bool = False) -> dict:
        """启动更新四态流。force=False（auto 路径）需过门控；force=True
        （用户指令）直接启动。"""
        from Architect.update_flow import UpdateFlow
        if self.is_update_active():
            return {"status": "busy", "state": self.update_state()}
        ctx = dict(context or {})
        if not force:
            min_commits = int(self._hub_cfg.get("upgrade", {}).get(
                "min_commits", 3) or 3)
            try:
                behind = int(ctx.get("behind") or 0)
            except (TypeError, ValueError):
                behind = 0
            if behind < min_commits:
                logger.info("update 门控：落后 %d < min_commits %d，不启动",
                            behind, min_commits)
                return {"status": "below_threshold", "behind": behind}
            if not self._is_suitable_moment():
                logger.info("update 门控：主会话忙或用户活跃中，推迟")
                return {"status": "deferred"}
        flow = UpdateFlow(self, source=source, context=ctx)
        self._update_flow = flow
        flow.start()
        # 通知源：该 sha 已被受理（受理前源会持续重报）
        sha = str(ctx.get("remote_head", ""))
        if sha:
            for s in self._sources:
                if hasattr(s, "note_accepted"):
                    s.note_accepted(sha)
        logger.info("update flow started (source=%s)", source)
        return {"status": "started", "source": source}

    # ------------------------------------------------------------------
    # 版本升级（触发器 ③）：staging 拉取 → 审计 → 合并
    # ------------------------------------------------------------------

    def set_proxy_provider(self, provider) -> None:
        """注入网络代理提供器，供 GitUpdateSource 与 UpdateFlow 共用。"""
        self._proxy_provider = provider

    def request_upgrade(self, note: str = "") -> dict:
        """收到升级指令：fetch 最新代码到 staging，过审计管线，PASS 才合并。

        merge_fn 未接线时只出审计报告不动生产目录（安全默认）。
        """
        from Architect.upgrader import Upgrader
        up_cfg = self._hub_cfg.get("upgrade", {}) if isinstance(self._hub_cfg, dict) else {}
        up = Upgrader(
            repo_dir=up_cfg.get("repo_dir", "."),
            staging_dir=up_cfg.get("staging_dir", "runtime_memory/upgrade_staging"),
            remote=up_cfg.get("remote", "origin"),
            branch=up_cfg.get("branch", "master"),
            proxy_provider=self._proxy_provider,
        )
        fetch = up.fetch_staging()
        if not fetch.get("ok"):
            logger.warning("upgrade: staging 拉取失败: %s", fetch.get("error"))
            return {"verdict": "FAIL", "stage": "fetch", "error": fetch.get("error")}
        diff = up.diff_summary()
        return self.request_audit(fetch["staging_dir"], diff_text=diff,
                                  note=note or "版本升级")

    # ------------------------------------------------------------------
    # 审计（代码更新把关）
    # ------------------------------------------------------------------

    def request_audit(self, staging_dir, diff_text: str = "",
                      note: str = "") -> dict:
        return self.audit_pipeline.audit(staging_dir, diff_text, note)

    # ------------------------------------------------------------------

    @staticmethod
    def _render_upgrade_report_simple(report: dict) -> str:
        """审计报告的精简文本（UpdateFlow 用）：判定 + 契约测试结果。"""
        report = report or {}
        lines = [f"审计判定: {report.get('verdict', '?')}"]
        tests = report.get("tests") or []
        if tests:
            lines.append("契约测试: " + " / ".join(
                f"{str(t.get('test', '?')).split('/')[-1]}"
                f"{'✓' if t.get('ok') else '✗'}" for t in tests))
        llm = report.get("llm_audit") or {}
        if not llm.get("skipped") and llm.get("content_tail"):
            lines.append(f"Architect 走读摘要: {llm['content_tail'][-300:]}")
        return "\n".join(lines)

    @staticmethod
    def _render_upgrade_report(request: WakeRequest, report: dict) -> str:
        """升级审计的用户推送版：精简（commit 清单 + 测试结果 + 处置）。"""
        ctx = request.context or {}
        lines = [
            f"代码更新审计：{report.get('verdict')}"
            f"（{ctx.get('ref', '?')} 新提交 {ctx.get('behind', '?')} 个）",
        ]
        if ctx.get("new_commits"):
            lines.append(str(ctx["new_commits"]))
        tests = report.get("tests") or []
        if tests:
            lines.append("契约测试: " + " / ".join(
                f"{str(t.get('test', '?')).split('/')[-1]}"
                f"{'✓' if t.get('ok') else '✗'}" for t in tests))
        if report.get("merged"):
            lines.append("处置：已合并到生产目录。")
        elif report.get("verdict") == "PASS":
            lines.append("处置：PASS 但 merge 未接线，仅审计未合并。")
        else:
            lines.append("处置：已隔离，生产未动。")
        return "\n".join(lines)

    def _build_task_text(self, request: WakeRequest) -> str:
        ctx = "\n".join(f"- {k}: {v}" for k, v in (request.context or {}).items())
        return (
            f"【唤醒:{request.reason}】\n\n{self.render_digest()}\n\n"
            f"上下文:\n{ctx or '(无)'}"
        )

    def status(self) -> dict:
        return {
            "digest": self.digest.snapshot(),
            "dispatch_wired": self._dispatch is not None,
            "pending": len(self._pending),
            "wake_history": list(self._wake_history)[-10:],
        }


# ----------------------------------------------------------------------
# Goharu 内默认派发：SubAgent(architect) 同步执行（惰性 import，独立部署降级）
# ----------------------------------------------------------------------

def default_dispatch(kind: str, task_text: str):
    try:
        from Agent.SubAgent import SubAgent
        from Tools.builtin.agent_delegate import notify_output
        from Tools.registry import registry
        from Tools.subagent_config import get_subagent_llm
    except ImportError as exc:
        logger.warning("architect dispatch 不可用（独立部署？）: %s", exc)
        return None

    def _notify(payload):
        try:
            notify_output(payload)
        except Exception:
            logger.warning("architect 子代理事件发送失败", exc_info=True)

    agent_id = f"architect_hub_{uuid.uuid4().hex[:8]}"
    _notify({
        "agent_id": agent_id,
        "agent_type": "architect",
        "task": task_text,
        "kind": "started",
        "level": "info",
        "message": f"启动 architect agent [{agent_id}]",
    })
    result = None
    try:
        subagent = SubAgent(
            agent_type="architect", task=task_text, agent_id=agent_id,
            tools_registry=registry, llm=get_subagent_llm(),
            output_callback=notify_output,
        )
        result = subagent.execute()
        return result
    finally:
        status = result.get("status") if isinstance(result, dict) else "error"
        if status not in ("success", "partial", "error"):
            status = "error"
        _notify({
            "agent_id": agent_id,
            "agent_type": "architect",
            "task": task_text,
            "kind": "finished",
            "level": "error" if status == "error" else "info",
            "message": f"architect agent [{agent_id}] 完成",
            "status": status,
        })


# ----------------------------------------------------------------------
# 进程级默认单例（TelemetryChannel 插件经 config 装配时使用）
# ----------------------------------------------------------------------

_default_hub = None
_default_lock = threading.Lock()


def create_architect_hub(config: dict | None = None, dispatch=None,
                         merge_fn=None) -> ArchitectHub:
    return ArchitectHub(config=config, dispatch=dispatch, merge_fn=merge_fn)


def get_default_hub() -> ArchitectHub:
    """进程级懒加载单例。dispatch 惰性接 default_dispatch（SubAgent 可用时）。"""
    global _default_hub
    if _default_hub is None:
        with _default_lock:
            if _default_hub is None:
                try:
                    from configurationLoader import config as _gconfig
                    cfg = dict(_gconfig.data) if hasattr(_gconfig, "data") else {}
                except Exception:
                    cfg = {}
                _default_hub = ArchitectHub(config=cfg, dispatch=default_dispatch)
                logger.info("architect hub: default instance created")
    return _default_hub
