"""
配置加载器 — 支持从 .env 文件覆盖 credentials.json 中的敏感字段
"""
import json
import os
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class ConfigLoader:
    """
    加载 config/credentials.json，支持环境变量覆盖：
      FEISHU_PERSONAL_APP_ID, FEISHU_PERSONAL_APP_SECRET, FEISHU_PERSONAL_WIKI_SPACE_ID,
      FEISHU_ENTERPRISE_APP_ID, FEISHU_ENTERPRISE_APP_SECRET,
      AI_API_KEY, GITHUB_TOKEN
    """

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
            self._apply_env_overrides()
        return self._credentials

    def _apply_env_overrides(self):
        """用环境变量覆盖 credentials.json 中的敏感字段（适合 CI/部署场景）"""
        env_map = [
            ("FEISHU_PERSONAL_APP_ID", ["accounts", "personal", "app_id"]),
            ("FEISHU_PERSONAL_APP_SECRET", ["accounts", "personal", "app_secret"]),
            ("FEISHU_PERSONAL_WIKI_SPACE_ID", ["accounts", "personal", "wiki_space_id"]),
            ("FEISHU_PERSONAL_MAILBOX_ID", ["accounts", "personal", "mailbox_id"]),
            ("FEISHU_ENTERPRISE_APP_ID", ["accounts", "enterprise", "app_id"]),
            ("FEISHU_ENTERPRISE_APP_SECRET", ["accounts", "enterprise", "app_secret"]),
            ("AI_API_KEY", ["ai", "api_key"]),
            ("GITHUB_TOKEN", ["github", "token"]),
        ]
        for env_key, json_path in env_map:
            value = os.environ.get(env_key)
            if value:
                obj = self._credentials
                for key in json_path[:-1]:
                    obj = obj.setdefault(key, {})
                obj[json_path[-1]] = value

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
        accounts = self.load_credentials().get("accounts", {})
        if account not in accounts:
            raise ValueError(
                f"credentials.json 中未找到账号配置: accounts.{account}\n"
                f"可用账号: {list(accounts.keys())}"
            )
        return accounts[account]

    def get_ai_config(self) -> Dict[str, str]:
        return self.load_credentials().get("ai", {})

    def get_github_config(self) -> Dict[str, Any]:
        return self.load_credentials().get("github", {})


config_loader = ConfigLoader()
