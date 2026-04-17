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

    def scan_wiki(
        self,
        space_id: Optional[str] = None,
        exclude_tokens: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        try:
            import lark_oapi as lark
            from lark_oapi.api.wiki.v2 import ListSpaceRequest, ListSpaceNodeRequest
        except ImportError:
            raise ImportError("请先安装 lark-oapi: pip install lark-oapi")

        known_folders: set = exclude_tokens or set()
        docs = []
        space_ids = [space_id] if space_id else self._list_wiki_spaces()

        for sid in space_ids:
            logger.info(f"[{self.account_name}] 扫描 Wiki 空间: {sid}")
            nodes = self._list_nodes_recursive(sid, "", known_folders)
            docs.extend(nodes)

        logger.info(f"[{self.account_name}] Wiki 扫描完成，共 {len(docs)} 个节点")
        return docs

    def scan_drive(self) -> List[Dict[str, Any]]:
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

    def _list_nodes_recursive(
        self,
        space_id: str,
        parent_node_token: str,
        exclude_tokens: Optional[set] = None,
    ) -> List[Dict]:
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

        known_folders: set = exclude_tokens or set()
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
                if node.node_token in known_folders:
                    logger.debug(f"[{self.account_name}] 跳过分类文件夹: {node.title}")
                    continue

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
                if node.has_child:
                    children = self._list_nodes_recursive(
                        space_id, node.node_token, known_folders
                    )
                    nodes.extend(children)

            if not resp.data.has_more:
                break
            page_token = resp.data.page_token

        return nodes
