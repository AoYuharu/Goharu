"""子代理 LLM 注入持有者（无副作用模块，import 不注册任何工具）。

AgentDelegate 工具（Tools/builtin/agent_delegate.py）在 import 时会注册工具，
所以注入值不能存在它里面——create_agent 需要在不加载该模块的前提下完成接线。
"""

_holder = None


def set_subagent_llm(llm) -> None:
    """注入子代理使用的 LLM（结构化接口对象）。由 Agent.api.create_agent 调用。"""
    global _holder
    _holder = llm


def get_subagent_llm():
    """取注入的 LLM；None 表示走 Goharu LLMCore 单例（默认）。"""
    return _holder
