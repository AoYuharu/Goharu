"""
Gateway Entry Point — agent backend subprocess.

JSON-RPC server over stdio for TUI communication.
Supports message batching: pending user messages are merged before each actor step.

Protocol contract (methods/events/payloads): docs/tui-protocol.md
— consumed by the tui-ts/ (TypeScript Ink) frontend. Keep both in sync
when changing the protocol.
"""

import asyncio
import http.server
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to path BEFORE importing project modules
project_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(project_root))

# Set UTF-8 encoding for stdout/stderr (platform-aware, no-op on Linux)
from Tools.platform_utils import setup_stdio_encoding
setup_stdio_encoding()

# Real stdout for JSON-RPC (AFTER encoding setup)
_real_stdout = sys.stdout
_stdout_lock = threading.Lock()

from Tools.api import reconnect_mcp_server
from configurationLoader import config
from Gateway.channels.base import ChannelMessage
from Gateway.session_manager import SessionManager

# Logging setup — centralized, per-module file output
from Core.LogManager import init_logging, get_logger
from Core.LifecycleLog import lifecycle
init_logging()
logger = get_logger(__name__)
lifecycle("════════ 新会话 ════════", f"Gateway 进程启动 pid={os.getpid()}", level=1)

# Crash log path (独立 crash 文件，同时 LogManager 也会捕获 ERROR+ 到 logs/gateway/crash.log)
CRASH_LOG = project_root / "runtime_memory" / "logs" / "gateway" / "crash.log"


