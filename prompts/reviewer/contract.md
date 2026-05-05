你的任务是从“当前轮次的对话 transcript”中提取用户明确表达的客观事实与稳定偏好，并输出严格 JSON。

请严格输出且仅输出 JSON，格式必须为：
{
  "profile_updates": {
    "name": "",
    "age": "",
    "employer": "",
    "home_address": "",
    "preferences.communication_style": ""
  },
  "important_facts": [""],
  "conflicts": [
    {
      "field": "age",
      "existing_value": "29",
      "candidate_value": "30",
      "reason": "explicit_correction"
    }
  ],
  "retractions": ["field_name"],
  "turn_summary": ""
}

约束：
1. 只提取用户在本轮中明确表达的信息，不要根据上下文猜测、补全或推断
2. 不要从 assistant 的表述中反推用户事实
3. profile_updates 只放本轮可以确认的增量；没有就返回空对象 {}
4. stable preferences 只有在用户明确表达时才写入，例如喜欢/不喜欢/偏好某种回答风格
5. 如果候选值与 USER.md 现有值冲突，只有本轮出现明确更正语义时，conflicts.reason 才能写 explicit_correction；否则写 ambiguous_conflict
6. 如果用户明确撤回、否定或删除某个旧画像字段，可在 retractions 中写字段名
7. 若本轮没有可写入画像的信息，返回空更新，不要编造内容
8. important_facts 仅保留明确、稳定、客观且适合长期保留的事实；不要写一次性任务细节
9. turn_summary 仅用于调试审计，简短概括本轮是否有画像变更
10. 不要输出 markdown 代码块
11. 不要在 JSON 前后添加任何解释文字
12. 输出结果必须可以被 json.loads 直接解析
