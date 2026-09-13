export interface ConfigEntry {
  key: string;
  name: string;
  type: "int" | "float" | "str" | "bool" | "choice";
  description: string;
  default: unknown;
  range?: [number, number];
  choices?: string[];
  tips: string;
  group: string;
}

/** Port of TUI/screens/config_editor.py CONFIG_CATALOG. */
export const CONFIG_CATALOG: ConfigEntry[] = [
  {
    key: "agent.maxDepth",
    name: "最大迭代次数",
    type: "int",
    description: "主智能体最大迭代次数，控制单次对话中智能体可以执行的最大步骤数",
    default: 8,
    range: [1, 50],
    tips: "过高会增加响应时间和成本，过低可能无法完成复杂任务",
    group: "🤖 Agent 配置",
  },
  {
    key: "agent_delegate.max_iterations",
    name: "子智能体最大迭代次数",
    type: "int",
    description: "子智能体（如 explore、plan）的最大迭代次数",
    default: 20,
    range: [5, 100],
    tips: "子智能体用于执行特定任务，如代码探索、规划等",
    group: "🔧 子智能体配置",
  },
  {
    key: "agent_delegate.explore.max_concurrent",
    name: "Explore 最大并发数",
    type: "int",
    description: "explore 类型子智能体的最大并发数",
    default: 3,
    range: [1, 10],
    tips: "并发执行可以加速代码探索，但会增加资源消耗",
    group: "🔧 子智能体配置",
  },
  {
    key: "agent_delegate.plan.max_concurrent",
    name: "Plan 最大并发数",
    type: "int",
    description: "plan 类型子智能体的最大并发数",
    default: 2,
    range: [1, 5],
    tips: "规划任务通常需要顺序执行，不建议设置过高",
    group: "🔧 子智能体配置",
  },
  {
    key: "agent_delegate.max_history_turns",
    name: "子智能体历史轮数",
    type: "int",
    description: "子智能体保留的历史对话轮数",
    default: 3,
    range: [1, 10],
    tips: "更多历史可以提供更好的上下文，但会增加 token 消耗",
    group: "🔧 子智能体配置",
  },
  {
    key: "memory.daily.retention_days",
    name: "消息保留天数",
    type: "int",
    description: "每日消息保留天数，超过此天数的消息会被归档到长期记忆",
    default: 7,
    range: [1, 365],
    tips: "较短的保留期可以减少内存占用，但可能丢失近期上下文",
    group: "💾 内存管理",
  },
  {
    key: "model.large-language-model.provider",
    name: "LLM 提供商",
    type: "choice",
    description: "LLM 提供商：仅支持 anthropic_compatible(Anthropic 兼容 API)",
    default: "anthropic_compatible",
    choices: ["anthropic_compatible"],
    tips: "当前仅支持 anthropic_compatible 提供商。需配置 api_key_env 和 base_url",
    group: "🧠 模型配置",
  },
  {
    key: "model.large-language-model.model",
    name: "模型名称",
    type: "str",
    description: "使用的模型名称（如 claude-3-5-sonnet-20241022, MiniMax-M2.7）",
    default: "MiniMax-M2.7",
    tips: "确保模型名称与 API 提供商匹配",
    group: "🧠 模型配置",
  },
  {
    key: "model.large-language-model.max_tokens",
    name: "最大 Token 数",
    type: "int",
    description: "单次生成的最大 token 数",
    default: 1024,
    range: [128, 8192],
    tips: "更大的值允许生成更长的回答，但会增加成本",
    group: "🧠 模型配置",
  },
  {
    key: "model.large-language-model.temperature",
    name: "生成温度",
    type: "float",
    description: "生成温度，控制输出的随机性（0.0-1.0）",
    default: 0.7,
    range: [0.0, 1.0],
    tips: "较低的值使输出更确定，较高的值使输出更有创造性",
    group: "🧠 模型配置",
  },
  {
    key: "model.large-language-model.top_p",
    name: "核采样参数",
    type: "float",
    description: "核采样参数，控制输出的多样性（0.0-1.0）",
    default: 0.9,
    range: [0.0, 1.0],
    tips: "与 temperature 配合使用，控制生成质量",
    group: "🧠 模型配置",
  },
  {
    key: "tools.security.enabled",
    name: "安全检查",
    type: "bool",
    description: "是否启用工具安全检查",
    default: true,
    tips: "强烈建议保持启用，防止执行危险命令",
    group: "🔒 工具安全",
  },
  {
    key: "tools.security.allow_confirmation",
    name: "允许确认执行",
    type: "bool",
    description: "是否允许确认后执行需要确认的命令",
    default: false,
    tips: "启用后，某些命令会要求用户确认后才执行",
    group: "🔒 工具安全",
  },
];
