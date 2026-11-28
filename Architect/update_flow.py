"""UpdateFlow —— Goharu 版本更新的四态状态机。

    IDLE → GATHERING → AWAITING_CONFIRM → UPDATING → DONE
                                                      （outcome: success / failed / cancelled）

  ① GATHERING        开始更新：staging 拉取 + 审计 + 更新计划
  ② AWAITING_CONFIRM 计划就绪，经确认链等用户拍板（超时自动取消）
  ③ UPDATING         合并进生产目录（merge_fn 未接线则只出报告）
  ④ DONE             更新完毕，解锁主会话

不可干扰契约：中段三态期间主会话 enqueue 挂起一切新消息（不打断、不启动
新轮）；确认/作答走 channel 拦截层不受影响；DONE 时 hub._on_update_done
回调解锁。本对象由 ArchitectHub 持有驱动，全程不抛异常穿透。
"""

import logging
import threading

logger = logging.getLogger(__name__)


class UpdateFlow:
    IDLE = "idle"
    GATHERING = "gathering"
    AWAITING_CONFIRM = "awaiting_confirm"
    UPDATING = "updating"
    DONE = "done"

    def __init__(self, hub, source: str = "manual", context: dict | None = None):
        self._hub = hub
        self.source = str(source)
        self.context = dict(context or {})
        self.state = self.IDLE
        self.outcome = None           # success / failed / cancelled
        self.error = None
        self.report = None            # 审计报告 dict

    @property
    def active(self) -> bool:
        return self.state in (self.GATHERING, self.AWAITING_CONFIRM,
                              self.UPDATING)

    # ------------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._run, daemon=True,
                         name=f"update-flow-{self.source}").start()

    def _set(self, state):
        self.state = state
        logger.info("update flow [%s] → %s", self.source, state)

    def _run(self):
        hub = self._hub
        try:
            # ① 搜集更新信息
            self._set(self.GATHERING)
            hub.notify_user("🔄 开始更新：正在搜集更新信息（拉取 staging + 审计）…")
            from Architect.upgrader import Upgrader
            up_cfg = hub._hub_cfg.get("upgrade", {})
            up = Upgrader(
                repo_dir=up_cfg.get("repo_dir", "."),
                staging_dir=up_cfg.get("staging_dir",
                                       "runtime_memory/upgrade_staging"),
                remote=up_cfg.get("remote", "origin"),
                branch=up_cfg.get("branch", "master"),
                proxy_provider=getattr(hub, "_proxy_provider", None),
            )
            fetch = up.fetch_staging()
            if not fetch.get("ok"):
                self.error = f"staging 拉取失败: {fetch.get('error')}"
                hub.notify_user(f"❌ 更新中止：{self.error}")
                return self._finish("failed")
            diff = up.diff_summary()
            self.report = hub.audit_pipeline.audit(
                fetch["staging_dir"], diff_text=diff,
                note=self.context.get("ref", "版本更新"))
            if self.report.get("verdict") != "PASS":
                hub.notify_user(
                    f"❌ 更新审计未通过，已隔离 staging，生产未动。\n"
                    f"{hub._render_upgrade_report_simple(self.report)}")
                up.cleanup()
                return self._finish("failed")

            # ② 等待确认
            self._set(self.AWAITING_CONFIRM)
            plan = self._render_plan()
            hub.notify_user(plan)
            timeout = int(up_cfg.get("confirm_timeout_seconds", 1800) or 1800)
            approved = bool(hub._confirm_fn and hub._confirm_fn(
                "应用 Goharu 更新（合并 staging → 生产）", plan, timeout))
            if not approved:
                hub.notify_user("⏹ 更新已取消（未确认或超时）。")
                up.cleanup()
                return self._finish("cancelled")

            # ③ 执行更新
            self._set(self.UPDATING)
            hub.notify_user("🚀 确认收到，开始对 Goharu 进行更新…")
            if hub._merge_fn is not None:
                merge_result = hub._merge_fn(fetch["staging_dir"])
                logger.info("update merge result: %s", merge_result)
                hub.notify_user("✅ 更新完毕：已合并到生产目录。"
                                "重启后生效（重启需人工确认）。")
            else:
                hub.notify_user("✅ 更新完毕：审计通过，但 merge_fn 未接线——"
                                "代码仍在 staging，生产未动（安全默认）。")
            up.cleanup()
            return self._finish("success")
        except Exception as exc:
            logger.exception("update flow crashed")
            self.error = str(exc)
            hub.notify_user(f"❌ 更新流程异常：{exc}")
            return self._finish("failed")

    def _render_plan(self) -> str:
        ctx = self.context
        lines = [
            "📋 更新信息搜集完毕，更新计划已写好，等待确认：",
            f"- 来源: {ctx.get('ref', '?')}（本地 {ctx.get('local_head', '?')}"
            f" → 远端 {ctx.get('remote_head', '?')}，落后 {ctx.get('behind', '?')} 个提交）",
        ]
        if ctx.get("new_commits"):
            lines.append(str(ctx["new_commits"]))
        lines.append(self._hub._render_upgrade_report_simple(self.report))
        lines.append("确认后进入更新阶段；期间新消息将挂起，不打断更新。")
        return "\n".join(lines)

    def _finish(self, outcome):
        self.outcome = outcome
        self._set(self.DONE)
        # 解锁主会话：挂起的消息开始处理
        if self._hub._on_update_done is not None:
            try:
                self._hub._on_update_done()
            except Exception as exc:
                logger.warning("on_update_done kick failed: %s", exc)
        return {"state": self.state, "outcome": outcome, "error": self.error}
