"""HaruNet 统一命名常量 + 网络昵称识别格式。

改统一名称只动这里（提示词自动跟随）：
- NET_NAME：网络统一名
- EXEC_LABEL：主执行智能体通称
- MAINT_LABEL：管理升级维护通称
- nick_token(name)：网络昵称在提示词中的统一识别格式 «名字»——
  全库提示词里凡是出现 «xxx» 即网络昵称，改名/排查时一处可 grep。
"""

NET_NAME = "HaruNet"
EXEC_LABEL = "goharu 智能体"
MAINT_LABEL = "arch 智能体"


def nick_token(name: str) -> str:
    """网络昵称的提示词识别格式：«名字»。"""
    return f"«{name}»"
