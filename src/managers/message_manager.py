"""
飞书 IM 消息管理器 — 查看/回复/删除/标记已读
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessageManager:
    """管理飞书 IM 消息（im.v1 API）"""

    def __init__(self, client):
        self._client = client

    def list_messages(self, chat_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取指定群聊的最近消息"""
        try:
            from lark_oapi.api.im.v1 import ListMessageRequest

            results = []
            page_token = None

            while len(results) < limit:
                req_builder = (
                    ListMessageRequest.builder()
                    .container_id_type("chat")
                    .container_id(chat_id)
                    .page_size(min(20, limit - len(results)))
                )
                if page_token:
                    req_builder.page_token(page_token)
                resp = self._client.im.v1.message.list(req_builder.build())

                if not resp.success():
                    logger.warning(f"获取消息列表失败: {resp.msg}")
                    break

                for msg in (resp.data.items or []):
                    results.append({
                        "message_id": msg.message_id,
                        "msg_type": msg.msg_type,
                        "content": msg.body.content if msg.body else "",
                        "sender_id": msg.sender.id if msg.sender else "",
                        "create_time": msg.create_time,
                    })

                if not resp.data.has_more:
                    break
                page_token = resp.data.page_token

            return results

        except Exception as e:
            logger.warning(f"消息列表获取异常: {e}")
            return []

    def reply_message(self, message_id: str, text: str) -> bool:
        """回复指定消息"""
        try:
            import json
            from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

            body = (
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": text}))
                .msg_type("text")
                .build()
            )
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.reply(req)
            if resp.success():
                logger.info(f"消息回复成功: {message_id}")
                return True
            logger.warning(f"消息回复失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"消息回复异常: {e}")
            return False

    def delete_message(self, message_id: str) -> bool:
        """撤回/删除消息"""
        try:
            from lark_oapi.api.im.v1 import DeleteMessageRequest

            req = DeleteMessageRequest.builder().message_id(message_id).build()
            resp = self._client.im.v1.message.delete(req)
            if resp.success():
                logger.info(f"消息已删除: {message_id}")
                return True
            logger.warning(f"消息删除失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"消息删除异常: {e}")
            return False

    def mark_as_read(self, message_ids: List[str]) -> bool:
        """批量标记消息为已读（使用 im.v1.message.read_users API）"""
        try:
            import json
            from lark_oapi.api.im.v1 import CreateMessageReadUsersRequest

            success_count = 0
            for mid in message_ids:
                req = (
                    CreateMessageReadUsersRequest.builder()
                    .message_id(mid)
                    .build()
                )
                resp = self._client.im.v1.message_read_users.create(req)
                if resp.success():
                    success_count += 1
                else:
                    logger.warning(f"标记已读失败 [{mid}]: {resp.msg}")

            logger.info(f"标记已读: {success_count}/{len(message_ids)}")
            return success_count == len(message_ids)
        except Exception as e:
            logger.warning(f"标记已读异常: {e}")
            return False

    def send_message(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> bool:
        """发送文本消息"""
        try:
            import json
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            body = (
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.create(req)
            if resp.success():
                logger.info(f"消息发送成功 → {receive_id}")
                return True
            logger.warning(f"消息发送失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"消息发送异常: {e}")
            return False