def _env_flag(name: str, default: bool) -> bool:
    """环境变量布尔覆盖（机器专属配置，如 haru 的 headless 守护）。"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def write_json(obj: Dict[str, Any]) -> bool:
    """
    Write a JSON-RPC message to stdout.

    Returns:
        bool: True on success, False if pipe is broken
    """
    try:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with _stdout_lock:
            _real_stdout.write(line)
            _real_stdout.flush()
        return True
    except (BrokenPipeError, OSError, ValueError) as e:
        # Pipe broken during shutdown — expected, not an error
        if isinstance(e, ValueError) and "closed file" not in str(e):
            raise
        return False


def emit_voice_event(ev_type: str, payload: Dict[str, Any]):
    """推送语音相关事件给 TUI（下载进度 / 转写结果 / 状态）。"""
    write_json({
        "jsonrpc": "2.0", "method": "event",
        "params": {"type": ev_type, "payload": payload},
    })


def log_crash(reason: str, exc_info=None):
    """Log crash information to crash log and stderr.

    LogManager 的 root logger 已附加 crash handler 和 stderr handler，
    直接使用 logger.error() 会同时写入 crash.log 和 stderr。
    同时保留原始 crash 格式写入独立文件以便快速 grep。
    """
    logger.error("CRASH: %s", reason, exc_info=exc_info if exc_info else None)
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== Gateway crash · {datetime.now().isoformat()} ===\n")
            f.write(f"Reason: {reason}\n")
            if exc_info:
                traceback.print_exception(*exc_info, file=f)
    except Exception:
        pass


def _set_dotenv_value(key: str, value: str):
    """写入/更新 .env 文件中的 key=value（保留其他行与注释）。"""
    env_path = project_root / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    quoted = value.replace('"', '\\"')
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f'{key}="{quoted}"'
            found = True
            break
    if not found:
        lines.append(f'{key}="{quoted}"')
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _classify_switch_failure(err: str):
    """把模型切换失败的原始异常串归类成一句用户可读的短消息。

    返回 (error_kind, message)。auto 探测的原始错误里混着 OpenAI 协议探测
    的预期失败（如对 anthropic 路由的 404），不能原样抛给用户。
    """
    low = (err or "").lower()
    if "authenticationerror" in low or "401" in low or "login fail" in low:
        return "auth", "API Key 认证失败（401）：key 缺失或无效，请重新输入正确的 key 后重试"
    if "apitimeouterror" in low or "timeout" in low or "connection error" in low:
        return "network", "无法连接目标端点（超时或网络错误），请检查 API URL 与网络后重试"
    if "notfounderror" in low or "404" in low:
        return "route", "端点路由不存在（404）：请检查 API URL 是否正确"
    return "unknown", f"切换失败：{(err or '')[:160]}"


# Global session manager（多 session 装配根；TUI 请求经它路由到目标 session）
_manager: Optional[SessionManager] = None
_request_queue: queue.Queue = queue.Queue()  # Incoming request queue
_bg_reader_running = False

# 进程级常驻事件循环：MCP 会话（asyncio 资源）绑定在创建它的 loop 上，
# 历史上 init/每条消息/收尾各建一个临时 loop 并即关，导致 MCP 首调必报
# "Event loop is closed"，重连途中还会间歇性静默崩溃。统一用同一个 loop。
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_main_loop() -> asyncio.AbstractEventLoop:
    global _MAIN_LOOP
    if _MAIN_LOOP is None or _MAIN_LOOP.is_closed():
        _MAIN_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_MAIN_LOOP)
    return _MAIN_LOOP
_SHUTTING_DOWN = False  # Set to True during process shutdown to stop new processing


def dispatch(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Dispatch a JSON-RPC request.

    For agent.send_message: if session is already processing, the message
    is queued for batching and the method returns immediately.

    Args:
        request: JSON-RPC request object

    Returns:
        JSON-RPC response or None for notifications
    """
    global _manager, _bg_reader_running

    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    try:
        if method == "gateway.ping":
            return {"jsonrpc": "2.0", "result": {
                "ok": True, "pid": os.getpid(),
                "daemon": "--daemon" in sys.argv,
                "sessions": list(_manager.sessions) if _manager else [],
            }, "id": req_id}
        if method == "gateway.shutdown":
            logger.info("gateway.shutdown requested via RPC")
            _bg_reader_running = False   # 主循环退出 → finally 走完整收尾
            return {"jsonrpc": "2.0", "result": {"shutting_down": True}, "id": req_id}
        if method == "agent.send_message":
            message = params.get("message", "")
            session_id = params.get("session_id", "default")

            # TUI 入口等价于 tui channel 的入站消息（带来源标注入队）
            # priority="high"：插队 + 打断当前轮（默认 normal 排队）
            _manager.get_session(session_id).enqueue(ChannelMessage(
                text=message, channel_type="tui", channel_id="tui_main",
                priority=str(params.get("priority", "normal")),
            ))

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True},
                "id": req_id
            }

        elif method == "agent.clear_session":
            session_id = params.get("session_id", "default")

            session = _manager.get_session(session_id)
            session.queue_command("clear_session", {"session_id": session_id}, req_id)
            session._start_processing()

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True, "command": "clear_session"},
                "id": req_id,
            }

        elif method == "agent.compact":
            session_id = params.get("session_id", "default")

            session = _manager.get_session(session_id)
            session.queue_command("compact", {"session_id": session_id}, req_id)
            session._start_processing()

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True, "command": "compact"},
                "id": req_id,
            }

        elif method == "agent.upgrade":
            session_id = params.get("session_id", "default")

            session = _manager.get_session(session_id)
            session.queue_command("upgrade", {"session_id": session_id}, req_id)
            session._start_processing()

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True, "command": "upgrade"},
                "id": req_id,
            }

        elif method == "agent.get_history":
            """获取聊天历史记录（结构化回放条目：text/thinking/tool）。

            tool_use 与其后的 tool_result 按 tool_use_id 配对回填，
            使 TUI 能以与实时一致的形态（折叠工具行、thinking 块）重建界面。
            存储的 assistant 文本可能带有 LLM 模仿的 [ID:msg_xxx] 前缀，
            回放前统一剥离（与 ActorAgent 实时输出同一规则）。
            """
            session_id = params.get("session_id", "default")
            replay = _manager.get_session(session_id).get_history_payload()

            return {
                "jsonrpc": "2.0",
                "result": {"messages": replay},
                "id": req_id
            }

        elif method == "tools.list":
            """列出当前注册的全部工具（内置 + MCP），供 TUI 工具面板展示。

            MCP 工具命名约定 mcp__<server>__<tool>，据此还原来源分组；
            last_tool_definitions 在启动与 ReloadTools/mcp refresh 后保持最新。
            """
            definitions = list(
                getattr(_manager.runtime, "last_tool_definitions", []) or []
            )
            tools = []
            for definition in definitions:
                name = str(definition.get("name") or "")
                source, server = "builtin", None
                if name.startswith("mcp__"):
                    parts = name.split("__", 2)
                    if len(parts) == 3:
                        source, server = "mcp", parts[1]
                tools.append({
                    "name": name,
                    "description": str(definition.get("description") or "")[:2000],
                    "source": source,
                    "server": server,
                    "schema": definition.get("inputSchema") or {},
                })
            return {
                "jsonrpc": "2.0",
                "result": {"count": len(tools), "tools": tools},
                "id": req_id,
            }

        elif method == "agent.update_config":
            """更新配置参数并保存到文件"""
            key = params.get("key", "")
            value = params.get("value")
            if not key:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Missing 'key' parameter"},
                    "id": req_id
                }
            try:
                # 尝试转换数值类型
                if isinstance(value, str):
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    else:
                        try:
                            if "." in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except (ValueError, TypeError):
                            pass  # 保持字符串
                old_value = config.set(key, value)
                config.save()
                logger.info(f"Config updated: {key} = {value} (was: {old_value})")
                return {
                    "jsonrpc": "2.0",
                    "result": {"success": True, "key": key, "old_value": old_value, "new_value": value},
                    "id": req_id
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                    "id": req_id
                }

        elif method == "agent.cancel_background_task":
            task_id = params.get("task_id")
            if task_id is None:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Missing 'task_id' parameter"},
                    "id": req_id,
                }
            try:
                cancelled = _manager.bg_tasks.cancel_task(int(task_id))
                return {
                    "jsonrpc": "2.0",
                    "result": {"cancelled": cancelled, "task_id": task_id},
                    "id": req_id,
                }
            except Exception as e:
                logger.error("Cancel background task failed: %s", e, exc_info=True)
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                    "id": req_id,
                }

        elif method == "agent.interrupt":
            # 仅在 agent 正在处理消息或队列里有待处理消息时才标记中断
            # （has_pending 覆盖"消息已入队但处理线程还没进入 process_message"的窗口，
            # 此时不设标志的话，用户想取消的消息会照常跑起来）
            session_id = params.get("session_id", "default")
            session = _manager.get_session(session_id)
            if session._processing or session.has_pending():
                session._interrupt_event.set()
                logger.info("Interrupt requested by user (session=%s)", session_id)
                interrupted = True
            else:
                interrupted = False
            return {
                "jsonrpc": "2.0",
                "result": {"interrupted": interrupted},
                "id": req_id
            }

        elif method == "agent.confirm_response":
            # 用户对危险命令确认的回复——立即放行/拒绝等待中的工具线程
            from Tools.confirmation_manager import confirmation_manager
            confirm_id = str(params.get("confirm_id", ""))
            approved = bool(params.get("approved", False))
            found = confirmation_manager.resolve(confirm_id, approved)
            logger.info("Confirmation %s resolved: approved=%s found=%s", confirm_id, approved, found)
            return {
                "jsonrpc": "2.0",
                "result": {"ok": found, "approved": approved},
                "id": req_id
            }

        elif method == "agent.user_question_response":
            # 用户对 AskUser 提问的回答——放行等待中的工具线程
            from Tools.question_manager import question_manager
            request_id = str(params.get("request_id", ""))
            answer = str(params.get("answer", ""))
            found = question_manager.resolve(request_id, answer)
            logger.info("User question %s resolved: found=%s", request_id, found)
            return {
                "jsonrpc": "2.0",
                "result": {"ok": found, "request_id": request_id},
                "id": req_id
            }

        elif method == "voice.get_status":
            from Gateway import voice_service
            return {
                "jsonrpc": "2.0",
                "result": voice_service.get_status(),
                "id": req_id,
            }

        elif method == "voice.set_config":
            from Gateway import voice_service
            p = {}
            for k in ("mic_consent", "asr_enabled", "wakeup_enabled"):
                if k in params:
                    p[k] = bool(params[k])
            status = voice_service.set_config(**p)
            # 根据开关启停唤醒监听（仅唤醒模式需要长驻监听器）
            if status.get("wakeup_enabled"):
                ok, msg = voice_service.start_listener(emit_voice_event)
                status["listener"] = {"ok": ok, "message": msg}
            else:
                voice_service.stop_listener()
                status["listener"] = {"ok": False, "message": "已停止"}
            return {
                "jsonrpc": "2.0",
                "result": status,
                "id": req_id,
            }

        elif method == "voice.download_model":
            from Gateway import voice_service
            started, msg = voice_service.download_model(emit_voice_event)
            return {
                "jsonrpc": "2.0",
                "result": {"started": started, "message": msg},
                "id": req_id,
            }

        elif method == "browser_llm.get_status":
            from Gateway import browser_llm_config
            return {
                "jsonrpc": "2.0",
                "result": browser_llm_config.get_status(),
                "id": req_id,
            }

        elif method == "browser_llm.set_config":
            # 浏览器 LLM 抽取开关：写 browser-use 的 config.json 并重连 MCP server 生效
            from Gateway import browser_llm_config
            enabled = bool(params.get("enabled", False))
            try:
                status = browser_llm_config.set_enabled(enabled)
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "result": {"success": False, "message": str(e), **browser_llm_config.get_status()},
                    "id": req_id,
                }
            # browser-use 是独立子进程且配置在启动时读取——必须重连才生效
            reconnect_msg = ""
            ok, err = reconnect_mcp_server("browser-use", timeout=30)
            if ok:
                reconnect_msg = "，browser-use 已重连生效"
            elif err and "not configured" not in str(err):
                reconnect_msg = f"，但重连失败（{err}），重启后生效"
            return {
                "jsonrpc": "2.0",
                "result": {
                    "success": True,
                    "message": ("已开启" if enabled else "已关闭") + f"浏览器 LLM 抽取{reconnect_msg}",
                    **status,
                },
                "id": req_id,
            }

        elif method == "model.get_status":
            llm_cfg = config.get("model.large-language-model", {}) or {}
            api_key_env = llm_cfg.get("api_key_env", "")
            api_key = os.getenv(api_key_env) if api_key_env else ""
            masked = ""
            if api_key:
                masked = api_key[:6] + "***" + api_key[-4:] if len(api_key) > 12 else "***"
            return {
                "jsonrpc": "2.0",
                "result": {
                    "model": llm_cfg.get("model", ""),
                    "base_url": llm_cfg.get("base_url", ""),
                    "provider": llm_cfg.get("provider", "auto"),
                    "api_key_env": api_key_env,
                    "api_key_set": bool(api_key),
                    "api_key_masked": masked,
                    "history": config.get("model.model_history", []) or [],
                },
                "id": req_id,
            }

        elif method == "model.set_config":
            from Agent.llm import reload_global_llm
            model = params.get("model")
            base_url = params.get("base_url")
            api_key = params.get("api_key")
            new_api_key_env = params.get("api_key_env")
            changed = []
            # 事务性：先记住旧值，热重载失败时整体回滚，避免 config.yaml
            # 停留在"已切换但实际没生效"的半套状态
            old_llm_cfg = dict(config.get("model.large-language-model", {}) or {})
            if model:
                config.set("model.large-language-model.model", model)
                changed.append("model")
            if base_url:
                config.set("model.large-language-model.base_url", base_url)
                changed.append("base_url")
            if new_api_key_env:
                config.set("model.large-language-model.api_key_env", new_api_key_env)
                changed.append("api_key_env")
            api_key_env = config.get("model.large-language-model.api_key_env", "ANTHROPIC_API_KEY")
            if api_key and not new_api_key_env and (model or base_url):
                # 手动切换且未显式指定目标 env 时，按目标匹配的历史条目决定
                # key 写进哪个变量——避免把新厂商的 key 写进当前厂商的变量
                # 名下，覆盖掉原厂商的密钥
                for h in (config.get("model.model_history", []) or []):
                    if not isinstance(h, dict) or not h.get("api_key_env"):
                        continue
                    if (h.get("model") == model) and (not base_url or h.get("base_url") == base_url):
                        api_key_env = h["api_key_env"]
                        config.set("model.large-language-model.api_key_env", api_key_env)
                        changed.append("api_key_env")
                        break
            if api_key:
                _set_dotenv_value(api_key_env, api_key)
                os.environ[api_key_env] = api_key
                changed.append("api_key")
            ok, err = reload_global_llm()
            if not ok:
                # 回滚 config（.env 与运行中的 os.environ 不回滚：写掉的 key
                # 仍是有效密钥，留在新变量名下无害）；LLMCore 保持旧客户端，
                # 对话不中断——失败即回退到原模型继续工作
                for key, val in old_llm_cfg.items():
                    config.set(f"model.large-language-model.{key}", val)
                config.save()
                kind, msg = _classify_switch_failure(err)
                fallback = (old_llm_cfg.get("model") or "")
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "success": False,
                        "error_kind": kind,
                        "message": msg,
                        "fallback_model": fallback,
                    },
                    "id": req_id,
                }
            # 重载成功后才记入最近使用列表（同 model+base_url 去重置顶，
            # 最多 8 条），并随条目记录 api_key_env——不同厂商的 key 各存
            # 各的环境变量，回切时自动换用，互不覆盖
            if model or base_url:
                llm_cfg = config.get("model.large-language-model", {}) or {}
                entry = {
                    "model": llm_cfg.get("model", ""),
                    "base_url": llm_cfg.get("base_url", ""),
                    "api_key_env": llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"),
                }
                history = [
                    h for h in (config.get("model.model_history", []) or [])
                    if isinstance(h, dict)
                    and (h.get("model"), h.get("base_url")) != (entry["model"], entry["base_url"])
                ]
                history.insert(0, entry)
                config.set("model.model_history", history[:8])
                config.save()
            logger.info("Model config updated: %s", ", ".join(changed) if changed else "(none)")
            return {
                "jsonrpc": "2.0",
                "result": {"success": True, "changed": changed},
                "id": req_id,
            }

        else:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                },
                "id": req_id
            }

    except Exception as e:
        logger.error(f"Error dispatching {method}: {e}", exc_info=True)

        # Emit error event
        write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "agent.error",
                "payload": {"message": str(e)}
            }
        })

        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": str(e)
            },
            "id": req_id
        }


