"""LLM 超时与重试总时限测试 —— 离线可跑，不发网络请求。

验证：
(a) anthropic/openai create_client 产生的 client 带细粒度 httpx.Timeout，
    timeout.connect == connect_timeout（默认 15），read == api_timeout；
(b) generate() 在 provider.call 持续抛可重试错误时，总耗时被
    max_total_seconds 封顶（不再无界退避）。

运行：python tests/test_llm_timeout.py
"""

import os
import sys
import time
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


class AlwaysFailProvider(LLMProvider):
    name = "always_fail"
    state = {"calls": 0}

    def create_client(self, llm_config):
        return object()

    def probe(self, client, llm_config):
        return None

    def build_request(self, client, messages, params, tools=None, tool_choice=None):
        state = self.state

        def call():
            state["calls"] += 1
            raise ConnectionError("connection error: TLS handshake stalled")

        return ProviderRequest(wire={"messages": messages}, call=call)

    def normalize_response(self, raw):
        return NormalizedResponse(content=[{"type": "text", "text": "x"}])


providers._PROVIDERS["always_fail"] = AlwaysFailProvider


def main():
    print("=" * 60)
    print("LLM 超时配置与重试总时限测试")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. anthropic create_client 细粒度超时
    # ------------------------------------------------------------------
    print("\n[1] anthropic client httpx.Timeout")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
    from Agent.providers.anthropic_provider import AnthropicProvider

    client = AnthropicProvider().create_client({
        "provider": "anthropic_compatible",
        "model": "claude-test",
        "api_key_env": "ANTHROPIC_API_KEY",
    })
    timeout = client.timeout
    check("connect 默认 15s", float(timeout.connect) == 15.0, repr(timeout))
    check("read 默认 120s（api_timeout）", float(timeout.read) == 120.0, repr(timeout))
    check("write 默认 30s", float(timeout.write) == 30.0, repr(timeout))
    check("pool 默认 10s", float(timeout.pool) == 10.0, repr(timeout))
    check("max_retries=0 不变", client.max_retries == 0)

    client2 = AnthropicProvider().create_client({
        "provider": "anthropic_compatible",
        "model": "claude-test",
        "api_key_env": "ANTHROPIC_API_KEY",
        "connect_timeout": 5,
        "api_timeout": 60,
    })
    t2 = client2.timeout
    check("connect_timeout 可配置", float(t2.connect) == 5.0, repr(t2))
    check("api_timeout 落到 read", float(t2.read) == 60.0, repr(t2))

    # ------------------------------------------------------------------
    # 2. openai create_client 细粒度超时
    # ------------------------------------------------------------------
    print("\n[2] openai client httpx.Timeout")
    os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")
    from Agent.providers.openai_provider import OpenAIProvider

    oclient = OpenAIProvider().create_client({
        "provider": "openai_compatible",
        "model": "gpt-test",
        "api_key_env": "OPENAI_API_KEY",
    })
    ot = oclient.timeout
    check("connect 默认 15s", float(ot.connect) == 15.0, repr(ot))
    check("read 默认 120s", float(ot.read) == 120.0, repr(ot))

    # ------------------------------------------------------------------
    # 3. generate() 总时限兜底
    # ------------------------------------------------------------------
    print("\n[3] max_total_seconds 封顶重试总耗时")
    from Agent.llm import create_llm

    llm = create_llm({
        "provider": "always_fail",
        "model": "fail-model",
        "max_retries": 100,        # 次数上限拉高，确保封顶来自总时限
        "max_total_seconds": 2,
    })
    AlwaysFailProvider.state["calls"] = 0
    t0 = time.time()
    raised = None
    try:
        llm.query([{"role": "user", "content": "hi"}])
    except ConnectionError as e:
        raised = e
    elapsed = time.time() - t0
    calls = AlwaysFailProvider.state["calls"]

    check("抛出原始可重试错误", isinstance(raised, ConnectionError), repr(raised))
    check("总耗时被 max_total_seconds=2 封顶（<15s）", elapsed < 15, f"{elapsed:.1f}s")
    check("总耗时 >= 2s（确实等过退避）", elapsed >= 2, f"{elapsed:.1f}s")
    # 退避序列 1,2,4,8...：2s 预算内最多 ~2 次重试 → calls 远小于 max_retries
    check("重试次数远小于 max_retries=100", calls < 10, f"calls={calls}")

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
