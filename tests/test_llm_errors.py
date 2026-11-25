"""Agent.providers.errors 契约测试 —— LLM 失败归类（仅标准库，无网络）。

规则来自各厂商实测报错原文（2026-11-24）：DeepSeek / Kimi / MiniMax 的 401 体、
智谱 400「模型不存在」、OpenAI 协议 401、OpenAI / 智谱的配额错误、SDK 网络异常。

运行：python tests/test_llm_errors.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Agent.providers import errors as E  # noqa: E402

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


class FakeAPIError(Exception):
    def __init__(self, text, status=None):
        super().__init__(text)
        if status is not None:
            self.status_code = status


# 实测原文（密钥均已脱敏）
DEEPSEEK_401 = ("Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
                "'message': 'Authentication Fails, Your api key: ****7zyb is invalid "
                "(request_id: 41eea963-0486-4a44-b4fa-c4a73219b76d)'}}")
MINIMAX_401 = ("Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
               "'message': \"login fail: Please carry the API secret key in the 'X-Api-Key' "
               "field of the request header\"}, 'request_id': '0754df9fe7155f275993416f151db203'}")
KIMI_401 = ("Error code: 401 - {'error': {'message': 'The API Key appears to be invalid or may "
            "have expired. Please verify your credentials and try again.', "
            "'type': 'invalid_authentication_error'}}")
ZHIPU_400 = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
             "'code': '1211', 'message': '[1211][模型不存在，请检查模型代码。]"
             "[20261124232625071f25a002464545]'}, 'request_id': '20261124232625071f25a002464545'}")
OPENAI_401 = ("Error code: 401 - Authentication Fails, Your api key: ****-000 is invalid "
              "(request_id: d8a1c1fa-d20b-4389-af48-11095f1a2407)")


def main():
    print("=" * 60)
    print("LLM 失败归类契约测试")
    print("=" * 60)

    print("\n[1] 厂商鉴权失败（401）")
    for name, body in (("DeepSeek", DEEPSEEK_401), ("MiniMax", MINIMAX_401),
                       ("Kimi", KIMI_401), ("OpenAI 协议", OPENAI_401)):
        kind, msg = E.explain_exception(FakeAPIError(body, 401))
        check(f"{name} 401 → auth", kind == "auth", kind)
        check(f"{name} 401 文案含处置建议", "API Key 无效或无权限（401）" in msg
              and "请确认密钥属于当前厂商" in msg, msg)
        check(f"{name} 401 保留上游原话", "上游返回：" in msg and "request_id" not in msg, msg)
    _, minimax_msg = E.explain_exception(FakeAPIError(MINIMAX_401, 401))
    check("MiniMax 嵌套引号的 message 提取完整",
          "login fail: Please carry the API secret key in the 'X-Api-Key' field" in minimax_msg,
          minimax_msg)

    print("\n[2] 模型 / 地址类（400 / 404）")
    kind, msg = E.explain_exception(FakeAPIError(ZHIPU_400, 400))
    check("智谱 400 模型不存在 → not_found", kind == "not_found", kind)
    check("not_found 提示检查模型名与地址", "请检查模型名与 API 地址是否匹配" in msg, msg)

    print("\n[3] 配额 / 限流（402 / 429 / 403）")
    kind, _ = E.explain_exception(FakeAPIError("Error code: 402 - {'error': "
                                               "{'message': 'Insufficient Balance'}}", 402))
    check("DeepSeek 402 → quota", kind == "quota", kind)
    kind, _ = E.explain_exception(FakeAPIError(
        "Error code: 429 - You exceeded your current quota, please check your plan and billing "
        "details.", 429))
    check("OpenAI 429 insufficient_quota → quota", kind == "quota", kind)
    kind, _ = E.explain_exception(FakeAPIError(
        "Error code: 429 - {'error': {'code': '1113', 'message': '您的账户已欠费，请充值后重试'}}", 429))
    check("智谱 429 欠费 → quota", kind == "quota", kind)
    kind, _ = E.explain_exception(FakeAPIError("Error code: 429 - Rate limit reached for requests", 429))
    check("普通 429 限流 → rate_limit", kind == "rate_limit", kind)
    kind, _ = E.explain_exception(FakeAPIError(
        "Error code: 403 - {'error': {'message': 'Your account is suspended, please check your "
        "plan and billing.'}}", 403))
    check("403 余额欠费 → quota（而非 auth）", kind == "quota", kind)
    kind, _ = E.explain_exception(FakeAPIError(
        "Error code: 403 - {'error': {'type': 'forbidden', 'message': 'Request not allowed'}}", 403))
    check("403 其它 → auth", kind == "auth", kind)

    print("\n[4] 可重试判定（LLMCore._is_retryable_error 共用）")
    cases = [
        ("限流", FakeAPIError("Error code: 429 - Rate limit reached", 429), True),
        ("5xx", FakeAPIError("Error code: 503 - Overloaded", 503), True),
        ("超时", FakeAPIError("Request timed out."), True),
        ("网络", FakeAPIError("Connection error."), True),
        ("旧式 RuntimeError(HTTP 500)", RuntimeError("HTTP 500 server error"), True),
        ("鉴权", FakeAPIError(DEEPSEEK_401, 401), False),
        ("配额", FakeAPIError("Error code: 402 - Insufficient Balance", 402), False),
        ("模型不存在", FakeAPIError(ZHIPU_400, 400), False),
        ("400 参数错误", FakeAPIError("Error code: 400 - invalid_request_error", 400), False),
        ("未知异常", RuntimeError("boom"), False),
    ]
    for name, exc, want in cases:
        kind, _ = E.explain_exception(exc)
        check(f"{name} → retryable={want}", (kind in E.RETRYABLE_KINDS) is want, kind)

    print("\n[5] auto 探测合并错误（LLMProviderError）")
    merged = E.LLMProviderError(
        "LLM provider auto-detection failed for all protocols. " + OPENAI_401 + " | " + DEEPSEEK_401,
        "openai_compatible: AuthenticationError: " + OPENAI_401
        + " | anthropic_compatible: AuthenticationError: " + DEEPSEEK_401)
    kind, msg = E.explain_exception(merged)
    check("合并错误优先识别 auth（404 路由噪音不误导）", kind == "auth", kind)
    check("合并错误 401 状态码取自明细", "（401）" in msg, msg)
    check("合并错误逐协议保留真实原因", "openai_compatible" in msg and "anthropic_compatible" in msg, msg)
    no_key = E.LLMProviderError(
        "LLM provider auto-detection failed for all protocols. openai_compatible: ValueError: "
        "Environment variable ZHIPU_API_KEY is required for provider openai_compatible | "
        "anthropic_compatible: ValueError: Environment variable ZHIPU_API_KEY is required",
        "openai_compatible: ValueError: Environment variable ZHIPU_API_KEY is required for "
        "provider openai_compatible | anthropic_compatible: ValueError: Environment variable "
        "ZHIPU_API_KEY is required")
    kind, msg = E.explain_exception(no_key)
    check("缺 key → missing_key", kind == "missing_key", kind)
    check("缺 key 文案指出具体环境变量", "ZHIPU_API_KEY" in msg and "未配置 API Key" in msg, msg)
    net = E.LLMProviderError("x", "openai_compatible: APIConnectionError: Connection error. | "
                                  "anthropic_compatible: APIConnectionError: Connection error.")
    kind, msg = E.explain_exception(net)
    check("全协议连不上 → network", kind == "network" and "无法连接" in msg, (kind, msg))

    print("\n[6] 裸状态码 + 响应体（安装向导 urllib 路径）")
    kind, st = E.classify_failure(401, json.dumps(
        {"error": {"message": "Authentication Fails, Your api key: ****7zyb is invalid",
                   "type": "authentication_error", "code": "invalid_request_error"}}))
    check("401 + JSON 体 → auth", (kind, st) == ("auth", 401), (kind, st))
    kind, st = E.classify_failure(402, "")
    check("402 空体 → quota", (kind, st) == ("quota", 402), (kind, st))
    kind, st = E.classify_failure(200, '{"code":500,"msg":"404 NOT_FOUND"}')
    check("HTTP 200 包业务错误 → not_found", kind == "not_found", kind)
    kind, st = E.classify_failure(502, "<html><body>502 Bad Gateway</body></html>")
    check("HTML 502 页 → server", (kind, st) == ("server", 502), (kind, st))
    msg = E.failure_message("auth", 401, hint=False)
    check("brief（hint=False）只给原因", msg == "API Key 无效或无权限（401）", msg)
    msg = E.failure_message("unknown", None, "RuntimeError: boom")
    check("unknown 原样透传", msg == "RuntimeError: boom", msg)

    print("\n[7] 上游报错句压缩")
    check("SDK JSON 噪音被压掉",
          E.upstream_detail(DEEPSEEK_401) == "401 Authentication Fails, Your api key: ****7zyb is invalid",
          E.upstream_detail(DEEPSEEK_401))
    check("request_id 剥除", "request_id" not in E.upstream_detail(OPENAI_401),
          E.upstream_detail(OPENAI_401))
    check("非 JSON 文本按原文截断", E.upstream_detail("Connection error.") == "Connection error.")
    check("空文本不抛错", E.upstream_detail("") == "")

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
