import yaml
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

class Configuration:
    _instance = None

    def __new__(cls, path=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 支持通过环境变量指定配置文件
            if path is None:
                path = os.getenv("CONFIG_FILE", "./config.yaml")
            cls._instance._load(path)
        return cls._instance

    def _load(self, path):
        self.path = Path(path)
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f)




    def get(self, key_path, default=None):
        keys = key_path.split(".")
        val = self.data
        for k in keys:
            val = val.get(k)
            if val is None:
                return default
        return val

    def set(self, key_path, value):
        """设置配置项（支持点号分隔的路径），返回旧值"""
        keys = key_path.split(".")
        val = self.data
        for k in keys[:-1]:
            if k not in val:
                val[k] = {}
            val = val[k]
        last_key = keys[-1]
        old_value = val.get(last_key)
        val[last_key] = value
        return old_value

    def save(self):
        """将当前配置写回 YAML 文件"""
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

config = Configuration()