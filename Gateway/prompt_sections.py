"""Gateway 侧的提示词段叠加层（组合根专用）。

Prompting 只认配置里的 `prompt.system.<agent_key>` 文件列表，不认识任何具体
功能；"启用某功能时把它的提示词段追加进 actor system"这个决策属于组合根
（SessionManager）。本模块提供一个最小配置叠加对象：其余键原样委托基础
配置，仅对目标 key 追加段文件路径。

用法：
    loader = create_prompt_loader(
        PromptSectionsOverlay(config, actor=["./prompts/actor/agentnet.md"]))
    create_agent(..., prompt_loader=loader)
"""

from __future__ import annotations


class PromptSectionsOverlay:
    """带 .get/.path 的配置视图：对 `prompt.system.<key>` 追加段文件。"""

    def __init__(self, base, **extra_sections: list[str]):
        self._base = base
        self._extra = {f"prompt.system.{k}": list(v) for k, v in extra_sections.items()}

    @property
    def path(self):
        # PromptLoader 用 config.path 作为相对路径的解析基准
        return getattr(self._base, "path", "./config.yaml")

    def get(self, key, default=None):
        extra = self._extra.get(str(key))
        if extra is None:
            return self._base.get(key, default)
        base_list = self._base.get(key, []) or []
        if isinstance(base_list, str):
            base_list = [base_list]
        return list(base_list) + extra
