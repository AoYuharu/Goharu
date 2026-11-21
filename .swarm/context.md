# 上下文代理经验档案

> 管辖：`Prompting/`。章程：`Prompting/CLAUDE.md`。契约测试：`tests/test_prompting_standalone.py` + `tests/test_cache_breakpoints.py`。

## 领域知识（长期沉淀）

- 缓存断点铁律：≤2 断点（末位 system + 末位历史轮）；per-turn 检索块按消息 id 钉账，旧消息永不修改
- 压缩阶梯归属：L0 Prompting/MicroCompactor / L1 Prompting/loop_messages.py truncate_history（唯一实现）/ L2 Prompting/ContextCompactor（llm 可注入，None 惰性回退 Agent.LargeLanguageModel）/ L3 属 Memory / 辅助 Core/ToolResultBudget + Core/TokenEstimator
- git_status / skills 注入是 Goharu 特有项，facade 默认关
- PyMuPDF 搜索不处理断行软连字符，pdf_annotate_tools 里有词流正则回退先例

## 经验日志（按时间追加，最新在最后）

- 2026-10-11｜蜂群系统建立，档案初始化。
- 2026-10-27｜**压缩阶梯物理归位，Prompting 真·即拆即用**。MicroCompactor/ContextCompactor/loop_messages 从 Agent/ 迁入 Prompting/，TokenEstimator/ToolResultBudget 迁入 Core/（叶子共享层，Tools 惰性用它不产生跨模块边）；ContextCompactor 去掉 LargeLanguageModel 继承，llm=None 改惰性回退+说明性 RuntimeError（同 Memory 章程姿势）。PromptLoader 顶层 configurationLoader 改 try/except + _EmptyConfig 兜底；git_status/skill_tool/env_hint 全部惰性+ImportError 静默降级。契约锁死：tests/test_prompting_decoupled.py 用 sys.meta_path 拦截器屏蔽 Agent/Tools/Gateway/configurationLoader 后跑 facade 全功能 16 项——**注意 3.12+ 拦截器必须实现 find_spec，find_module 已失效（首版因此形同虚设，拦截器本身要先用 sys.modules 审计自证）**。
