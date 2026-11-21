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
        # 被显式 set 过的键：save() 合并时只覆盖这些键，
        # 防止进程启动后外部对 config.yaml 的修改被内存旧快照整体覆盖
        self._dirty = {}




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
        self._dirty[key_path] = value
        return old_value

    def save(self):
        """合并式保存：以磁盘当前内容为准，只覆盖内存中显式 set 过的键。

        防两类事故：① 进程启动后 config.yaml 被外部编辑过（新增键/改值），
        内存旧快照做全量 dump 会把这些修改抹掉（2026-10-14 模型切换两次
        冲掉 task_tools 注册的事故根因）；② 全量重写本身就是危险操作。
        """
        with open(self.path, "r", encoding="utf-8") as f:
            disk_data = yaml.safe_load(f) or {}
        for key_path, value in self._dirty.items():
            keys = key_path.split(".")
            val = disk_data
            for k in keys[:-1]:
                if not isinstance(val.get(k), dict):
                    val[k] = {}
                val = val[k]
            val[keys[-1]] = value
        self.data = disk_data
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

config = Configuration()