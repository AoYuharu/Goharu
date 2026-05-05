import yaml
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

class Configuration:
    _instance = None
    FirstLoad = True

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
    
config = Configuration()