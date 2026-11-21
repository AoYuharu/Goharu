"""Prompting 子系统"即拆即用"解耦契约测试。

在进程内安装 import 拦截器，屏蔽 Agent/、Tools/、Gateway/、configurationLoader，
模拟外部 harness 只携带 Core/ + Prompting/ 的部署形态，然后验证 facade 全功能：

- create_context_manager / build_messages / context_report
- L0 micro_compact / L1 truncate_history / L2 llm_compact（注入 FakeLLM）
- apply_result_budget（超限结果落盘 + 占位符）
- estimate_tokens
- PromptLoader 无全局配置时回退空配置；actor 文件加载跳过 env hint 不报错
- Goharu 特有注入（git_status / skills）在被屏蔽环境下优雅降级
- llm=None 且 Agent 不可得时，llm_compact 抛说明性 RuntimeError
- 全程不触发任何 Agent.*/Tools.* 模块加载（sys.modules 审计）

运行：python tests/test_prompting_decoupled.py
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

# ── import 拦截器：模拟宿主环境没有 Agent/Tools/Gateway/configurationLoader ──
BLOCKED_PREFIXES = ("Agent", "Tools", "Gateway", "configurationLoader")


class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED_PREFIXES:
            raise ImportError(f"blocked by decoupling test: {name}")
        return None


# 先剔除可能已被其他 import 带入的模块，再装拦截器
for mod in [m for m in sys.modules if m.split(".")[0] in BLOCKED_PREFIXES]:
    del sys.modules[mod]
sys.meta_path.insert(0, _Blocker())

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


# ── 1. facade 导入与基础组装 ─────────────────────────────────────────────
from Prompting.api import create_context_manager  # noqa: E402

cm = create_context_manager(system_prompt="你是一个助手。")

history = [
    {"role": "user", "content": "你好", "id": "m1", "timestamp": datetime.now().isoformat()},
    {"role": "assistant", "content": "你好，有什么可以帮你？", "id": "m2",
     "timestamp": datetime.now().isoformat()},
    {"role": "user", "content": "介绍下你自己", "id": "m3",
     "timestamp": datetime.now().isoformat()},
]
messages = cm.build_messages(history)
check(isinstance(messages, list) and len(messages) >= len(history),
      "build_messages 返回消息列表")
check(any(m.get("role") == "system" for m in messages), "system 块存在")

# 跨 turn 前缀逐字节稳定（缓存铁律的独立部署形态）
ctx_store: dict = {}
m1 = cm.build_messages(history, memory_contexts=ctx_store)
history2 = history + [{"role": "assistant", "content": "好的", "id": "m4",
                       "timestamp": datetime.now().isoformat()}]
m2 = cm.build_messages(history2, memory_contexts=ctx_store)
prefix1 = [b for m in m1 for b in (m["content"] if isinstance(m.get("content"), list) else [])]
prefix2 = [b for m in m2 for b in (m["content"] if isinstance(m.get("content"), list) else [])]
check(prefix1 == prefix2[: len(prefix1)], "跨 turn 历史渲染逐字节稳定")

# ── 2. 压缩阶梯 ──────────────────────────────────────────────────────────
# L1：孤儿 tool_result 截断安全
hist = [
    {"role": "user", "content": "q1"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
    {"role": "user", "content": "q2"},
]
trimmed = cm.truncate_history(hist, max_messages=2)
first = trimmed[0]["content"]
check(not (isinstance(first, list) and any(b.get("type") == "tool_result" for b in first)),
      "L1 截断丢弃孤儿 tool_result")
check(cm.truncate_history(hist, max_messages=0) == hist, "L1 max=0 不截断")

# L0：过期长工具结果折叠
old_ts = (datetime.now() - timedelta(hours=2)).isoformat()
expired = [
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                  "content": "x" * 500}], "timestamp": old_ts}
    for i in range(8)
]
folded, n_folded = cm.micro_compact(expired)
check(n_folded > 0 and folded is not expired, "L0 过期工具结果被折叠")

# L2：注入 FakeLLM 的摘要压缩（不触 Agent）
class FakeLLM:
    def query(self, msgs, **kw):
        return "[上下文摘要] 假摘要"


long_hist = [{"role": "user", "content": "内容 " * 5000, "id": f"h{i}",
              "timestamp": datetime.now().isoformat()} for i in range(4)]
cm2 = create_context_manager(system_prompt="sys", llm=FakeLLM(), compact_threshold_tokens=100)
check(cm2.should_llm_compact(long_hist), "L2 阈值判定触发")
summary = cm2.llm_compact(long_hist)
check(summary.startswith("[上下文摘要]"), "L2 注入 llm 摘要成功（无 Agent 依赖）")

# L2：未注入 llm 且 Agent 被屏蔽 → 说明性 RuntimeError
try:
    cm.llm_compact(long_hist)
    check(False, "L2 无 llm 应报错")
except RuntimeError as e:
    check("llm=" in str(e), "L2 无 llm 报说明性 RuntimeError")

# ── 3. 工具结果预算 ──────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    big = [{"tool_name": "run_cmd", "result_text": "y" * 90000}]
    applied = cm.apply_result_budget(big, cache_dir=tmp, max_single_tokens=100)
    check(applied and "[TRUNCATED]" in big[0]["result_text"], "预算超限替换占位符")
    check(list(Path(tmp).glob("tool_run_cmd_*.txt")), "完整结果已落盘")

# ── 4. token 估算 ────────────────────────────────────────────────────────
est = cm.estimate_tokens("hello 你好")
check(isinstance(est, int) and est > 0, "estimate_tokens 可用")

# ── 5. PromptLoader 无全局配置降级 + actor 文件加载无 env hint 崩溃 ────────
from Prompting.PromptLoader import PromptLoader  # noqa: E402

loader = PromptLoader()
check(loader.load_system_sections("actor") == [], "无配置时 load_system_sections 返回空")

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "base.md"
    p.write_text("你是测试助手。", encoding="utf-8")
    from Prompting.api import _PipelineConfig  # noqa: E402
    file_loader = PromptLoader(_PipelineConfig(
        {"prompt": {"system": {"actor": ["base.md"]}}}, base_dir=tmp))
    sections = file_loader.load_system_sections("actor")
    check(len(sections) == 1 and "测试助手" in sections[0].content,
          "actor 文件加载成功（env hint 被屏蔽时优雅跳过）")

# ── 6. Goharu 特有注入降级 ───────────────────────────────────────────────
from Prompting.PromptAssembler import PromptAssembler  # noqa: E402

asm = PromptAssembler(loader, include_git_status=True, include_skills=True,
                      include_persona=False)
doc = asm.build_actor_document(history)
check(doc is not None, "git_status/skills 被屏蔽时组装不崩溃")

# ── 7. sys.modules 审计：全程不得加载任何被屏蔽模块 ──────────────────────
leaked = [m for m in sys.modules if m.split(".")[0] in BLOCKED_PREFIXES]
check(not leaked, f"未加载 Agent/Tools/Gateway/configurationLoader（泄漏: {leaked}）")

# ── 汇总 ─────────────────────────────────────────────────────────────────
failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} "
      f"({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
