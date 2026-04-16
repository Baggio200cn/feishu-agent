"""
飞书邮箱管理器 — 列出/读取/回复/删除邮件
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EmailManager:
    """管理飞书邮箱（mail.v1 API）"""

    def __init__(self, client):
        self._client = client

    def list_mails(self, folder: str = "INBOX", limit: int = 20) -> List[Dict[str, Any]]:
        """列出邮件，返回邮件摘要列表"""
        try:
            from lark_oapi.api.mail.v1 import ListUserMailboxMessageRequest

            results = []
            page_token = None
            fetched = 0

            while fetched < limit:
                req_builder = (
                    ListUserMailboxMessageRequest.builder()
                    .user_mailbox_id("me")
                    .page_size(min(20, limit - fetched))
                )
                if page_token:
                    req_builder.page_token(page_token)
                resp = self._client.mail.v1.user_mailbox_message.list(req_builder.build())

                if not resp.success():
                    logger.warning(f"获取邮件列表失败: {resp.msg}")
                    break

                for mail in (resp.data.items or []):
                    results.append({
                        "message_id": mail.message_id,
                        "subject": mail.subject,
                        "from": mail.from_,
                        "date": mail.date,
                        "is_read": mail.is_read,
                    })
                    fetched += 1

                if not resp.data.has_more:
                    break
                page_token = resp.data.page_token

            return results

        except Exception as e:
            logger.warning(f"邮件列表获取异常: {e}")
            return []

    def read_mail(self, message_id: str) -> Optional[Dict[str, Any]]:
        """读取邮件详情"""
        try:
            from lark_oapi.api.mail.v1 import GetUserMailboxMessageRequest

            req = (
                GetUserMailboxMessageRequest.builder()
                .message_id(message_id)
                .build()
            )
            resp = self._client.mail.v1.user_mailbox_message.get(req)
            if not resp.success():
                logger.warning(f"读取邮件失败: {resp.msg}")
                return None

            m = resp.data.message
            return {
                "message_id": m.message_id,
                "subject": m.subject,
                "from": m.from_,
                "to": m.to,
                "date": m.date,
                "body_text": m.body_plain_text,
                "body_html": m.body_html,
            }
        except Exception as e:
            logger.warning(f"读取邮件异常: {e}")
            return None

    def reply_mail(self, message_id: str, body: str) -> bool:
        """回复邮件"""
        try:
            from lark_oapi.api.mail.v1 import ReplyUserMailboxMessageRequest, ReplyUserMailboxMessageRequestBody

            body_obj = (
                ReplyUserMailboxMessageRequestBody.builder()
                .body({"content": body, "content_type": "text"})
                .build()
            )
            req = (
                ReplyUserMailboxMessageRequest.builder()
                .message_id(message_id)
                .request_body(body_obj)
                .build()
            )
            resp = self._client.mail.v1.user_mailbox_message.reply(req)
            if resp.success():
                logger.info(f"邮件回复成功: {message_id}")
                return True
            logger.warning(f"邮件回复失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"邮件回复异常: {e}")
            return False

    def delete_mail(self, message_id: str) -> bool:
        """删除邮件（移入回收站）"""
        try:
            from lark_oapi.api.mail.v1 import DeleteUserMailboxMessageRequest

            req = (
                DeleteUserMailboxMessageRequest.builder()
                .message_id(message_id)
                .build()
            )
            resp = self._client.mail.v1.user_mailbox_message.delete(req)
            if resp.success():
                logger.info(f"邮件已删除: {message_id}")
                return True
            logger.warning(f"邮件删除失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"邮件删除异常: {e}")
            return False
