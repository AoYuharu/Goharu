import json
import logging
import os
import re

import requests

import config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是智能体能力注册表的管理员。一个智能体上报了它的一项能力，"
    "请你判断该如何把这项能力与它已有的能力原子进行整合。\n"
    "只输出一个 JSON 对象，不要输出任何其他文字。"
)

_ACTION_RULES = (
    "动作取值：\n"
    "- add：本次能力是全新的，与现有能力无重叠，新增为一个原子\n"
    "- update：本次能力是某个现有能力的演化/完善，覆盖那个原子（target 填那个现有 capability_id）\n"
    "- merge：本次能力与某个现有能力高度重复，并入那个原子（target 填那个现有 capability_id）\n"
    "- ignore：本次能力是已有能力的完全重复且无新增信息，忽略\n"
    "retire 数组：列出需要软删除（已过期/被取代）的现有能力 capability_id，可空。\n"
    "输出格式：{\"action\": \"add|update|merge|ignore\", \"target\": \"现有capability_id或null\", "
    "\"retire\": [\"capability_id\", ...], \"reason\": \"一句话理由\"}"
)


class Curator:
    def __init__(self, db, retriever, embedder):
        self.db = db
        self.retriever = retriever
        self.embedder = embedder

    def reconcile(self, agent_id, capability_id, name, description):
        """检索相关原子 → 小模型判断 → 执行增/改/删，返回决策。"""
        candidates = self.retriever.search_in_agent(
            agent_id, f"{name} {description}", top_k=config.CURATOR_TOP_K
        )

        try:
            decision = self._judge(capability_id, name, description, candidates)
            used_llm = True
        except Exception as e:
            logger.warning("Curator: 小模型判断失败 (%s)，降级为确定性 upsert", e)
            decision = self._fallback_decision(agent_id, capability_id)
            used_llm = False

        self._execute(agent_id, capability_id, name, description, decision)
        decision["_used_llm"] = used_llm
        return decision

    # ------------------------------------------------------------------
    # Judge (LLM)
    # ------------------------------------------------------------------

    def _judge(self, capability_id, name, description, candidates):
        if not candidates:
            candidate_text = "（该智能体目前没有任何已注册的能力）"
        else:
            lines = [
                f"- capability_id={c['capability_id']} | {c['capability_name']} | {c['description']}"
                for c in candidates
            ]
            candidate_text = "\n".join(lines)

        user = (
            "【本次上报的能力】\n"
            f"capability_id: {capability_id}\n"
            f"名称: {name}\n"
            f"描述: {description}\n\n"
            "【该智能体现有的相关能力原子】\n"
            f"{candidate_text}\n\n"
            f"{_ACTION_RULES}"
        )
        text = self._call_llm(_SYSTEM_PROMPT, user)
        return self._parse(text)

    def _call_llm(self, system, user):
        key = os.getenv(config.CURATOR_API_KEY_ENV, "")
        if not key:
            raise RuntimeError(f"环境变量 {config.CURATOR_API_KEY_ENV} 未设置")
        url = config.CURATOR_BASE_URL.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": config.CURATOR_MODEL,
            "max_tokens": 800,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        text_blocks = [
            b.get("text", "")
            for b in data.get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "\n".join(t for t in text_blocks if t).strip()
        if not text:
            raise RuntimeError("MiniMax 响应无 text 块")
        return text

    def _parse(self, text):
        stripped = text.strip()
        fence = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, re.DOTALL)
        if fence:
            stripped = fence.group(1).strip()
        decision = json.loads(stripped)

        action = decision.get("action", "add")
        if action not in ("add", "update", "merge", "ignore"):
            action = "add"
        decision["action"] = action
        decision.setdefault("target", None)
        decision.setdefault("retire", [])
        decision.setdefault("reason", "")
        return decision

    def _fallback_decision(self, agent_id, capability_id):
        """LLM 不可用时的确定性行为：已存在同 id 则 update，否则 add。"""
        exists = self.db.get_capability(agent_id, capability_id) is not None
        return {
            "action": "update" if exists else "add",
            "target": None,
            "retire": [],
            "reason": "curator LLM 不可用，降级为确定性 upsert",
        }

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def _execute(self, agent_id, capability_id, name, description, decision):
        action = decision.get("action")
        target = decision.get("target")

        # 本次要落库/更新的原子：add 是 capability_id，update/merge 是 target
        active_target = target if action in ("update", "merge") else capability_id

        # retire 不能作用于 active_target 本身：先 retire 再 upsert 会把刚退的原子重新激活
        retire_list = [
            oid for oid in (decision.get("retire") or [])
            if oid and oid != active_target
        ]

        for old_id in retire_list:
            if self.db.get_capability(agent_id, old_id) is not None:
                self.db.retire_capability(agent_id, old_id)
                self.db.log_event(agent_id, old_id, "retire", decision)
                logger.info("Curator: retire 原子 %s/%s", agent_id, old_id)

        if action == "add":
            self.db.upsert_capability(agent_id, capability_id, name, description)
            self._reindex(agent_id, capability_id, name, description)
            self.db.log_event(agent_id, capability_id, "add", decision)
            logger.info("Curator: add 原子 %s/%s", agent_id, capability_id)

        elif action == "update":
            target_id = target or capability_id
            self.db.upsert_capability(agent_id, target_id, name, description)
            self._reindex(agent_id, target_id, name, description)
            self.db.log_event(agent_id, target_id, "update", decision)
            logger.info("Curator: update 原子 %s/%s", agent_id, target_id)

        elif action == "merge":
            # 高度重复 → 并入现有原子：保留现有富内容，不覆盖，仅记录事件
            target_id = target or capability_id
            if self.db.get_capability(agent_id, target_id) is not None:
                self.db.log_event(agent_id, target_id, "merge", decision)
                logger.info("Curator: merge 原子 %s/%s（保留现有内容）", agent_id, target_id)
            else:
                self.db.upsert_capability(agent_id, target_id, name, description)
                self._reindex(agent_id, target_id, name, description)
                self.db.log_event(agent_id, target_id, "merge", decision)

        elif action == "ignore":
            self.db.log_event(agent_id, capability_id, "ignore", decision)
            logger.info("Curator: ignore %s/%s", agent_id, capability_id)

    def _reindex(self, agent_id, capability_id, name, description):
        agent = self.db.get_agent(agent_id)
        agent_name = agent["name"] if agent else ""
        self.db.replace_fts(agent_id, agent_name, capability_id, name, description)
        vector = self.embedder.encode_description(f"{name}。{description}")
        if vector:
            self.db.replace_embedding(agent_id, capability_id, vector, self.embedder.model_name)
