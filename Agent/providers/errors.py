"""LLM 调用失败归类（仅标准库，无 SDK 依赖）。

把「HTTP 状态码 + 响应体」或 SDK 异常翻译成用户看得懂的中文原因。安装向导
（urllib 裸请求）、启动期 provider 探测、对话轮（SDK 异常）、TUI 模型切换
共用这一份规则：同一种失败在任何入口都是同一句话。

kind：missing_key / auth / quota / not_found / bad_request / rate_limit /
      timeout / network / server / unknown
"""
from __future__ import annotations

import re

RETRYABLE_KINDS = frozenset({"rate_limit", "timeout", "network", "server"})

# kind → (原因, 处置建议)
_KIND_INFO = {
    "missing_key": ("未配置 API Key", "请在 .env 中填写密钥，或重新运行安装向导"),
    "auth": ("API Key 无效或无权限", "请确认密钥属于当前厂商、未过期、未被禁用"),
    "quota": ("API 配额不足或余额已用完", "请到厂商控制台查看余额与配额，或更换密钥"),
    "not_found": ("模型或 API 地址不存在", "请检查模型名与 API 地址是否匹配"),
    "bad_request": ("请求被接口拒绝", "请检查模型名与 API 地址是否匹配"),
    "rate_limit": ("请求过于频繁（限流）", "稍后重试"),
    "timeout": ("连接超时", "请检查网络与代理设置"),
    "network": ("无法连接 API 地址", "请检查网络、代理与 API 地址"),
    "server": ("模型服务端出错", "服务端暂时不可用，请稍后重试"),
    "unknown": ("调用失败", ""),
}

_STATUS_KIND = {
    400: "bad_request", 401: "auth", 402: "quota", 403: "auth",
    404: "not_found", 408: "timeout", 413: "bad_request", 422: "bad_request",
    429: "rate_limit",
}

# 文本规则：顺序即优先级——鉴权失败比「路由 404」更能定性（auto 探测会先打
# OpenAI 协议，对 Anthropic 兼容端点必然得到一个预期内的 404）
_TEXT_RULES = (
    ("missing_key", re.compile(
        r"environment variable \w+ is required"
        r"|missing model\.large-language-model\.api_key_env"
        r"|api[_ ]?key (is required|not set|missing)", re.I)),
    ("auth", re.compile(
        r"authentication_?error|unauthori[sz]ed|permission_?denied|forbidden|login fail"
        r"|invalid[_ ]?(api[_ ]?key|x-api-key)"
        r"|api[_ ]?key[^|\n]{0,60}?(invalid|incorrect|expired|非法|不合法|过期)"
        r"|认证失败|密钥(无效|错误|过期)", re.I)),
    ("quota", re.compile(
        r"insufficient[_ ]?(quota|balance|user_quota)|exceeded your current quota"
        r"|payment required|billing|余额不足|欠费|请充值|arrear", re.I)),
    ("not_found", re.compile(
        r"not[_ ]?found|no such model|model_not_found|does not exist|不存在", re.I)),
    ("rate_limit", re.compile(
        r"rate[_ ]?limit|too many requests|(?:^|[^\w-])429(?:[^\w-]|$)|限流|请求过于频繁",
        re.I)),
    ("server", re.compile(
        r"server_?error|internal server|service unavailable|bad gateway|overloaded"
        r"|(?:^|[^\w-])5\d\d(?:[^\w-]|$)", re.I)),
    ("timeout", re.compile(r"timed?[\s_]?out|超时", re.I)),
    ("network", re.compile(
        r"connection|connecterror|network|getaddrinfo|name or service not known"
        r"|resolve host|no route to host|ssl|tls|proxy|unreachable|refused"
        r"|reset by peer|protocolerror|disconnected|无法连接", re.I)),
    ("bad_request", re.compile(
        r"bad_?request|invalid[_ ]request[_ ]error|请求参数|参数错误", re.I)),
)
_RULES = dict(_TEXT_RULES)
# 状态码不够具体时（400 / 403 / 429 等）再按文本细化，仍按信息量排序
_REFINE_ORDER = ("quota", "auth", "not_found")

_MSG_RE = re.compile(
    r"(['\"])(?:message|msg|detail|error)\1\s*:\s*(['\"])((?:(?!\2).)+)\2")
_ERROR_CODE_RE = re.compile(r"Error code:\s*(\d{3})\s*-\s*(.*?)(?=\s*\||$)")


class LLMProviderError(RuntimeError):
    """provider 解析 / 探测失败（启动期装配与热切换共用）。

    message 保留原始细节（日志与冒烟测试依赖其中的协议名与异常类型）；
    面向用户的一句话说明用 explain_exception() 现取。
    """


def _first_message(text: str) -> str:
    """响应体 / 异常文本里上游真正想说的第一句话（error.message / message / msg）。"""
    m = _MSG_RE.search(text)
    return m.group(3) if m else ""


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def upstream_detail(text: str, limit: int = 400) -> str:
    """把异常文本压成上游报错句：去掉 SDK 的 JSON / headers 噪音，多协议探测失败时逐段保留。"""
    def squash(m):
        body = m.group(2)
        return f"{m.group(1)} {_squeeze(_first_message(body) or body)}"

    text = _ERROR_CODE_RE.sub(squash, str(text or ""))
    if "Error code:" not in text:
        text = _first_message(text) or text
    return _squeeze(re.sub(r"\s*\(request_id:[^)]*\)", "", text))[:limit]


def classify_failure(status, text="") -> tuple[str, int | None]:
    """→ (kind, 用于展示的状态码)。有状态码时状态码优先（文本仅用于细化），否则按文本规则。"""
    text = str(text or "")
    codes = [int(m.group(1)) for m in _ERROR_CODE_RE.finditer(text)]
    if status is None:
        status = codes[0] if len(set(codes)) == 1 else None
    if status is None:
        for kind, rx in _TEXT_RULES:
            if rx.search(text):
                return kind, next((c for c in codes if _STATUS_KIND.get(c) == kind
                                   or (kind == "server" and c >= 500)), None)
        return "unknown", None
    status = int(status)
    kind = "server" if status >= 500 else _STATUS_KIND.get(status, "unknown")
    if kind in ("bad_request", "rate_limit", "unknown") or (kind == "auth" and status == 403):
        for refined in _REFINE_ORDER:
            if _RULES[refined].search(text):
                kind = refined
                break
    return kind, status


def failure_message(kind: str, status=None, detail: str = "", *, hint: bool = True) -> str:
    """一句话用户可读说明：原因（状态码）。处置建议 [+ 上游原话]。unknown 原样透传 detail。"""
    if kind == "unknown":
        return detail or _KIND_INFO["unknown"][0]
    reason, advice = _KIND_INFO[kind]
    msg = f"{reason}（{status}）" if status else reason
    if hint and advice:
        msg += "。" + advice
    if detail:
        msg += "\n上游返回：" + detail
    return msg


def explain_exception(exc: BaseException) -> tuple[str, str]:
    """任意异常 → (kind, 用户可读说明)。供对话轮 / 启动期 / 模型切换展示。"""
    if isinstance(exc, LLMProviderError):
        text = exc.args[1] if len(exc.args) > 1 else str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}"
    kind, status = classify_failure(getattr(exc, "status_code", None), text)
    if kind == "missing_key":
        m = re.search(r"Environment variable (\w+) is required", text)
        detail = f"环境变量 {m.group(1)} 为空" if m else ""
    else:
        detail = upstream_detail(text)
    return kind, failure_message(kind, status, detail)
