"""AskUser 工具：需要用户做选择时，向前端发起交互式提问（支持批量多问 + 多选）。

对标 Claude Code 的 AskUserQuestion：模型不要只在正文里罗列选项让用户
打字回复，而是调用本工具——TUI 会渲染可选择的问卷面板，用户作答后
答案作为工具结果直接返回，模型据此继续执行。

两种调用形态：
1. 旧版单问：AskUser(question=..., options=[...])            → 单选，返回 {answer}
2. 批量多问：AskUser(questions=[{question, options, multi_select}, ...])
   → 一次问完多个问题（≤4 个），每题可单选或多选，返回 {answers: [...]}

handler 阻塞当前工具线程等待用户回答（QuestionManager 实现）。
本工具在 runtime 白名单里（tools.background_exempt_tools）——永不转后台，
可以一直阻塞等用户。无前端/超时优雅降级返回 status JSON，绝不抛异常。
"""

import json
import logging

from Tools.config_proxy import get_config
from Tools.question_manager import DEFAULT_TIMEOUT, question_manager
from Tools.registry import registry

logger = logging.getLogger(__name__)


def _normalize_questions(args: dict):
    """把新旧两种入参形态归一为 questions 列表；非法输入返回错误信息。"""
    questions = args.get("questions")
    if isinstance(questions, list) and questions:
        if len(questions) > 4:
            return None, "questions 一次最多 4 个问题（更多请拆多次调用或减少问题）"
        normalized = []
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                return None, f"questions[{i}] 必须是对象"
            text = str(q.get("question") or "").strip()
            options = q.get("options")
            if not text:
                return None, f"questions[{i}] 缺少 question"
            if not isinstance(options, list) or not options:
                return None, f"questions[{i}] 缺少 options（至少 1 个）"
            if len(options) > 5:
                return None, f"questions[{i}] 的 options 最多 5 个"
            labels = []
            for opt in options:
                label = str((opt or {}).get("label") or "").strip() if isinstance(opt, dict) else ""
                if not label:
                    return None, f"questions[{i}] 有选项缺少 label"
                labels.append({"label": label, "description": str(opt.get("description") or "") or None})
            normalized.append({
                "question": text,
                "options": labels,
                "multi_select": bool(q.get("multi_select", False)),
            })
        return normalized, None

    # 旧版单问形态
    question = str(args.get("question") or "").strip()
    options = args.get("options")
    if not question:
        return None, "缺少 question（或使用 questions 批量形态）"
    if not isinstance(options, list) or not options:
        return None, "options 不能为空列表，至少提供 1 个选项"
    labels = []
    for opt in options:
        label = str((opt or {}).get("label") or "").strip() if isinstance(opt, dict) else ""
        if not label:
            return None, "有选项缺少 label"
        labels.append({"label": label, "description": str(opt.get("description") or "") or None})
    return [{
        "question": question,
        "options": labels,
        "multi_select": False,
    }], None


def ask_user(**kwargs) -> str:
    """向用户提问（单问/批量、单选/多选），阻塞等待作答，返回答案。"""
    questions, err = _normalize_questions(kwargs)
    if err:
        return json.dumps({"status": "error", "message": err}, ensure_ascii=False)

    batch = len(questions) > 1 or any(q["multi_select"] for q in questions)
    timeout = int(get_config().get("tools.ask_user.timeout_seconds", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)

    logger.info(
        "AskUser 发出提问: %d 个问题（%s）, timeout=%ds",
        len(questions), "批量" if batch else "单问", timeout,
    )
    raw = question_manager.request({"questions": questions}, timeout=timeout)

    if raw is None:
        if not question_manager.has_notifier:
            logger.info("AskUser 无前端可用，降级为正文罗列选项")
            return json.dumps(
                {
                    "status": "unavailable",
                    "message": "当前前端不支持交互提问，请直接在回答正文中列出选项让用户回复",
                },
                ensure_ascii=False,
            )
        logger.info("AskUser 等待用户回答超时 (%ds)", timeout)
        return json.dumps(
            {
                "status": "timeout",
                "message": f"用户 {timeout} 秒内未响应，请按最合理的默认方案继续，或稍后再问",
            },
            ensure_ascii=False,
        )

    if batch:
        # TUI 批量面板回传 JSON：{"answers": [{"question","answer"}, ...]}
        try:
            parsed = json.loads(raw)
            answers = parsed.get("answers") if isinstance(parsed, dict) else None
            if isinstance(answers, list) and len(answers) == len(questions):
                logger.info("AskUser 收到批量答案: %s", raw[:200])
                return json.dumps({"status": "answered", "answers": answers}, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        # 兜底：非预期格式按取消处理
        logger.warning("AskUser 批量答案格式异常: %s", raw[:120])
        return json.dumps(
            {"status": "answered", "answers": [{"question": q["question"], "answer": None} for q in questions]},
            ensure_ascii=False,
        )

    # 单问：raw 即用户选中的 label（或取消说明文本）
    logger.info("AskUser 收到用户答案: %r", raw)
    return json.dumps({"status": "answered", "answer": raw}, ensure_ascii=False)


registry.register(
    name="AskUser",
    description=(
        "Ask the user interactive multiple-choice questions — supports BATCH asking "
        "(up to 4 questions in one call, each single-select or multi-select). "
        "Use `questions: [{question, options: [{label, description}], multi_select}, ...]` "
        "to ask everything at once so the user answers in one pass instead of being "
        "interrupted repeatedly. Legacy form (question + options) still works as a single "
        "single-select question. Answers: batch returns {status, answers: [{question, answer}]} "
        "where answer is a label (single), an array of labels (multi), or null (user left it "
        "unanswered); legacy returns {status, answer}. This tool blocks until the user "
        "responds (never backgrounded) — do NOT ask and then proceed without waiting. "
        "If status is 'timeout', proceed with the most reasonable default."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "description": "Batch form: 1-4 questions to ask in one panel.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question text."},
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 5,
                            "description": "1-5 options for this question.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "Short option label."},
                                    "description": {"type": "string", "description": "Optional explanation."},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": "true = user may tick multiple options (checkbox); default false (radio).",
                            "default": False,
                        },
                    },
                    "required": ["question", "options"],
                },
            },
            "question": {
                "type": "string",
                "description": "Legacy single-question form (use `questions` for batch).",
            },
            "options": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "description": "Legacy single-question options.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label"],
                },
            },
        },
    },
    handler=ask_user,
    group="core",
)
