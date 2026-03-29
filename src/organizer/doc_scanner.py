"""
文档扫描器 — 扫描指定飞书账号的 Wiki 和云盘文件
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DocScanner:
    """扫描飞书账号中的所有文档（Wiki 节点 + 云盘文件）"""

    def __init__(self, client, account_name: str):
        self._client = client
        self.account_name = account_name

    def scan_wiki(self, space_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        扫描 Wiki 空间的所有节点。
        - space_id 为 None 时，自动列出账号下所有 Wiki 空间后逐个扫描。
        返回文档列表，每项格式：
        {
            "title": str,
            "node_token": str,
            "obj_token": str,
            "obj_type": str,   # "doc" / "sheet" / "mindnote" 等
            "space_id": str,
            "parent_node_token": str,
            "account": str,
            "source": "wiki"
        }
        """
        try:
            import lark_oapi as lark
            from lark_oapi.api.wiki.v2 import ListSpaceRequest, ListSpaceNodeRequest
        except ImportError:
            raise ImportError("请先安装 lark-oapi: pip install lark-oapi")

        docs = []
        space_ids = [space_id] if space_id else self._list_wiki_spaces()

        for sid in space_ids:
            logger.info(f"[{self.account_name}] 扫描 Wiki 空间: {sid}")
            nodes = self._list_nodes_recursive(sid, "")
            docs.extend(nodes)

        logger.info(f"[{self.account_name}] Wiki 扫描完成，共 {len(docs)} 个节点")
        return docs

    def scan_drive(self) -> List[Dict[str, Any]]:
        """
        扫描云盘根目录的文件（My Drive）。
        返回文件列表，格式：
        {
            "title": str,
            "token": str,
            "type": str,   # "doc" / "sheet" / "file" 等
            "account": str,
            "source": "drive"
        }
        """
        try:
            from lark_oapi.api.drive.v1 import ListFileRequest
        except ImportError:
            raise ImportError("请先安装 lark-oapi: pip install lark-oapi")

        docs = []
        page_token = None

        while True:
            req_builder = ListFileRequest.builder()
            if page_token:
                req_builder.page_token(page_token)
            req = req_builder.build()
            resp = self._client.drive.v1.file.list(req)

            if not resp.success():
                logger.warning(f"[{self.account_name}] 云盘扫描失败: {resp.msg}")
                break

            for f in (resp.data.files or []):
                docs.append({
                    "title": f.name,
                    "token": f.token,
                    "type": f.type,
                    "account": self.account_name,
                    "source": "drive",
                })

            if not resp.data.has_more:
                break
            page_token = resp.data.next_page_token

        logger.info(f"[{self.account_name}] 云盘扫描完成，共 {len(docs)} 个文件")
        return docs

    def _list_wiki_spaces(self) -> List[str]:
        """列出账号下所有 Wiki 空间 ID"""
        from lark_oapi.api.wiki.v2 import ListSpaceRequest

        space_ids = []
        page_token = None

        while True:
            req_builder = ListSpaceRequest.builder()
            if page_token:
                req_builder.page_token(page_token)
            req = req_builder.build()
            resp = self._client.wiki.v2.space.list(req)

            if not resp.success():
                logger.warning(f"[{self.account_name}] 获取 Wiki 空间列表失败: {resp.msg}")
                break

            for space in (resp.data.items or []):
                space_ids.append(space.space_id)

            if not resp.data.has_more:
                break
            page_token = resp.data.page_token

        return space_ids

    def _list_nodes_recursive(self, space_id: str, parent_node_token: str) -> List[Dict]:
        """递归列出 Wiki 空间下的所有节点"""
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

        nodes = []
        page_token = None

        while True:
            req_builder = (
                ListSpaceNodeRequest.builder()
                .space_id(space_id)
            )
            if parent_node_token:
                req_builder.parent_node_token(parent_node_token)
            if page_token:
                req_builder.page_token(page_token)
            req = req_builder.build()
            resp = self._client.wiki.v2.space_node.list(req)

            if not resp.success():
                logger.warning(f"[{self.account_name}] 节点列表获取失败 space={space_id}: {resp.msg}")
                break

            for node in (resp.data.items or []):
                nodes.append({
                    "title": node.title,
                    "node_token": node.node_token,
                    "obj_token": node.obj_token,
                    "obj_type": node.obj_type,
                    "space_id": space_id,
                    "parent_node_token": parent_node_token,
                    "account": self.account_name,
                    "source": "wiki",
                })
                # 递归扫描子节点
                if node.has_child:
                    children = self._list_nodes_recursive(space_id, node.node_token)
                    nodes.extend(children)

            if not resp.data.has_more:
                break
            page_token = resp.data.page_token

        return nodes
