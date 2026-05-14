你的任务是把"某一天已经过期的对话记录"压缩成可以写入长期记忆系统的严格 JSON。
请只保留长期有价值、可复用、可检索的信息，忽略寒暄和一次性噪声。

请严格输出且仅输出 JSON，格式必须为：
{
  "profile_updates": {"字段": "值"},
  "important_facts": ["事实1", "事实2"],
  "conversation_summary": "不超过 120 字的摘要",
  "topics": [
    {
      "slug": "topic-slug",
      "title": "Topic Title",
      "action": "create",
      "summary": "该话题的长期摘要",
      "keywords": ["关键词1", "关键词2"],
      "facts": ["关键事实1", "关键事实2"],
      "open_questions": ["未解决问题"]
    }
  ]
}

约束：
1. action 只能是 create、update、ignore 之一
2. topics 中只保留值得进入长期主题记忆的话题
3. 不要输出重复事实
4. 不要输出 markdown 代码块
5. 不要在 JSON 前后添加任何解释文字
6. 输出结果必须可以被 json.loads 直接解析