# stdio 前端（TUI spawn 的本进程）是否已活跃发过请求——用于拒绝第二个 TUI 的
# TCP attach（一旦 stdio TUI 在用，事件流归它，不能被后到的 attach 抢走）
_STDIO_ACTIVE = threading.Event()


def _stdin_reader():
    """Background thread: read JSON-RPC requests from stdin"""
    global _bg_reader_running
    _bg_reader_running = True

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            _STDIO_ACTIVE.set()
            _request_queue.put((request, write_json))
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            write_json({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            })

    _bg_reader_running = False
    logger.info("stdin EOF")


# ── TUI attach：TCP JSON-RPC 服务（守护进程侧）+ 代理模式（TUI 侧）────
#
# 场景：QQ 守护进程常驻时用户再开 TUI——TUI spawn 的 gateway_entry 探测到
# 端口已被占用，自动降级为 stdio↔TCP 代理，对接现有守护；TUI 关闭时代理
# 发 tui.shutdown，守护随之退出（不留守户进程）。


class TuiRpcServer:
    """守护进程的本地 TCP JSON-RPC 端点（127.0.0.1:gateway.rpc_port）。

    - 普通连接（探测）直接关闭无副作用；只有发送 tui.attach 的连接才算
      "已对接 TUI"：此后 tui channel 的事件流转到该 socket
    - 已对接 TUI 断开 → 守护整体退出（gateway.tui_attach_shutdown，默认开）
    """

    def __init__(self, port, shutdown_on_detach=True):
        self.port = int(port)
        self.shutdown_on_detach = bool(shutdown_on_detach)
        self._lock = threading.Lock()
        self._attached_sock = None   # 当前对接中的 TUI socket
        self._server = None

    def start(self):
        """绑定并开始监听。端口即单实例锁：Windows 上必须用
        SO_EXCLUSIVEADDRUSE，绝不能用 SO_REUSEADDR（后者允许双绑定，
        会导致两个 gateway 抢同一端口/事件）。"""
        import socket
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):      # Windows
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:                                            # POSIX
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(2)
            self._server = srv
        except OSError as e:
            logger.warning("TuiRpcServer: port %s bind failed: %s", self.port, e)
            return False
        threading.Thread(target=self._accept_loop, daemon=True,
                         name="tui-rpc-server").start()
        logger.info("TuiRpcServer: listening on 127.0.0.1:%d", self.port)
        return True

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._serve_client, args=(conn,),
                             daemon=True, name="tui-rpc-client").start()

    def _send_line(self, sock, obj):
        try:
            sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except OSError:
            return False

    def _reroute_tui_channel(self, sock):
        """把 default session 的 tui channel 事件流改道到对接的 socket。"""
        session = _manager.get_session("default") if _manager else None
        swapped = False
        for ch in (session.channels if session else []):
            if ch.channel_type == "tui":
                ch._transport = lambda obj, _s=sock: self._send_line(_s, obj)
                swapped = True
        return swapped

    def _restore_tui_channel(self):
        session = _manager.get_session("default") if _manager else None
        for ch in (session.channels if session else []):
            if ch.channel_type == "tui":
                ch._transport = write_json

    def _serve_client(self, conn):
        global _bg_reader_running
        attached = False
        try:
            conn.settimeout(300)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        req = json.loads(text)
                    except json.JSONDecodeError:
                        self._send_line(conn, {
                            "jsonrpc": "2.0",
                            "error": {"code": -32700, "message": "Parse error"},
                            "id": None})
                        continue
                    method = req.get("method", "")

                    if method == "tui.attach":
                        with self._lock:
                            already = self._attached_sock is not None
                            if not already:
                                self._attached_sock = conn
                        if _STDIO_ACTIVE.is_set():
                            # stdio 型 TUI 正在使用本 daemon（它就是本进程的
                            # 父前端）——不允许 TCP attach 抢走事件流
                            with self._lock:
                                if self._attached_sock is conn:
                                    self._attached_sock = None
                            self._send_line(conn, {
                                "jsonrpc": "2.0",
                                "result": {"attached": False,
                                           "error": "a TUI is already attached (stdio)"},
                                "id": req.get("id")})
                            return
                        if already:
                            self._send_line(conn, {
                                "jsonrpc": "2.0",
                                "result": {"attached": False,
                                           "error": "another TUI already attached"},
                                "id": req.get("id")})
                            continue
                        attached = True
                        self._reroute_tui_channel(conn)
                        self._send_line(conn, {
                            "jsonrpc": "2.0", "result": {"attached": True},
                            "id": req.get("id")})
                        # 守护的 gateway.ready 早在启动时发过了——对接上的 TUI
                        # 等不到它界面会永远卡"连接中"，补发一份
                        self._send_line(conn, {
                            "jsonrpc": "2.0", "method": "event",
                            "params": {"type": "gateway.ready", "payload": {}}})
                        logger.info("TuiRpcServer: TUI attached via TCP")
                        continue

                    if method == "tui.shutdown":
                        if self.shutdown_on_detach:
                            self._send_line(conn, {
                                "jsonrpc": "2.0", "result": {"shutting_down": True},
                                "id": req.get("id")})
                            logger.info("TuiRpcServer: shutdown requested by TUI")
                            _bg_reader_running = False   # 主循环退出 → finally 走完整收尾
                            return
                        # 常驻模式：TUI 关闭只 detach，守护继续跑（连接随后由对端关闭 → finally 清理）
                        self._send_line(conn, {
                            "jsonrpc": "2.0",
                            "result": {"shutting_down": False, "detached": True},
                            "id": req.get("id")})
                        logger.info("TuiRpcServer: TUI detached (daemon stays alive)")
                        continue

                    # 普通 RPC → 主循环 dispatch，应答经本 socket 回
                    _request_queue.put((req, lambda o, _c=conn: self._send_line(_c, o)))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            if attached:
                with self._lock:
                    if self._attached_sock is conn:
                        self._attached_sock = None
                self._restore_tui_channel()
                if self.shutdown_on_detach:
                    logger.info("TuiRpcServer: attached TUI disconnected, "
                                "shutting gateway down")
                    _bg_reader_running = False


