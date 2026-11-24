"""Channel 基类 —— 会话消息的入口/出口抽象。

channel 是会话（AgentSession）与外部世界（TUI / QQBot / HTTP / WhatsApp…）
之间的适配器。设计约束：

- **N:1 绑定**：一个 channel 实例专属一个 session（config.yaml 声明）。
- **单向依赖**：channel 不 import session 类；入站消息经 `bind()` 注入的
  Callable 上送，channel 只认 ChannelMessage 与 Callable 两个概念。
- **能力声明**：`capabilities` 声明可选出站能力（thinking/tool_use/
  tool_result/event）；answer 是所有 channel 的隐含必要能力。
  session 轮发（broadcast）时按 capability 过滤——没声明就不调。
- **send 不得阻塞**：session 的 turn 循环在调用线程上同步调用 send_*，
  channel 必须快速返回（自己的慢 IO 放内部队列/线程）。
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# ask 挂起问题的本地 TTL（比 AskUser 工具默认 300s 略长，超时自然失效）
ASK_PENDING_TTL = 330


@dataclass
class ChannelMessage:
    """入站消息（channel → session）。

    priority:
      - "normal"（默认）：进队列按批次处理
      - "high"：**插到队首 + 打断当前轮**——智能体当前步骤立即中断
        （同 ESC 语义），随后马上分析这条消息
    """

    text: str
    channel_type: str            # "tui" / "qqbot" / "http" ...
    channel_id: str              # 实例 id（config 里声明的那个）
    meta: dict = field(default_factory=dict)   # 渠道附加信息（群号/用户/语音标记等）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    priority: str = "normal"     # "high" | "normal"


class Channel:
    """channel 插件基类。

    子类必须：
    - 设置类属性 channel_type（registry 的注册 key）
    - 实现 send_answer（必要出站）
    子类可选：
    - 覆写 capabilities 声明支持的可选出站（默认全不支持）
    - 实现 send_thinking / send_tool_use / send_tool_result / send_event
    - 覆写 start/stop 管理自己的监听资源（线程/HTTP server/长连接）
    """

    channel_type: str = "base"
    # 可选出站能力集合：{"thinking", "tool_use", "tool_result", "event"} 的子集
    capabilities: frozenset = frozenset()

    def __init__(self, channel_id: str, params: dict | None = None):
        self.channel_id = str(channel_id)
        self.params = dict(params or {})
        self._on_message = None

    # ── 入站（channel → session）────────────────────────────────────

    def bind(self, on_message):
        """SessionManager 注入上送回调：Callable[[ChannelMessage], None]。"""
        self._on_message = on_message

    def emit_inbound(self, text: str, meta: dict | None = None,
                     priority: str = "normal"):
        """channel 子类收到外部消息时调用，包装成 ChannelMessage 上送 session。

        priority="high"：插队 + 打断智能体当前步骤（适用于用户喊停/改需求）；
        默认 "normal" 按队列处理。
        """
        if self._on_message is None:
            logger.warning(
                "channel %s#%s received message before bind, dropped",
                self.channel_type, self.channel_id,
            )
            return
        self._on_message(ChannelMessage(
            text=str(text),
            channel_type=self.channel_type,
            channel_id=self.channel_id,
            meta=dict(meta or {}),
            priority=str(priority or "normal"),
        ))

    # ── 生命周期 ─────────────────────────────────────────────────────

    def start(self):
        """启动监听/泵（线程安全，可重入）。"""

    def stop(self):
        """停止监听，释放资源。"""

    # ── 出站（session → channel，轮发目标；不得阻塞）──────────────────

    def send_answer(self, text: str, meta: dict | None = None):
        """智能体最终回答（必要接口，子类必须实现）。"""
        raise NotImplementedError

    def send_thinking(self, text: str | None, meta: dict | None = None):
        """智能体 thinking：text=None 表示"开始思考"（spinner 语义）。"""

    def send_tool_use(self, name: str, args: dict, call_id: str = "",
                      meta: dict | None = None):
        """智能体发起工具调用。meta 可携带 {"step": n} 等会话上下文。"""

    def send_tool_result(self, name: str, result: str, call_id: str = "",
                         is_error: bool = False, meta: dict | None = None):
        """工具执行结果。"""

    # ── 健康状态（gateway status / TUI 设置面板的"是否配通"展示）─────────

    def health(self) -> dict:
        """连通/配置状态：{ok: bool, state: str, detail: str}。

        默认实现 = 无长连接的 channel 一律 ready；长连接型（harunet/qqbot）
        覆写为在线/连接中/失败三态并附最近错误原因。
        """
        return {"ok": True, "state": "ready", "detail": ""}

    def send_event(self, event_type: str, payload: dict):
        """通用事件逃生口（token.stats / 确认请求 / 后台任务面板等）。

        只有声明了 "event" 能力的 channel 才会被调到——富客户端（TUI）
        靠它保留完整事件协议；简单 channel 不受打扰。
        """

    def send_text_output(self, text: str, meta: dict | None = None):
        """模型的中间文本块（step 中的 text content，非最终 answer）。

        声明 "text_output" 能力的 channel 才会被调到——聊天型渠道
        （QQBot）用它把模型每段文字输出都送到用户眼前；TUI 走
        agent.text_content 事件面板，不受影响。
        """

    # ── 弹窗类交互：危险命令确认（可选接口）─────────────────────────
    #
    # 与 ask 三范式同样的退化策略：基类默认实现是纯文本 y/n 问答，
    # 子类可按需覆写为原生弹窗/按钮（QQBot 用回调按钮）。

    def bind_confirm(self, resolve_fn):
        """SessionManager 注入：fn(confirm_id, approved: bool)。"""
        self._resolve_confirmation = resolve_fn

    def confirm_request(self, confirm_id: str, command: str,
                        description: str = "", timeout: int = 300):
        """请求用户确认高危操作（默认文本退化：回复 y/n）。"""
        self._pending_confirm = {
            "id": str(confirm_id),
            "expires": time.time() + max(10, int(timeout or 300)) + 30,
        }
        self.send_answer(
            f"⚠️ 高危命令确认（{int(timeout or 300)}s 内有效）\n"
            f"命令：{command}\n{description}\n"
            f"回复 y 允许 / n 拒绝")

    def try_consume_as_confirmation(self, msg: "ChannelMessage") -> bool:
        """入站消息先经此口：是危险命令确认的 y/n 则拦截消费。"""
        pending = getattr(self, "_pending_confirm", None)
        if not pending:
            return False
        if time.time() > pending["expires"]:
            self._pending_confirm = None
            return False
        text = (msg.text or "").strip().lower()
        if text in ("y", "yes", "允许", "同意", "ok", "确认"):
            approved = True
        elif text in ("n", "no", "拒绝", "否", "取消", "cancel"):
            approved = False
        else:
            return False   # 不是确认回答，正常路由给智能体
        self._pending_confirm = None
        resolve = getattr(self, "_resolve_confirmation", None)
        if resolve is not None:
            resolve(pending["id"], approved)
        return True

    def send_file(self, path_or_url: str, caption: str = "",
                  meta: dict | None = None,
                  force_type: str | None = None) -> bool:
        """发送文件（可选接口）。path_or_url 是本机路径或公网 URL。

        force_type: 可选，覆盖自动推断的媒体类型（"image"/"video"/"file"）——
        默认仅图片/视频会自动识别，其余（含音频）一律按文件发送。

        返回 True=已受理发送（异步执行，失败走日志）；False=不支持或
        参数不可用（文件不存在/超限）。声明 "file" 能力的 channel
        才会被 SendFile 工具路由到。
        """
        logger.debug("channel %s does not support send_file", self)
        return False

    # ── 提问三范式（AskUser 的 channel 适配；全部可选）────────────────
    #
    # 三个接口默认实现都是**纯文本退化**：把问题渲染成编号列表，用户的
    # 下一条消息被拦截解析为答案。子类按需覆写为原生 UI（如 QQBot 的
    # 回调按钮）；覆写哪个人性化哪个人，没覆写的自动落回文本问答。
    # 答案经 bind_answer 注入的 resolve 回送（遵循 AskUser 契约：
    # 单问单选=选项 label 字符串；含多选或批量=JSON {"answers":[...]}）。

    def bind_answer(self, resolve_fn):
        """SessionManager 注入答案回送：fn(request_id, answer: str)。"""
        self._resolve_answer = resolve_fn

    def ask_questions(self, request_id: str, questions: list, meta: dict | None = None):
        """提问入口（SessionManager 调用）。单题分派到三范式；批量走文本。"""
        if len(questions) == 1:
            q = questions[0]
            labels = _option_labels(q)
            if not labels:
                self.ask_text(q.get("question", ""), request_id, meta)
            elif q.get("multi_select"):
                self.ask_multi(q.get("question", ""), labels, request_id, meta)
            else:
                self.ask_single(q.get("question", ""), labels, request_id, meta)
            return
        # 批量：文本退化（一次列出全部，按题序分号分隔作答）
        modes, lines = [], []
        for i, q in enumerate(questions, 1):
            labels = _option_labels(q)
            mode = "multi" if q.get("multi_select") else ("single" if labels else "text")
            modes.append(mode)
            zh = {"single": "单选", "multi": "多选", "text": "填空"}[mode]
            lines.append(f"【问题 {i}·{zh}】{q.get('question', '')}")
            for j, o in enumerate(labels, 1):
                lines.append(f"  {j}. {o}")
        lines.append("请按题序回复，题与题之间用分号（;）隔开；多选题内用逗号分隔编号；回复 q 放弃")
        self._set_ask_pending(request_id, questions, modes, meta)
        self.send_answer("\n".join(lines))

    def ask_single(self, question: str, options: list, request_id: str,
                   meta: dict | None = None):
        """单选（默认文本退化：回复编号或选项文字，自由文本也接受）。"""
        lines = [str(question), "（单选）请回复编号或选项："]
        lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append("回复 q 放弃")
        self._set_ask_pending(request_id, [{"question": question, "options": options}],
                              ["single"], meta)
        self.send_answer("\n".join(lines))

    def ask_multi(self, question: str, options: list, request_id: str,
                  meta: dict | None = None):
        """多选（默认文本退化：逗号分隔多个编号，如“1,3”）。"""
        lines = [str(question), "（多选）请回复编号，多个用逗号分隔："]
        lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append("回复 q 放弃")
        self._set_ask_pending(request_id, [{"question": question, "options": options,
                                            "multi_select": True}],
                              ["multi"], meta)
        self.send_answer("\n".join(lines))

    def ask_text(self, question: str, request_id: str, meta: dict | None = None):
        """填空（默认文本退化：下一条消息即答案）。"""
        self._set_ask_pending(request_id, [{"question": question}], ["text"], meta)
        self.send_answer(f"{question}\n（请直接回复内容；回复 q 放弃）")

    def try_consume_as_answer(self, msg: "ChannelMessage") -> bool:
        """入站消息先经此口：是挂起问题的答案则拦截消费（不进 agent 队列）。

        返回 True = 已消费；False = 不是答案，正常路由。
        解析失败会消费并重发格式提示（避免把乱答喂给 agent）。
        """
        pending = getattr(self, "_ask_pending", None)
        if not pending:
            return False
        if time.time() > pending["expires"]:
            self._ask_pending = None
            return False
        conv = self._ask_conversation_key(msg)
        if pending.get("conversation") and conv and conv != pending["conversation"]:
            return False   # 别的会话的消息不作答
        text = (msg.text or "").strip()
        if not text:
            return False
        if text.lower() in ("q", "Q", "取消", "放弃"):
            self._ask_pending = None
            self._dispatch_answer(pending["request_id"], pending["questions"],
                                  [None] * len(pending["questions"]))
            return True
        answers = _parse_ask_reply(text, pending)
        if answers is None:
            self.send_answer("没看懂这个回答，请按上面的格式回复（回复 q 放弃）")
            return True
        self._ask_pending = None
        self._dispatch_answer(pending["request_id"], pending["questions"], answers)
        return True

    # ── ask 内部机制（子类一般不用碰）─────────────────────────────────

    def _set_ask_pending(self, request_id, questions, modes, meta=None):
        self._ask_pending = {
            "request_id": request_id,
            "questions": questions,
            "modes": modes,
            "expires": time.time() + ASK_PENDING_TTL,
            "conversation": self._ask_conversation_key(meta=meta),
        }

    def _ask_conversation_key(self, msg: "ChannelMessage | None" = None, meta=None) -> "str | None":
        """会话键（c2c 用户/群号）。默认 None=不区分会话（单会话 channel 足够）。"""
        return None

    def _dispatch_answer(self, request_id, questions, answers):
        """按 AskUser 契约组装答案并回送。"""
        resolve = getattr(self, "_resolve_answer", None)
        if resolve is None:
            logger.warning("channel %s answered but no resolver bound", self)
            return
        batch = len(questions) > 1 or any(q.get("multi_select") for q in questions)
        if not batch:
            resolve(request_id, answers[0] if answers else None)
            return
        import json as _json
        resolve(request_id, _json.dumps({
            "answers": [
                {"question": q.get("question", ""),
                 "answer": a}   # label / [labels] / None
                for q, a in zip(questions, answers)
            ]
        }, ensure_ascii=False))


def _option_labels(q: dict) -> list:
    labels = []
    for o in q.get("options") or []:
        labels.append(o.get("label", "") if isinstance(o, dict) else str(o))
    return [l for l in labels if l]


def _parse_ask_reply(text: str, pending: dict):
    """把用户文本解析为逐题答案列表；失败返回 None。

    单题：编号→label；文字原样（允许自由文本）。
    多题：按 ; ；分段，段数必须等于题数。
    """
    modes = pending["modes"]
    if len(modes) == 1:
        return [_parse_one(text, pending["questions"][0], modes[0])]
    parts = [p for p in text.replace("；", ";").split(";")]
    if len(parts) != len(modes) or any(not p.strip() for p in parts):
        return None
    return [_parse_one(p.strip(), q, m)
            for p, q, m in zip(parts, pending["questions"], modes)]


def _parse_one(text: str, q: dict, mode: str):
    labels = _option_labels(q)
    if mode == "multi":
        selected = []
        for token in text.replace("，", ",").replace("、", ",").replace(" ", ",").split(","):
            token = token.strip()
            if not token:
                continue
            hit = _match_option(token, labels)
            if hit is None:
                return None
            if hit not in selected:
                selected.append(hit)
        return selected or None
    if labels:
        return _match_option(text, labels) or text   # 没匹配上 → 当自由文本
    return text


def _match_option(token: str, labels: list):
    token = token.strip()
    if token.isdigit():
        idx = int(token)
        if 1 <= idx <= len(labels):
            return labels[idx - 1]
        return None
    if token in labels:
        return token
    # 前缀/包含匹配（"苹果" → "苹果汁"）
    for l in labels:
        if token in l:
            return l
    return None

    def __repr__(self):
        return f"<{type(self).__name__} {self.channel_type}#{self.channel_id}>"
