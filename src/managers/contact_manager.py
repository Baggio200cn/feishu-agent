"""
飞书联系人管理器 — 查看/搜索联系人和部门
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContactManager:
    """管理飞书联系人（contact.v3 API）"""

    def __init__(self, client):
        self._client = client

    def list_contacts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出联系人"""
        try:
            from lark_oapi.api.contact.v3 import FindByDepartmentUserRequest

            results = []
            page_token = None

            while len(results) < limit:
                req_builder = (
                    FindByDepartmentUserRequest.builder()
                    .department_id("0")
                    .page_size(min(50, limit - len(results)))
                )
                if page_token:
                    req_builder.page_token(page_token)
                resp = self._client.contact.v3.user.find_by_department(req_builder.build())

                if not resp.success():
                    logger.warning(f"获取联系人失败: {resp.msg}")
                    break

                for user in (resp.data.items or []):
                    results.append({
                        "user_id": user.user_id,
                        "name": user.name,
                        "email": user.email,
                        "mobile": user.mobile,
                        "department_ids": user.department_ids,
                        "job_title": user.job_title,
                    })

                if not resp.data.has_more:
                    break
                page_token = resp.data.page_token

            return results

        except Exception as e:
            logger.warning(f"联系人列表获取异常: {e}")
            return []

    def search_contact(self, query: str) -> List[Dict[str, Any]]:
        """搜索联系人（按姓名/邮箱）"""
        try:
            from lark_oapi.api.contact.v3 import SearchUserRequest

            req = (
                SearchUserRequest.builder()
                .query(query)
                .page_size(20)
                .build()
            )
            resp = self._client.contact.v3.user.search(req)
            if not resp.success():
                logger.warning(f"搜索联系人失败: {resp.msg}")
                return []

            results = []
            for user in (resp.data.results or []):
                results.append({
                    "user_id": user.user_id,
                    "name": user.name,
                    "email": user.email,
                    "avatar_url": user.avatar.avatar_72 if user.avatar else "",
                })
            return results

        except Exception as e:
            logger.warning(f"搜索联系人异常: {e}")
            return []

    def get_contact(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取单个联系人详情"""
        try:
            from lark_oapi.api.contact.v3 import GetUserRequest

            req = GetUserRequest.builder().user_id(user_id).build()
            resp = self._client.contact.v3.user.get(req)
            if not resp.success():
                logger.warning(f"获取联系人详情失败: {resp.msg}")
                return None

            user = resp.data.user
            return {
                "user_id": user.user_id,
                "name": user.name,
                "email": user.email,
                "mobile": user.mobile,
                "job_title": user.job_title,
                "department_ids": user.department_ids,
                "city": user.city,
                "country": user.country,
            }
        except Exception as e:
            logger.warning(f"获取联系人详情异常: {e}")
            return None
