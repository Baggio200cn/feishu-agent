"""
配置加载器
"""
import json
import os
from typing import Any, Dict


class ConfigLoader:
    """加载 config/credentials.json"""

    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self._credentials = None
        self._categories = None

    def load_credentials(self) -> Dict[str, Any]:
        if self._credentials is None:
            path = os.path.join(self.config_dir, "credentials.json")
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"配置文件不存在: {path}\n"
                    f"请从 {path}.example 复制并填写配置信息"
                )
            with open(path, "r", encoding="utf-8") as f:
                self._credentials = json.load(f)
        return self._credentials

    def load_categories(self) -> Dict[str, Any]:
        if self._categories is None:
            path = os.path.join(self.config_dir, "categories.json")
            if not os.path.exists(path):
                example = os.path.join(self.config_dir, "categories.json.example")
                if os.path.exists(example):
                    with open(example, "r", encoding="utf-8") as f:
                        self._categories = json.load(f)
                else:
                    self._categories = {"categories": [], "default_category": "其他", "default_icon": "📄"}
            else:
                with open(path, "r", encoding="utf-8") as f:
                    self._categories = json.load(f)
        return self._categories

    def get_account_config(self, account: str) -> Dict[str, str]:
        """获取指定账号配置 ('personal' 或 'enterprise')"""
        return self.load_credentials()["accounts"][account]

    def get_ai_config(self) -> Dict[str, str]:
        return self.load_credentials().get("ai", {})

    def get_github_config(self) -> Dict[str, Any]:
        return self.load_credentials().get("github", {})

    def reload(self) -> None:
        """Discard cached config so the next access re-reads files from disk."""
        self._credentials = None
        self._categories = None


config_loader = ConfigLoader()
