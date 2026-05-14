# 提示词使用情况分析

## 提示词分类与注入时机

### 1. **通过 config.yaml 配置的提示词（5个）**

这些提示词通过 `PromptLoader.load_system_sections()` 加载，在 `config.yaml` 中配置路径。

#### 1.1 Actor Agent (2个)
- **prompts/actor/base.md**
  - 注入时机：每次 Actor 生成回答时
  - 调用方法：`PromptAssembler.build_actor_document()`
  - 作用：定义 Actor 的基础行为和工具调用规范

- **Agent/prompts/paper_analysis_sop.md** ⚠️
  - 注入时机：每次 Actor 生成回答时
  - 状态：**文件不存在**（config.yaml 中配置但未创建）
  - 建议：删除此配置或创建文件

#### 1.2 Reflection Agent (1个)
- **prompts/reflection/base.md**
  - 注入时机：每次 Reflection 审核时
  - 调用方法：`PromptAssembler.build_reflection_document()`
  - 作用：定义 Reflection 的审核标准

#### 1.3 Summarizer Agent (1个)
- **prompts/summarizer/base.md**
  - 注入时机：日总结和主题合并时
  - 调用方法：`PromptAssembler.build_day_summary_document()` 和 `build_topic_merge_document()`
  - 作用：定义总结和合并的基础规范

#### 1.4 Reviewer Agent (2个)
- **prompts/reviewer/base.md**
  - 注入时机：用户画像复盘时
  - 调用方法：`PromptAssembler.build_review_document()`
  - 作用：定义复盘的基础规范

- **prompts/reviewer/contract.md**
  - 注入时机：用户画像复盘时
  - 调用方法：`PromptAssembler.build_review_document()`
  - 作用：定义复盘的输出契约

---

### 2. **通过代码直接加载的提示词（20个）**

这些提示词通过代码中的 `_load_prompt()` 方法直接加载。

#### 2.1 论文分析子智能体 (8个) ✅ **本次新增**
所有提示词在 `PaperAnalysisDelegate` 执行时注入：

- **prompts/paper_analysis/pdf_parser.md**
  - 注入时机：PDF 解析阶段
  - Agent类型：`pdf_parser`
  - 作用：指导 PDF 内容提取

- **prompts/paper_analysis/content_analysis.md**
  - 注入时机：内容分析阶段
  - Agent类型：`content_analysis`
  - 作用：指导论文章节深度分析

- **prompts/paper_analysis/tech_extraction.md**
  - 注入时机：技术提取阶段（并行）
  - Agent类型：`tech_extraction`
  - 作用：指导技术模块和创新点提取

- **prompts/paper_analysis/fake_data_reproduction.md**
  - 注入时机：假数据复现阶段（并行）
  - Agent类型：`fake_data_reproduction`
  - 作用：指导维度追踪和数据流图生成

- **prompts/paper_analysis/literature_analysis.md**
  - 注入时机：文献分析阶段（并行）
  - Agent类型：`literature_analysis`
  - 作用：指导引用文献分析

- **prompts/paper_analysis/relation_analysis.md**
  - 注入时机：关联分析阶段
  - Agent类型：`relation_analysis`
  - 作用：指导引用网络和 SOTA 判断

- **prompts/paper_analysis/knowledge_integration.md**
  - 注入时机：知识库整合阶段
  - Agent类型：`knowledge_integration`
  - 作用：指导知识库文件创建和索引更新

- **prompts/paper_analysis/orchestrator.md**
  - 注入时机：主智能体协调时
  - 使用位置：`PaperAnalysisOrchestrator`
  - 作用：定义 SOP 流程和子智能体调度规则
  - ⚠️ **注意**：目前未实际使用，因为 `PaperAnalysisOrchestrator` 是程序化执行，不调用 LLM

#### 2.2 系统辅助提示词 (11个) ✅ **本次新增**

##### 2.2.1 上下文注入提示词 (5个)
在各个 Agent 构建时动态注入：

- **prompts/system/soul_section.md**
  - 注入时机：所有 Agent 构建时
  - 调用方法：`_build_soul_section()`
  - 作用：引导角色设定的注入

- **prompts/system/user_profile.md**
  - 注入时机：Actor 和 Reviewer 构建时
  - 调用方法：`_build_user_profile_section()`
  - 作用：引导用户画像的注入

- **prompts/system/memory.md**
  - 注入时机：Actor 和 Reflection 构建时
  - 调用方法：`_build_memory_section(background_only=False)`
  - 作用：引导长期记忆的注入（主要来源）

- **prompts/system/memory_background.md**
  - 注入时机：Reviewer 构建时
  - 调用方法：`_build_memory_section(background_only=True)`
  - 作用：引导长期记忆的注入（补充背景）

- **prompts/system/tool_directory.md**
  - 注入时机：Actor 构建时
  - 调用方法：`_build_tool_directory_section()`
  - 作用：引导工具目录的注入

##### 2.2.2 Reflection 提示词 (3个)
在 Reflection Agent 构建时注入：

