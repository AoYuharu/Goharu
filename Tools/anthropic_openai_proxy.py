#!/usr/bin/env python
"""
Anthropic <-> OpenAI 协议翻译代理

让 Claude Code (Anthropic 原生格式) 可以接入只支持 OpenAI 兼容格式的 LLM 网关。

用法:
  1. 设置环境变量:
     export ANTHROPIC_AUTH_TOKEN=sk-xxx
     export UPSTREAM_BASE=https://openox.tech/v1
     export PROXY_PORT=8099

  2. 启动代理:
     python Tools/anthropic_openai_proxy.py

  3. 配置 Claude Code:
     ANTHROPIC_BASE_URL=http://localhost:8099/v1
     ANTHROPIC_AUTH_TOKEN=sk-xxx
     ANTHROPIC_MODEL=gpt-5.4
"""

import json
import os
import sys
import uuid
import traceback

import requests
from flask import Flask, request, Response, stream_with_context

# ---------- 配置 ----------
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://openox.tech/v1")
UPSTREAM_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN", "sk-no-key")
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "8099"))

app = Flask(__name__)


# ====================================================================
# 工具函数
# ====================================================================

def _extract_text_from_content(content):
    """从 Anthropic content (string 或 list of blocks) 中提取纯文本."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _make_anthropic_id(prefix="msg"):
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ====================================================================
# 请求翻译: Anthropic Messages → OpenAI Chat Completions
# ====================================================================

def anthropic_to_openai(body: dict) -> dict:
    """
    关键映射:
      - system (top-level)  → messages[0] role="system"
      - tools[].input_schema → tools[].function.parameters
      - content 中 tool_use    → assistant.tool_calls
      - content 中 tool_result → message role="tool"
      - max_tokens            → max_tokens
      - tool_choice           → tool_choice (auto / required / specific)
    """
    model = body.get("model", "gpt-4")
    messages = []

    # 1) system prompt
    if "system" in body:
        sys_content = _extract_text_from_content(body["system"])
        if sys_content:
            messages.append({"role": "system", "content": sys_content})

    # 2) conversation messages
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "assistant":
            text_parts = []
            tool_calls = []

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", _make_anthropic_id("call")),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)
                            }
                        })
            else:
                text_parts.append(str(content))

            oai_msg = {"role": "assistant"}
            oai_msg["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
                if not oai_msg["content"]:
                    oai_msg["content"] = None   # OpenAI 要求 tool_calls 时 content 为 null

            messages.append(oai_msg)

        elif role == "user":
            tool_results = []
            text_parts = []

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        tool_results.append(block)
                    elif btype == "text":
                        text_parts.append(block.get("text", ""))
                    # 其他 unknown block 类型直接跳过
            else:
                text_parts.append(str(content))

            # tool_result 在 OpenAI 中是独立的 role="tool"
            for tr in tool_results:
                tr_content = tr.get("content", "")
                if isinstance(tr_content, list):
                    tr_content = _extract_text_from_content(tr_content)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id", ""),
                    "content": tr_content
                })

            # 如果有文本内容，仍然作为一个 user message
            if text_parts:
                messages.append({"role": "user", "content": "\n".join(text_parts)})
            # 如果 content 是纯字符串（首轮用户输入）
            elif not tool_results and isinstance(content, str):
                messages.append({"role": "user", "content": content})

    oai_body = {
        "model": model,
        "messages": messages,
    }

    # 3) generation params
    if "max_tokens" in body:
        oai_body["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        oai_body["temperature"] = body["temperature"]
    if "top_p" in body:
        oai_body["top_p"] = body["top_p"]
    if "top_k" in body:
        # OpenAI 没有 top_k，跳过
        pass
    if "stop_sequences" in body:
        oai_body["stop"] = body["stop_sequences"]

    # 4) tools
    if body.get("tools"):
        oai_tools = []
        for td in body["tools"]:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td.get("description", ""),
                    "parameters": td.get("input_schema", {"type": "object", "properties": {}})
                }
            })
        oai_body["tools"] = oai_tools

    # 5) tool_choice
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, str):
            if tc == "any":
                oai_body["tool_choice"] = "required"
            else:
                oai_body["tool_choice"] = "auto"
        elif isinstance(tc, dict) and "name" in tc:
            oai_body["tool_choice"] = {
                "type": "function",
                "function": {"name": tc["name"]}
            }

    return oai_body


# ====================================================================
# 响应翻译: OpenAI Chat Completions → Anthropic Messages
# ====================================================================

def openai_to_anthropic(oai_body: dict, model_name: str) -> dict:
    """
    关键映射:
      - choices[0].message.content          → content[] type="text"
      - choices[0].message.tool_calls       → content[] type="tool_use"
      - finish_reason "tool_calls"          → stop_reason "tool_use"
      - finish_reason "stop"               → stop_reason "end_turn"
      - finish_reason "length"             → stop_reason "max_tokens"
      - usage.prompt_tokens                → usage.input_tokens
      - usage.completion_tokens            → usage.output_tokens
    """
    choice = oai_body.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")
    usage = oai_body.get("usage", {})

    anthropic_content = []

    # text
    if message.get("content"):
        anthropic_content.append({"type": "text", "text": message["content"]})

    # tool calls
    for tc in message.get("tool_calls", []):
        func = tc.get("function", {})
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            arguments = func.get("arguments", "{}")

        anthropic_content.append({
            "type": "tool_use",
            "id": tc.get("id", _make_anthropic_id("toolu")),
            "name": func.get("name", ""),
            "input": arguments
        })

    # stop_reason
    stop_reason_map = {
        "tool_calls": "tool_use",
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "end_turn",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")

    return {
        "id": _make_anthropic_id("msg"),
        "type": "message",
        "role": "assistant",
        "content": anthropic_content,
        "model": model_name,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    }


# ====================================================================
# 流式响应翻译: OpenAI SSE → Anthropic SSE
# ====================================================================

def translate_sse_stream(upstream_resp, model_name: str):
    """
    两套 SSE 格式的关键差异:

    OpenAI SSE:
      data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}
      data: {"choices":[{"delta":{"tool_calls":[...]}}]}
      data: [DONE]

    Anthropic SSE:
      event: message_start
      data: {"type":"message_start","message":{...}}

      event: content_block_start
      data: {"type":"content_block_start","content_block":{"type":"text","text":""}}

      event: content_block_delta
      data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

      event: content_block_stop
      data: {}

      event: message_delta
      data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{...}}

      event: message_stop
      data: {"type":"message_stop"}
    """

    def generate():
        msg_id = _make_anthropic_id("msg")
        sent_message_start = False
        block_start_sent = set()   # set of block indices
        text_block_idx = 0
        accumulated_tool_calls = {}  # index -> {id, name, segments}
        input_tokens = 0
        output_tokens = 0
        stop_reason = "end_turn"
        has_any_content = False

        def _emit_event(event_type, data_dict):
            return f"event: {event_type}\ndata: {json.dumps(data_dict, ensure_ascii=False)}\n\n"

        for raw_line in upstream_resp.iter_lines():
            if not raw_line:
                continue

            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data: "):
                continue

            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            # usage
            if "usage" in chunk:
                usg = chunk["usage"]
                if usg.get("prompt_tokens"):
                    input_tokens = usg["prompt_tokens"]
                if usg.get("completion_tokens"):
                    output_tokens = usg["completion_tokens"]

            # 第一个 chunk: 发送 message_start
            if not sent_message_start:
                yield _emit_event("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model_name,
                        "content": [],
                        "usage": {"input_tokens": input_tokens, "output_tokens": 0}
                    }
                })
                sent_message_start = True

            # ---- text content ----
            if delta.get("content"):
                has_any_content = True
                text = delta["content"]
                if text_block_idx not in block_start_sent:
                    block_start_sent.add(text_block_idx)
                    yield _emit_event("content_block_start", {
                        "type": "content_block_start",
                        "index": text_block_idx,
                        "content_block": {"type": "text", "text": ""}
                    })
                yield _emit_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": text_block_idx,
                    "delta": {"type": "text_delta", "text": text}
                })

            # ---- tool calls ----
            if delta.get("tool_calls"):
                has_any_content = True
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id": tc.get("id", ""),
                            "name": "",
                            "segments": []
                        }

                    entry = accumulated_tool_calls[idx]
                    if "id" in tc and tc["id"]:
                        entry["id"] = tc["id"]
                    if tc.get("function", {}).get("name"):
                        entry["name"] = tc["function"]["name"]

                    arg_delta = tc.get("function", {}).get("arguments", "")
                    if arg_delta:
                        entry["segments"].append(arg_delta)

                    if idx not in block_start_sent:
                        block_start_sent.add(idx)
                        yield _emit_event("content_block_start", {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": entry["id"],
                                "name": entry["name"],
                                "input": {}
                            }
                        })

                    if arg_delta:
                        yield _emit_event("content_block_delta", {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": arg_delta
                            }
                        })

            # finish_reason
            if finish_reason:
                if finish_reason == "tool_calls":
                    stop_reason = "tool_use"
                elif finish_reason == "length":
                    stop_reason = "max_tokens"
                else:
                    stop_reason = "end_turn"

        # ---- 结束后: 关闭所有内容块 ----
        for idx in sorted(block_start_sent):
            yield _emit_event("content_block_stop", {
                "type": "content_block_stop",
                "index": idx
            })

        yield _emit_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason},
            "usage": {"output_tokens": output_tokens if output_tokens > 0 else 1}
        })
        yield _emit_event("message_stop", {"type": "message_stop"})

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ====================================================================
# 代理路由
# ====================================================================

def _log(msg: str):
    print(f"[PROXY] {msg}", flush=True)


@app.errorhandler(Exception)
def handle_error(e):
    _log(f"ERROR: {e}")
    traceback.print_exc()
    return app.response_class(
        response=json.dumps({"error": str(e), "type": type(e).__name__}, ensure_ascii=False),
        status=500,
        mimetype="application/json"
    )


def _handle_messages():
    """处理 POST /v1/messages"""
    anthropic_body = request.get_json(force=True)
    model_name = anthropic_body.get("model", "gpt-4")
    is_stream = anthropic_body.get("stream", False)

    # 翻译请求
    openai_body = anthropic_to_openai(anthropic_body)
    openai_body["stream"] = is_stream

    _log(f"→ upstream model={openai_body['model']} stream={is_stream} "
         f"msgs={len(openai_body['messages'])} tools={len(openai_body.get('tools', []))}")

    try:
        upstream_resp = requests.post(
            f"{UPSTREAM_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {UPSTREAM_KEY}",
                "Content-Type": "application/json",
            },
            json=openai_body,
            stream=is_stream,
            timeout=600,
        )
        upstream_resp.raise_for_status()
    except requests.RequestException as e:
        _log(f"upstream error: {e}")
        return app.response_class(
            response=json.dumps({"error": str(e)}, ensure_ascii=False),
            status=502,
            mimetype="application/json"
        )

    if is_stream:
        return translate_sse_stream(upstream_resp, model_name)
    else:
        oai_resp = upstream_resp.json()
        anthropic_resp = openai_to_anthropic(oai_resp, model_name)
        _log(f"← response stop={anthropic_resp['stop_reason']} "
             f"content_blocks={len(anthropic_resp['content'])}")
        return app.response_class(
            response=json.dumps(anthropic_resp, ensure_ascii=False),
            status=200,
            mimetype="application/json"
        )


# 同时注册 /v1/messages 和 /messages (兼容不同 ANTHROPIC_BASE_URL 设置)
@app.route("/v1/messages", methods=["POST"])
def v1_messages():
    return _handle_messages()


@app.route("/messages", methods=["POST"])
def messages():
    return _handle_messages()


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "upstream": UPSTREAM_BASE}


# ====================================================================
# 启动
# ====================================================================

if __name__ == "__main__":
    _log(f"启动 Anthropic→OpenAI 翻译代理")
    _log(f"  监听端口: {LISTEN_PORT}")
    _log(f"  上游地址: {UPSTREAM_BASE}")
    _log(f"  上游密钥: {UPSTREAM_KEY[:12]}...")
    _log(f"")
    _log(f"请设置 Claude Code 环境变量:")
    _log(f"  ANTHROPIC_BASE_URL=http://localhost:{LISTEN_PORT}/v1")
    _log(f"  ANTHROPIC_AUTH_TOKEN={UPSTREAM_KEY}")
    _log(f"  ANTHROPIC_MODEL=gpt-5.4")
    _log(f"")

    app.run(host="0.0.0.0", port=LISTEN_PORT, debug=False)
