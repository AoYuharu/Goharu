"""LLM 访问层独立接口测试 —— 外部 harness 视角。

用注册的假 provider（无网络）驱动真实 LLMCore 引擎，验证 Agent.llm（create_llm）：
- config dict 直注创建独立实例（不碰 Goharu 全局单例）
- 纯文本调用返回 str；带 tools 返回 NormalizedResponse
- 可重试错误 → 指数退避重试 → on_retry 事件 → 成功
- 重试耗尽 → 抛错
- usage / thinking 统计读取
- reload 热切换配置
- 与 AgentLoop 的连接：create_agent(llm_config=...) 便利接线

运行：python test_llm_standalone.py
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Agent import providers
from Agent.providers.base import LLMProvider, NormalizedResponse, ProviderRequest

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


# ---------------------------------------------------------------------------
# 假 provider：注册进 providers 表，离线驱动真实 LLMCore
# ---------------------------------------------------------------------------
class FakeProvider(LLMProvider):
    name = "fake"
    supports_native_tools = True
    state = {"calls": 0, "fail_times": 0}

    def create_client(self, llm_config):
        return {"client_for": llm_config.get("model")}

    def probe(self, client, llm_config):
        return None

    def build_request(self, client, messages, params, tools=None, tool_choice=None):
        state = self.state

        def call():
            state["calls"] += 1
            if state["calls"] <= state["fail_times"]:
                raise RuntimeError("HTTP 500 server error")  # 可重试错误
            return {"raw": True}

        return ProviderRequest(
            wire={"messages": messages, "tools": tools}, call=call,
        )

    def normalize_response(self, raw):
        return NormalizedResponse(
            content=[{"type": "thinking", "thinking": "思考一下"},
                     {"type": "text", "text": "你好，我是假模型"}],
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5,
                   "cache_read_input_tokens": 8, "cache_creation_input_tokens": 0},
        )


providers._PROVIDERS["fake"] = FakeProvider

FAKE_CONFIG = {
    "provider": "fake",
    "model": "fake-model-1",
    "max_tokens": 100,
    "temperature": 0.5,
    "max_retries": 2,
    "api_timeout": 10,
}


def main():
    print("=" * 60)
    print("LLM 访问层独立接口测试（create_llm）")
    print("=" * 60)

    from Agent.llm import create_llm
    from Agent.LLMCore import LLMCore

    # ------------------------------------------------------------------
    # 1. 创建与单例隔离
    # ------------------------------------------------------------------
    print("\n[1] 创建与单例隔离")
    llm = create_llm(FAKE_CONFIG)
    check("create_llm 返回 LLMClient", llm is not None)
    check("provider 解析正确", llm.provider_name == "fake")
    check("supports_native_tools", llm.supports_native_tools is True)

    global_core = LLMCore()  # Goharu 单例（读 config.yaml）
    check("独立实例不碰全局单例", llm._core is not global_core)
    check("全局单例仍是单例", LLMCore() is global_core)

    # ------------------------------------------------------------------
    # 2. 纯文本调用（不带 tools 返回 str）
    # ------------------------------------------------------------------
    print("\n[2] 纯文本调用")
    FakeProvider.state.update(calls=0, fail_times=0)
    text = llm.query([{"role": "user", "content": "你好"}])
    check("纯文本返回 str", text == "你好，我是假模型", repr(text))
    check("usage 已记录", llm.get_last_usage().get("cache_read_input_tokens") == 8)
    check("thinking 已记录", llm.get_last_thinking() == "思考一下")

    # ------------------------------------------------------------------
    # 3. 带 tools 调用（返回 NormalizedResponse）
    # ------------------------------------------------------------------
    print("\n[3] 带 tools 调用")
    resp = llm.query(
        [{"role": "user", "content": "调用工具"}],
        tools=[{"name": "t1", "description": "d", "input_schema": {}}],
    )
    check("返回 NormalizedResponse", isinstance(resp, NormalizedResponse))
    check("content 块正确", resp.content[-1]["text"] == "你好，我是假模型")
    check("stop_reason 正确", resp.stop_reason == "end_turn")
    check("usage 携带缓存细分", resp.usage["cache_read_input_tokens"] == 8)

    # ------------------------------------------------------------------
    # 4. 可重试错误 → 退避重试 → 成功
    # ------------------------------------------------------------------
    print("\n[4] 重试退避")
    FakeProvider.state.update(calls=0, fail_times=1)  # 第一次失败
    retry_events = []
    text = llm.query([{"role": "user", "content": "hi"}],
                     on_retry=lambda a, m, b, p: retry_events.append((a, m, b, p)))
    check("重试后成功", text == "你好，我是假模型")
    check("on_retry 事件含 backoff/requesting 两阶段",
          ("backoff" in [e[3] for e in retry_events])
          and ("requesting" in [e[3] for e in retry_events]),
          str(retry_events))
    check("重试次数=1", retry_events[0][0] == 1 and retry_events[0][1] == 2)

    # ------------------------------------------------------------------
    # 5. 重试耗尽 → 抛错
    # ------------------------------------------------------------------
    print("\n[5] 重试耗尽")
    FakeProvider.state.update(calls=0, fail_times=99)  # 永远失败
    try:
        llm.query([{"role": "user", "content": "hi"}])
        check("重试耗尽抛错", False, "未抛错")
    except RuntimeError as e:
        check("重试耗尽抛错", "500" in str(e), str(e))
    FakeProvider.state.update(fail_times=0)

    # ------------------------------------------------------------------
    # 6. reload 热切换
    # ------------------------------------------------------------------
    print("\n[6] reload")
    ok, err = llm.reload({**FAKE_CONFIG, "model": "fake-model-2"})
    check("reload 成功", ok is True and err is None, str(err))
    check("reload 后模型已切换", llm._core.llm_config.get("model") == "fake-model-2")

    # ------------------------------------------------------------------
    # 7. 与 AgentLoop 的连接：create_agent(llm_config=...)
    # ------------------------------------------------------------------
    print("\n[7] create_agent(llm_config=...) 接线")
    from Tools.api import create_tools, close_tools
    from Agent.api import create_agent

    tools = asyncio.run(create_tools(modules=["Tools.builtin.sleep_tool"]))
    FakeProvider.state.update(calls=0, fail_times=0)
    agent = asyncio.run(create_agent(
        tools=tools, llm_config={**FAKE_CONFIG, "model": "fake-model-1"},
    ))
    check("llm_config 自动建 LLMClient", agent.actor.core is None)  # 注入路径
    reply = asyncio.run(agent.run_turn("打个招呼"))
    check("run_turn 走通假模型", reply == "你好，我是假模型", reply[:100])
    asyncio.run(close_tools(tools))

    # ------------------------------------------------------------------
    # 8. 错误配置与边界
    # ------------------------------------------------------------------
    print("\n[8] 错误配置与边界")
    try:
        create_llm({})
        check("空配置 → ValueError", False, "未抛错")
    except ValueError:
        check("空配置 → ValueError", True)

    try:
        create_llm({"provider": "nonexistent_provider", "model": "x"})
        check("未知 provider → 抛错", False, "未抛错")
    except ValueError as e:
        check("未知 provider → 抛错", "nonexistent_provider" in str(e)
              or "Unsupported" in str(e), str(e)[:100])

    check("get_token_size 正整数", llm.get_token_size("hello world") > 0)

    ok8, err8 = llm.reload({"provider": "nonexistent_provider", "model": "x"})
    check("reload 失败返回 (False, err) 且保留旧配置",
          ok8 is False and err8 and llm.provider_name == "fake",
          f"ok={ok8} provider={llm.provider_name}")

    # 清理日志目录里本测试产生的请求日志由 LLMResponseLogger 自管，无需清理
    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
