from pathlib import Path

from Core.PromptSection import PromptSection

try:
    from configurationLoader import config as _global_config
except Exception:  # 独立使用 Prompting 包时宿主没有 configurationLoader
    _global_config = None


class _EmptyConfig:
    """无配置文件时的空配置：一切取值走默认值，路径基准为当前目录。"""

    path = "./config.yaml"

    def get(self, key, default=None):
        return default


class PromptLoader:
    def __init__(self, config_obj=None):
        self.config = config_obj or _global_config or _EmptyConfig()
        self.base_dir = Path(getattr(self.config, "path", "./config.yaml")).resolve().parent

    @staticmethod
    def _coerce_paths(raw_value):
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        if isinstance(raw_value, list):
            return [str(item) for item in raw_value if str(item).strip()]
        raise TypeError("Prompt file configuration must be a string or list of strings")

    def _resolve_path(self, path_text):
        path = Path(path_text)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()

    def load_system_sections(self, key):
        paths = self._coerce_paths(self.config.get(f"prompt.system.{key}", []))
        sections = []
        for i, path_text in enumerate(paths):
            path = self._resolve_path(path_text)
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            # 主 Actor 提示词附加当前运行环境（OS/Shell/可用命令），
            # 避免模型生成错配系统的 shell 命令
            if key == "actor" and i == len(paths) - 1:
                try:
                    from Tools.platform_utils import get_environment_hint
                    content = content + "\n\n" + get_environment_hint()
                except ImportError:
                    # 独立使用 Prompting 包时无 Tools 依赖：跳过环境便利贴
                    pass
            kwargs = dict(
                kind="system",
                title=path.stem,
                content=content,
                metadata={
                    "source_path": str(path),
                    "config_key": f"prompt.system.{key}",
                },
            )
            if i == len(paths) - 1:
                kwargs["cache_control"] = {"type": "ephemeral"}
            sections.append(PromptSection(**kwargs))
        return sections