def _probe_existing_gateway(port, timeout=1.0) -> bool:
    """探测 127.0.0.1:port 是否有常驻守护的 TuiRpcServer。"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _run_proxy_mode(port) -> int:
    """TUI spawn 进来的 gateway_entry 发现守护已在 → 本进程退化为代理。

    stdio(TUI) ↔ TCP(守护)。stdin EOF（TUI 关闭）→ 发 tui.shutdown →
    守护退出 → 代理退出。
    """
    import socket
    logger.info("existing gateway detected, running as TUI proxy")
    try:
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=5)
    except OSError as e:
        logger.warning("proxy connect failed: %s, fallback to standalone", e)
        return 1   # 探测竞态（守护刚好死了）→ 调用方回退正常启动

    def _sock_send(obj):
        try:
            sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    _sock_send({"jsonrpc": "2.0", "method": "tui.attach", "id": "attach"})

    # 必须先确认 attach 被接受（否则说明守护已被另一个 TUI 占用）
    import socket as _socket_mod
    try:
        sock.settimeout(5)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        sock.settimeout(None)
        first_line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        resp = json.loads(first_line) if first_line.strip() else {}
    except Exception:
        resp = {}
    if resp.get("result", {}).get("attached") is not True:
        err = resp.get("result", {}).get("error", "attach rejected")
        logger.warning("TUI proxy: attach rejected (%s)", err)
        try:
            sock.close()
        except OSError:
            pass
        return 2   # 另一个 TUI 已占用

    stop = threading.Event()
    # 若首行缓冲里还有剩余字节（事件），交给 pump 前置输出
    _pending_first_chunk = buf.split(b"\n", 1)[1] if b"\n" in buf else b""
    if _pending_first_chunk:
        sys.stdout.write(_pending_first_chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()

    def _pump_sock_to_stdout():
        try:
            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    sys.stdout.write(line.decode("utf-8", errors="replace") + "\n")
                    sys.stdout.flush()
        except OSError:
            pass
        finally:
            stop.set()

    threading.Thread(target=_pump_sock_to_stdout, daemon=True,
                     name="proxy-sock-reader").start()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sock.sendall((line + "\n").encode("utf-8"))
    except OSError:
        pass

    # stdin EOF = TUI 关闭 → 顺带关掉对接的守护
    _sock_send({"jsonrpc": "2.0", "method": "tui.shutdown", "id": "bye"})
    try:
        sock.shutdown(socket.SHUT_WR)
        deadline = time.time() + 5
        while time.time() < deadline and not stop.is_set():
            time.sleep(0.05)
    except OSError:
        pass
    finally:
        sock.close()
    logger.info("TUI proxy exit (gateway shut down)")
    return 0


def inject_voice_message(transcript: str) -> bool:
    """把语音识别结果作为带标签的 user 消息注入会话，并触发处理。"""
    transcript = (transcript or "").strip()
    if not transcript:
        logger.warning("voice: empty transcript, skip inject")
        return False
    session = _manager.default_session
    session.enqueue(ChannelMessage(
        text=f"[voice-recognize] {transcript}",
        channel_type="voice",
        channel_id="voice_wakeup",
        meta={"note": "ASR 转写，可能含同音误识别"},
    ))
    logger.info("voice: injected transcript into agent (%d chars)", len(transcript))
    return True


class _VoiceHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/message":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body or b"{}")
            transcript = str(data.get("message", "") or "")
        except Exception as exc:
            logger.warning("voice: bad HTTP payload: %s", exc)
            transcript = ""
        ok = inject_voice_message(transcript)
        resp = json.dumps({"ok": ok}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


def start_voice_http_server(port: int = 8999):
    """本地 HTTP 输入端点：POST /message 接收语音转写。"""
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _VoiceHTTPHandler)
    except OSError as exc:
        logger.warning("voice: HTTP endpoint failed to bind %s: %s", port, exc)
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="voice-http").start()
    logger.info("voice: HTTP endpoint listening on 127.0.0.1:%d", port)
    return server


def main():
    """Main entry point"""
    global _manager, _bg_reader_running

    # --daemon：常驻守护模式（goharu gateway start）。不读 stdin，跑到
    # SIGTERM/SIGINT 或 RPC gateway.shutdown 为止；TUI 断开不退出。
    daemon_mode = "--daemon" in sys.argv
    if daemon_mode:
        def _on_term(signum, _frame):
            global _bg_reader_running
            logger.info("daemon: received signal %s, shutting down", signum)
            _bg_reader_running = False
        for _sig in ("SIGTERM", "SIGINT"):
            if hasattr(signal, _sig):
                try:
                    signal.signal(getattr(signal, _sig), _on_term)
                except Exception:
                    pass

    rpc_port = int(config.get("gateway.rpc_port", 8791) or 0)

    # 单实例决策：端口先到先得。
    # - 探测到已有守护 → 本进程退化为 TUI 代理（TUI 关闭时顺带关掉守护）
    # - 否则**立即独占绑定端口再去做初始化**——消除"守护启动中(25s)
    #   探测失败 → TUI 又起第二个完整 gateway"的竞态（2026-10-31 双实例事故）
    if rpc_port > 0:
        rpc_server = None
        deadline = time.time() + 60
        while True:
            if _probe_existing_gateway(rpc_port, timeout=0.5):
                code = _run_proxy_mode(rpc_port)
                if code == 0:
                    sys.exit(0)
                if code == 2:
                    # 另一个 TUI 已占用守护——不能再起第二个 gateway 抢 QQ 事件
                    logger.error("another TUI is already using the running gateway")
                    write_json({
                        "jsonrpc": "2.0", "method": "event",
                        "params": {"type": "gateway.init_error",
                                   "payload": {"message":
                                               "已有另一个 TUI 连接着后台网关，请先关闭那个 TUI"}}})
                    sys.exit(1)
                break   # 代理连接失败（对端刚好退出）→ 继续尝试本地绑定
            candidate = TuiRpcServer(
                rpc_port,
                shutdown_on_detach=(False if daemon_mode else _env_flag(
                    "GOHARU_TUI_ATTACH_SHUTDOWN",
                    bool(config.get("gateway.tui_attach_shutdown", False)),
                )),
            )
            if candidate.start():        # 绑定即胜出：我是唯一实例
                rpc_server = candidate
                break
            if time.time() > deadline:
                logger.warning("RPC port busy, running without attach endpoint")
                rpc_server = None
                break
            time.sleep(1)                # 对手刚绑上但还没 listen，稍等再探
    else:
        rpc_server = None

    root_state = {"stop": None, "init_error": None}

    try:
        # 装配 SessionManager（共享服务 + 全部 session + channels）
        loop = _get_main_loop()
        _manager = SessionManager(tui_transport=write_json, loop=loop)

        # ── 常驻根任务：MCP 会话是 anyio 结构化并发建的，宿主任务必须一直
        # 活着，否则 task group 半坏——表现：启动时 list_tools 正常，之后
        # CallTool 请求永远写不进子进程 stdin（2026-10-31 netease 全线挂死
        # 的根因：run_until_complete(_manager.start()) 返回即宿主任务结束）。
        # 方案：事件循环跑在独立线程，一个根协程 start → 等停止 → shutdown。
        root_started = threading.Event()

        def _loop_runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(target=_loop_runner, daemon=True,
                         name="main-loop").start()
        while not loop.is_running():
            time.sleep(0.02)

        async def _async_root():
            root_state["stop"] = asyncio.Event()
            try:
                await _manager.start()
            except Exception as e:
                root_state["init_error"] = e
            finally:
                root_started.set()
            await root_state["stop"].wait()
            await _manager.shutdown()

        asyncio.run_coroutine_threadsafe(_async_root(), loop)
        root_started.wait(timeout=300)
        if root_state["init_error"] is None and not (_manager and _manager.sessions):
            root_state["init_error"] = RuntimeError("initialization timed out")
        if root_state["init_error"] is not None:
            init_err = root_state["init_error"]
            error_msg = str(init_err)
            log_crash(f"Gateway initialization failed: {error_msg}", sys.exc_info())
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "gateway.init_error",
                    "payload": {"message": error_msg}
                }
            })
            sys.exit(1)

        logger.info("Gateway session initialized")

        # 启动本地 HTTP 输入端点（语音转写由此注入 agent）
        try:
            voice_port = int(config.get("voice.http_port", 8999))
            start_voice_http_server(voice_port)
        except Exception as exc:
            logger.warning("voice: HTTP endpoint start failed: %s", exc)

        # 若上次会话开启了语音唤醒，启动时自动恢复长驻监听
        try:
            from Gateway import voice_service
            if config.get("voice.wakeup_enabled", False):
                ok, msg = voice_service.start_listener(emit_voice_event)
                logger.info("voice: auto-start listener → ok=%s %s", ok, msg)
        except Exception as exc:
            logger.warning("voice: auto-start failed: %s", exc)

        # Send ready event
        if not write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": {}}
        }):
            log_crash("Failed to send gateway.ready event")
            sys.exit(1)

        # Start background stdin reader —— 必须先于初始 token 统计：
        # 首次记忆检索要等 embedding 模型冷加载（实测可达 56s），若先算 token
        # 统计，TUI 刚发出来的 agent.get_history 等请求会在管道里晾到超时。
        if daemon_mode:
            _bg_reader_running = True   # 无 stdin reader：常驻直到 signal / RPC gateway.shutdown
            logger.info("daemon mode: running without stdin reader")
        else:
            reader_thread = threading.Thread(target=_stdin_reader, daemon=True, name="stdin-reader")
            reader_thread.start()

        # 若 TUI 在初始化完成前就已 attach（启动竞态窗口内），此时 channel
        # 才刚装配出来——补一次事件流改道
        if rpc_server is not None and rpc_server._attached_sock is not None:
            rpc_server._reroute_tui_channel(rpc_server._attached_sock)
            logger.info("TuiRpcServer: re-routed events for early-attached TUI")

        # Emit initial token stats —— 放后台线程，不阻塞请求主循环
        def _emit_initial_token_stats():
            try:
                stats = _manager.default_session.calculate_token_stats()
            except Exception as exc:
                logger.warning("initial token stats failed: %s", exc)
                return
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "token.stats",
                    "payload": stats
                }
            })
            logger.info("Gateway ready, token stats: current=%s, cache_ratio=%.2f%%",
                        stats["current_tokens"], stats["prompt_cache_ratio"] * 100)

        threading.Thread(
            target=_emit_initial_token_stats, daemon=True, name="initial-token-stats"
        ).start()

        # Process requests from queue
        while _bg_reader_running:
            try:
                request, reply = _request_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Dispatch request
            method = request.get("method", "?")
            response = dispatch(request)
            if response is not None:
                if not reply(response):
                    if daemon_mode:
                        # 守护模式：客户端断开是常态（TUI detach / CLI 探活超时关连接），
                        # 单个 reply 失败绝不能让守护退出——记日志继续服务。
                        logger.warning("reply failed (client gone) — daemon keeps running")
                        continue
                    log_crash("Failed to write response (broken pipe)")
                    sys.exit(0)

    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down...")
    except Exception as e:
        log_crash(f"Unhandled exception: {e}", sys.exc_info())
        sys.exit(1)
    finally:
        global _SHUTTING_DOWN
        # Mark processing as stopped so pending daemon threads don't
        # keep submitting LLM queries during executor shutdown
        _SHUTTING_DOWN = True

        # Cleanup gateway：通知常驻根任务自己在 loop 线程里跑 shutdown
        #（AsyncExitStack/MCP 拆除必须发生在进入它的同一任务里，跨任务会报
        # "cancel scope in a different task"）
        if _manager:
            loop = _get_main_loop()
            try:
                stop_ev = root_state.get("stop") if "root_state" in locals() else None
                if stop_ev is not None and loop.is_running():
                    loop.call_soon_threadsafe(stop_ev.set)
                    time.sleep(5)     # 根任务在此窗口内完成 MCP/工具拆除
                if loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
                    time.sleep(0.5)
                if not loop.is_running():
                    loop.close()
            except Exception as exc:
                logger.warning("gateway shutdown cleanup failed: %s", exc)

        # Shutdown the persistent LLM executor last
        try:
            from Agent.api import shutdown_llm_executor
            shutdown_llm_executor(wait=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
