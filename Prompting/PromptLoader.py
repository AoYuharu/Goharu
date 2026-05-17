from pathlib import Path

from configurationLoader import config
from Core.PromptSection import PromptSection


class PromptLoader:
    def __init__(self, config_obj=None):
        self.config = config_obj or config
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