- **prompts/system/reflection_question.md**
  - 注入时机：Reflection 构建时
  - 作用：引导原始问题的注入

- **prompts/system/reflection_history.md**
  - 注入时机：Reflection 构建时
  - 作用：引导对话历史的注入

- **prompts/system/reflection_task.md**
  - 注入时机：Reflection 构建时
  - 作用：定义 Reflection 的判断任务

##### 2.2.3 Summarizer 契约提示词 (2个)
在 Summarizer Agent 构建时注入：

- **prompts/system/day_summary_contract.md**
  - 注入时机：日总结时
  - 调用方法：`build_day_summary_document()`
  - 作用：定义日总结的输出格式契约

- **prompts/system/topic_merge_contract.md**
  - 注入时机：主题合并时
  - 调用方法：`build_topic_merge_document()`
  - 作用：定义主题合并的输出格式契约

##### 2.2.4 Reviewer 提示词 (1个)
在 Reviewer Agent 构建时注入：

- **prompts/system/review_turn.md**
  - 注入时机：用户画像复盘时
  - 调用方法：`build_review_document()`
  - 作用：引导复盘任务的注入

#### 2.3 Gateway 提示词 (1个) ✅ **本次新增**

- **prompts/gateway/session_context.md**
  - 注入时机：Gateway 构建会话上下文时
  - 调用方法：`build_session_context_prompt()`
  - 作用：定义会话上下文的模板
  - ⚠️ **注意**：目前未实际使用模板内容，代码中直接拼接字符串

---

## 使用状态总结

### ✅ 正在使用 (23个)

#### 通过 config.yaml 配置 (4个)
- actor/base.md
- reflection/base.md
- summarizer/base.md
- reviewer/base.md
- reviewer/contract.md

#### 通过代码直接加载 (18个)
- **论文分析** (7个)：除 orchestrator.md 外的所有子智能体提示词
- **系统辅助** (11个)：所有系统提示词

### ⚠️ 未使用或有问题 (2个)

1. **Agent/prompts/paper_analysis_sop.md**
   - 状态：配置中存在但文件不存在
   - 建议：从 config.yaml 中删除此配置

2. **prompts/paper_analysis/orchestrator.md**
   - 状态：文件存在但未实际使用
   - 原因：`PaperAnalysisOrchestrator` 是程序化执行，不调用 LLM
   - 建议：保留作为文档参考，或改造为 LLM 驱动的协调器

3. **prompts/gateway/session_context.md**
   - 状态：文件存在但未实际使用模板内容
   - 原因：代码中直接拼接字符串而非使用模板
   - 建议：改造代码以使用模板，或删除此文件

---

## 提示词注入流程图

```
用户请求
    │
    ├─→ Actor Agent
    │   ├─ prompts/actor/base.md (config)
    │   ├─ prompts/system/soul_section.md
    │   ├─ prompts/system/user_profile.md
    │   ├─ prompts/system/memory.md
    │   └─ prompts/system/tool_directory.md
    │
    ├─→ Reflection Agent (如果启用)
    │   ├─ prompts/reflection/base.md (config)
    │   ├─ prompts/system/soul_section.md
    │   ├─ prompts/system/memory.md
    │   ├─ prompts/system/reflection_question.md
    │   ├─ prompts/system/reflection_history.md
    │   └─ prompts/system/reflection_task.md
    │
    ├─→ Reviewer Agent (用户画像复盘)
    │   ├─ prompts/reviewer/base.md (config)
    │   ├─ prompts/reviewer/contract.md (config)
    │   ├─ prompts/system/memory_background.md
    │   └─ prompts/system/review_turn.md
    │
    ├─→ Summarizer Agent (日总结)
    │   ├─ prompts/summarizer/base.md (config)
    │   └─ prompts/system/day_summary_contract.md
    │
    ├─→ Summarizer Agent (主题合并)
    │   ├─ prompts/summarizer/base.md (config)
    │   └─ prompts/system/topic_merge_contract.md
    │
    └─→ Paper Analysis Agents (论文分析)
        ├─ PDF Parser
        │   └─ prompts/paper_analysis/pdf_parser.md
        ├─ Content Analysis
        │   └─ prompts/paper_analysis/content_analysis.md
        ├─ Tech Extraction
        │   └─ prompts/paper_analysis/tech_extraction.md
        ├─ Fake Data Reproduction
        │   └─ prompts/paper_analysis/fake_data_reproduction.md
        ├─ Literature Analysis
        │   └─ prompts/paper_analysis/literature_analysis.md
        ├─ Relation Analysis
        │   └─ prompts/paper_analysis/relation_analysis.md
        └─ Knowledge Integration
            └─ prompts/paper_analysis/knowledge_integration.md
```

---

## 建议

1. **清理无效配置**：从 `config.yaml` 删除 `Agent/prompts/paper_analysis_sop.md`
2. **决定 orchestrator.md 的去留**：要么改造为 LLM 驱动，要么移至文档目录
3. **完善 Gateway 模板**：改造代码以实际使用 `session_context.md` 模板
4. **添加提示词版本管理**：为每个提示词添加版本号和变更日志
