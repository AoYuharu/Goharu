"""AuditPipeline —— 外部更新（git push 等）进生产前的总体审计管线。

铁律：**生产目录只跑审计通过且可回滚的代码**。流程：

  1. 机械层：staging 目录里跑全量契约测试（subprocess，默认 sys.executable）
  2. 智能层：dispatch 已接线时，把 diff + 测试结果交给 Architect 子代理走读
     （不变量审查：facade 主权 / 缓存断点纪律 / 安全链）；未接线则跳过，
     机械层独立成判
  3. PASS 且注入了 merge_fn → 执行合并；FAIL 或 merge_fn 未接线 → 只出报告，
     staging 隔离不动

所有动作可注入（run_test / dispatch / merge_fn），契约测试用假实现驱动真实
管线。管线本身不 import 任何 Goharu 模块。
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditPipeline:
    def __init__(self, contract_tests=None, run_test=None, dispatch=None,
                 merge_fn=None, test_timeout: int = 600):
        self.contract_tests = list(contract_tests or [])
        self._run_test = run_test or self._default_run_test
        self._dispatch = dispatch
        self._merge_fn = merge_fn
        self.test_timeout = int(test_timeout or 600)

    # ------------------------------------------------------------------

    def audit(self, staging_dir, diff_text: str = "", note: str = "") -> dict:
        """对 staging_dir 里的代码做总体审计，返回报告 dict。"""
        staging = str(staging_dir)
        report = {
            "staging_dir": staging, "note": note,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tests": [], "llm_audit": {"skipped": True},
            "verdict": "FAIL", "merged": False,
        }

        # ── 1. 机械层：契约测试 ──
        if not Path(staging).is_dir():
            report["error"] = f"staging 目录不存在: {staging}"
            logger.warning("audit: %s", report["error"])
            return report
        for test_path in self.contract_tests:
            r = self._run_test(test_path, staging, self.test_timeout)
            report["tests"].append(r)
            logger.info("audit test %s -> %s", test_path,
                        "PASS" if r.get("ok") else "FAIL")
        tests_ok = bool(report["tests"]) and all(
            t.get("ok") for t in report["tests"])

        # ── 2. 智能层：Architect 走读（dispatch 接线后生效）──
        if self._dispatch is not None:
            task_text = self._build_audit_task(report, diff_text)
            try:
                llm_result = self._dispatch("audit", task_text) or {}
                content = str(llm_result.get("content", "") or "")
                verdict = "PASS" if "VERDICT: PASS" in content else (
                    "FAIL" if "VERDICT: FAIL" in content else "PARTIAL")
                report["llm_audit"] = {
                    "skipped": False, "verdict": verdict,
                    "content_tail": content[-2000:],
                }
            except Exception as exc:
                logger.warning("audit: llm walkthrough failed: %s", exc)
                report["llm_audit"] = {"skipped": False, "verdict": "FAIL",
                                       "error": str(exc)}
        else:
            logger.info("audit: dispatch 未接线，跳过 LLM 走读（机械层独立成判）")

        # ── 3. 判定与合并 ──
        llm = report["llm_audit"]
        llm_ok = llm.get("skipped") or llm.get("verdict") == "PASS"
        report["verdict"] = "PASS" if (tests_ok and llm_ok) else "FAIL"

        if report["verdict"] == "PASS" and self._merge_fn is not None:
            try:
                report["merge_result"] = self._merge_fn(staging)
                report["merged"] = True
                logger.info("audit: PASS，已合并 %s", staging)
            except Exception as exc:
                report["merged"] = False
                report["merge_error"] = str(exc)
                logger.warning("audit: 合并失败: %s", exc)
        elif report["verdict"] == "PASS":
            logger.info("audit: PASS，但 merge_fn 未接线，仅出报告")
        else:
            logger.warning("audit: FAIL，staging 隔离: %s", staging)
        return report

    # ------------------------------------------------------------------

    @staticmethod
    def _build_audit_task(report: dict, diff_text: str) -> str:
        tests = "\n".join(
            f"- {t['test']}: {'PASS' if t.get('ok') else 'FAIL'}"
            + (f"（尾部输出: {t.get('tail', '')}）" if not t.get("ok") else "")
            for t in report["tests"]
        )
        return (
            "【代码更新审计】staging 区有待合并更新，请做总体审计走读。\n\n"
            f"staging 目录: {report['staging_dir']}\n\n"
            f"契约测试结果（机械层已跑）:\n{tests}\n\n"
            f"变更 diff:\n{diff_text or '(未提供，请自行 git diff 查看)'}\n\n"
            "审计要点：facade 主权（跨模块只走 api/bridge）、prompt cache 断点"
            "纪律、工具安全链、配置兼容性（新键有无默认值）。\n"
            "以 VERDICT: PASS / FAIL / PARTIAL 结尾，FAIL 必须列出阻断项。"
        )

    @staticmethod
    def _default_run_test(test_path: str, cwd: str, timeout: int) -> dict:
        try:
            proc = subprocess.run(
                [sys.executable, test_path], cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return {"test": test_path, "ok": proc.returncode == 0,
                    "exit_code": proc.returncode, "tail": out[-500:]}
        except subprocess.TimeoutExpired:
            return {"test": test_path, "ok": False, "error": f"timeout {timeout}s"}
        except Exception as exc:
            return {"test": test_path, "ok": False, "error": str(exc)}
