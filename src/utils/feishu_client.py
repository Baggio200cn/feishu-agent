"""
飞书 lark_oapi.Client 工厂 — 管理个人和企业两个账号的 Client 实例
"""
import logging
from typing import Dict, Literal

logger = logging.getLogger(__name__)

AccountType = Literal["personal", "enterprise"]


class FeishuClientFactory:
    """懒初始化两个账号的 lark_oapi.Client"""

    def __init__(self, accounts_config: Dict):
        self._config = accounts_config
        self._clients: Dict[str, object] = {}

    def get_client(self, account: AccountType = "personal"):
        """获取指定账号的 lark_oapi.Client 实例（首次调用时初始化）"""
        if account not in self._clients:
            self._clients[account] = self._build_client(account)
        return self._clients[account]

    def _build_client(self, account: AccountType):
        try:
            import lark_oapi as lark
        except ImportError:
            raise ImportError("请先安装 lark-oapi: pip install lark-oapi")

        cfg = self._config.get(account)
        if not cfg:
            raise ValueError(f"credentials.json 中缺少账号配置: accounts.{account}")

        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        if not app_id or app_id.startswith("cli_personal_app_id") or app_id.startswith("cli_enterprise_app_id"):
            raise ValueError(
                f"accounts.{account}.app_id 未配置，请在 config/credentials.json 中填写真实值"
            )

        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        logger.info(f"飞书 Client 初始化完成 [{account}]: app_id={app_id[:8]}...")
        return client
