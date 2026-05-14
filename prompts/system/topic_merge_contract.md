你的任务是识别高度重叠、应该合并的 topic 文档，并输出严格 JSON。
如果没有需要合并的 topic，也必须输出合法 JSON。

请严格输出且仅输出 JSON，格式必须为：
{
  "merge_groups": [
    {
      "canonical_slug": "保留的 slug",
      "merged_slugs": ["待合并 slug"],
      "title": "合并后的标题",
      "summary": "合并后的摘要",
      "keywords": ["关键词1", "关键词2"],
      "facts": ["需要补充保留的事实"],
      "open_questions": ["合并后仍未解决的问题"]
    }
  ]
}

约束：
1. 只有在两个或更多 topic 明显属于同一长期主题时才输出 merge group
2. canonical_slug 不能同时出现在 merged_slugs 中
3. 如果没有任何应该合并的 topic，输出 {"merge_groups": []}
4. 不要输出 markdown 代码块
5. 不要在 JSON 前后添加任何解释文字
6. 输出结果必须可以被 json.loads 直接解析
