"""
测试新的答案审核流程

新流程特点：
1. Actor 自己判断何时可以回答
2. 当 Actor 输出答案时，Reflection 进行审核
3. FileStateManager 记录所有文件读取和工具调用
4. Reflection 可以看到工具调用结果，但看不到 Actor 的思考过程
5. 只有审核通过的答案才展示给用户
6. 最多 N 次审核循环，超过后强制展示

测试方法：
1. 修改 config.yaml，设置 reflection_mode: answer_review
2. 运行 python main.py
3. 输入测试问题
4. 观察新流程的执行
"""

print(__doc__)

print("\n" + "=" * 60)
print("配置说明")
print("=" * 60)

print("""
在 config.yaml 中添加/修改以下配置：

```yaml
mcp:
  executor: D:\\MyAnaconda\\envs\\llm\\python.exe
  args:
    - E:\\TableHelper\\MCP\\MCP.py
  maxDepth: 8
  reflection_mode: answer_review  # 使用新的答案审核模式
  max_review_cycles: 3  # 最多审核循环次数
```

reflection_mode 选项：
- adaptive: 旧模式，定期触发 Reflection
- always: 旧模式，每步都触发 Reflection
- never: 旧模式，不触发 Reflection
- answer_review: 新模式，仅在 Actor 输出答案时审核
""")

print("\n" + "=" * 60)
print("新流程说明")
print("=" * 60)

print("""
执行流程：

1. Actor 调用工具收集信息
   - FileStateManager 记录所有 Read 工具的文件内容
   - FileStateManager 记录所有工具调用的参数和结果

2. Actor 认为信息充分，输出答案
   - 触发 Reflection 审核

3. Reflection 审核答案
   - 输入：用户问题 + 工具调用记录 + 文件内容 + Actor 的答案
   - 不包含：Actor 的思考过程（raw_reply）
   - 输出："答案可以接受" 或 "答案需要改进"

4. 根据审核结果：
   a) 答案可以接受 → 展示给用户，结束
   b) 答案需要改进 → 反馈给 Actor，让其改进
      - Actor 可以继续调用工具
      - Actor 可以直接改进答案
      - 重复步骤 2-4，最多 N 次

5. 达到最大审核次数 → 强制展示当前答案
""")

print("\n" + "=" * 60)
print("测试用例")
print("=" * 60)

print("""
测试用例 1：信息收集任务
问题："扫描当前项目结构，告诉我你自己是怎么被搭建起来的"

预期行为：
- Actor 调用 dir, Read CLAUDE.md, Read config.yaml, Read main.py
- FileStateManager 记录所有文件内容
- Actor 输出答案
- Reflection 审核：检查答案是否基于实际读取的文件
- 如果答案准确，审核通过
- 如果答案编造或不完整，要求改进

测试用例 2：代码分析任务
问题："分析 ActorAgent.py 的核心功能"

预期行为：
- Actor 调用 Read ActorAgent.py
- FileStateManager 记录文件内容
- Actor 输出答案
- Reflection 审核：检查答案是否准确分析了代码
- 如果分析准确，审核通过
- 如果分析有误，要求改进

测试用例 3：多轮审核
问题："详细说明项目的内存管理机制"

预期行为：
- Actor 第一次回答可能不够详细
- Reflection 要求改进
- Actor 继续调用工具或补充信息
- Actor 第二次回答
- Reflection 再次审核
- 最多 3 次循环
""")

print("\n" + "=" * 60)
print("验证要点")
print("=" * 60)

print("""
1. Reflection 触发时机
   ✓ 只在 Actor 输出 answer 时触发
   ✓ Actor 调用工具时不触发

2. FileStateManager 功能
   ✓ 记录所有 Read 工具读取的文件内容
   ✓ 记录所有工具调用的参数和结果
   ✓ 提供给 Reflection 完整的上下文

3. Reflection 输入
   ✓ 包含：用户问题、工具调用记录、文件内容、Actor 答案
   ✓ 不包含：Actor 的思考过程（raw_reply）

4. 审核循环
   ✓ 审核通过 → 展示答案
   ✓ 审核不通过 → 反馈给 Actor
   ✓ 最多 N 次循环
   ✓ 超过后强制展示

5. 用户体验
   ✓ 只看到审核通过的答案
   ✓ 看到审核统计信息（循环次数、文件数、工具调用数）
   ✓ 不看到中间的审核过程（除非配置显示）
""")

print("\n" + "=" * 60)
print("运行测试")
print("=" * 60)

print("""
1. 确保配置正确：
   cat config.yaml | grep -A 5 "mcp:"

2. 运行主程序：
   python main.py

3. 输入测试问题：
   扫描当前项目结构，告诉我你自己是怎么被搭建起来的

4. 观察输出：
   - Actor 调用工具的过程
   - Actor 输出答案
   - Reflection 审核过程
   - 最终展示的答案
   - 审核统计信息
""")

print("\n" + "=" * 60)
print("对比旧流程")
print("=" * 60)

print("""
旧流程（adaptive 模式）：
- Reflection 定期触发（每 3 步或答案时）
- Reflection 判断"可以给出最终回答"或"需要继续调用工具"
- Actor 根据 Reflection 的判断调整行为
- 问题：Actor 和 Reflection 可能不同步

新流程（answer_review 模式）：
- Reflection 仅在 Actor 输出答案时触发
- Reflection 审核答案质量，而不是判断是否继续
- Actor 完全自主决定何时回答
- 优势：Actor 和 Reflection 职责更清晰
""")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
print("\n请按照上述步骤运行测试\n")
