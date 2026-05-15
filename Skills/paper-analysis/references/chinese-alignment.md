# Chinese alignment

Use these phrasing conventions when the user writes in Chinese or wants bilingual outputs.

## Core concepts

- paper analysis -> 论文分析
- engineering-style analysis -> 工程化分析 / 工程化解读
- traceable package -> 可追溯分析包
- claim-evidence mapping -> 论断-证据映射
- experiment audit -> 实验审计 / 实验批判性审查
- benchmark audit -> 基准测试审计
- methods audit -> 方法审计
- source fact -> 源事实
- author claim -> 作者论断
- assistant inference -> 助手推断
- readiness state -> 完成状态 / 就绪状态
- partial with gaps -> 部分完成但存在缺口
- needs source material -> 需要补充源材料
- blocked -> 受阻 / 无法可信完成

## `model/` stage language

Use consistent phrasing for the later workflow stages:

- repository analysis -> 仓库分析 / 代码仓库分析
- code structure -> 代码结构
- training or evaluation path -> 训练 / 评估路径
- core modules -> 核心模块
- tensor flow -> 张量流
- data flow -> 数据流
- math-to-code mapping -> 数学到代码映射
- paper-inferred only -> 仅基于论文推断
- repository-verified -> 已通过仓库核验

## Critique language

Prefer restrained wording:

- weakly supported -> 支撑较弱
- partially supported -> 部分支撑
- indirectly supported -> 间接支撑
- unsupported from supplied material -> 当前提供材料无法支撑
- supplement-dependent -> 依赖补充材料
- baseline fairness unclear -> 基线公平性不清楚
- implementation detail missing -> 实现细节缺失
- tensor path uncertain -> 张量路径不确定

## Tone rules

- Keep Chinese concise and technical.
- Avoid exaggerated wording such as "完全证明" or "绝对领先" unless the source truly justifies it.
- Prefer evidence-first phrases such as "从表2可以直接看到" or "论文声称...但当前材料仅部分支撑".
- When uncertainty matters, make it visible early rather than burying it in the last paragraph.
