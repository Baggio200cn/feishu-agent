"""
Wiki 工具箱 — Agent 能调用的具体操作函数。

设计原则：
  1. 每个函数是原子的、幂等的、可预览的
  2. 危险操作（删除）不直接执行，需要上层先 dry_run 拿到计划
  3. 所有函数都返回结构化 dict，便于 agent 汇总结果
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def search_wiki_by_title(client, space_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """按标题关键词搜 Wiki，返回匹配的节点列表"""
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    query_lower = (query or "").strip().lower()
    if not query_lower:
        return []

    matches: List[Dict[str, Any]] = []

    def _scan(parent_token: Optional[str], depth: int):
        if len(matches) >= limit * 3 or depth > 4:
            return
        page_token = None
        for _ in range(5):
            b = ListSpaceNodeRequest.builder().space_id(space_id).page_size(50)
            if parent_token:
                b.parent_node_token(parent_token)
            if page_token:
                b.page_token(page_token)
            r = client.wiki.v2.space_node.list(b.build())
            if not r.success():
                return
            for it in (getattr(r.data, "items", None) or []):
                title = getattr(it, "title", "") or ""
                if query_lower in title.lower():
                    matches.append({
                        "title": title,
                        "node_token": it.node_token,
                        "obj_token": it.obj_token,
                        "obj_type": it.obj_type,
                        "has_child": bool(getattr(it, "has_child", False)),
                    })
                    if len(matches) >= limit * 3:
                        return
                if getattr(it, "has_child", False) and depth < 4:
                    _scan(it.node_token, depth + 1)
                    if len(matches) >= limit * 3:
                        return
            if not getattr(r.data, "has_more", False):
                break
            page_token = getattr(r.data, "page_token", None)
            if not page_token:
                break

    _scan(None, 0)

    def _score(t: str) -> int:
        t = t.lower()
        if t == query_lower: return 100
        if t.startswith(query_lower): return 50
        return 10
    matches.sort(key=lambda m: _score(m["title"]), reverse=True)
    return matches[:limit]


def list_all_nodes(
    client,
    space_id: str,
    skip_prefixes: tuple = (),
    max_nodes: int = 500,
) -> List[Dict[str, Any]]:
    """遍历 Wiki 空间所有节点，返回扁平列表。skip_prefixes 的子树不递归进入。"""
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    result: List[Dict[str, Any]] = []

    def _scan(parent_token: Optional[str], depth: int):
        if len(result) >= max_nodes or depth > 6:
            return
        page_token = None
        for _ in range(10):
            b = ListSpaceNodeRequest.builder().space_id(space_id).page_size(50)
            if parent_token:
                b.parent_node_token(parent_token)
            if page_token:
                b.page_token(page_token)
            r = client.wiki.v2.space_node.list(b.build())
            if not r.success():
                logger.warning(f"list_all_nodes: {r.code} {r.msg}")
                return
            for it in (getattr(r.data, "items", None) or []):
                title = getattr(it, "title", "") or ""
                result.append({
                    "title": title,
                    "node_token": it.node_token,
                    "obj_token": it.obj_token,
                    "obj_type": it.obj_type,
                    "parent_node_token": parent_token or "",
                    "has_child": bool(getattr(it, "has_child", False)),
                })
                if len(result) >= max_nodes:
                    return
                # skip prefix 子树不进
                if any(title.startswith(p) for p in skip_prefixes):
                    continue
                if getattr(it, "has_child", False):
                    _scan(it.node_token, depth + 1)
                    if len(result) >= max_nodes:
                        return
            if not getattr(r.data, "has_more", False):
                break
            page_token = getattr(r.data, "page_token", None)
            if not page_token:
                break

    _scan(None, 0)
    return result


def count_document_blocks(client, document_id: str, timeout_sec: int = 5) -> Optional[int]:
    """查一个 docx 文档的 block 数。返回 None 表示调用失败。"""
    try:
        from lark_oapi.api.docx.v1 import ListDocumentBlockRequest
        req = (
            ListDocumentBlockRequest.builder()
            .document_id(document_id)
            .page_size(10)
            .build()
        )
        resp = client.docx.v1.document_block.list(req)
        if not resp.success():
            logger.debug(f"count_document_blocks({document_id}): {resp.code} {resp.msg}")
            return None
        items = getattr(resp.data, "items", None) or []
        return len(items)
    except Exception as e:
        logger.debug(f"count_document_blocks 异常 {document_id}: {e}")
        return None


def get_document_text_length(client, document_id: str) -> Optional[int]:
    """
    用 RawContent API 拿文档正文纯文本长度。返回 None 表示调用失败/权限不足
    （重要：None 绝不能被判定为"空"，否则会删光所有读不到的文档！）

    纯文本含义：不包含 block 结构、不包含标题，只是正文文字。
    真正的"空文档"应该返回 0 或少数几个空白字符。
    """
    try:
        from lark_oapi.api.docx.v1 import RawContentDocumentRequest
        req = RawContentDocumentRequest.builder().document_id(document_id).build()
        resp = client.docx.v1.document.raw_content(req)
        if not resp.success():
            logger.debug(f"get_document_text_length({document_id}): {resp.code} {resp.msg}")
            return None
        content = getattr(resp.data, "content", "") or ""
        return len(content.strip())
    except Exception as e:
        logger.debug(f"get_document_text_length 异常 {document_id}: {e}")
        return None


def find_empty_nodes(
    client,
    space_id: str,
    skip_prefixes: tuple = (),
    max_scan: int = 200,
    min_text_chars: int = 5,
) -> Dict[str, Any]:
    """
    扫描 Wiki 找"空节点"。严格判定（宁漏不误杀）：
      - obj_type 必须是 docx（其他类型的"空"定义不同，保守跳过）
      - has_child 必须为 False（有子节点的一律非空）
      - RawContent API 必须成功返回（失败 = None = 不当空）
      - 纯文本长度 < min_text_chars（默认 5 字符）才算空

    关键安全: 任何一步读取失败都**不**当作空。

    Returns:
      {
        "scanned": int,          # 扫描节点数
        "checked": int,          # 实际检查了正文的节点数
        "skipped_unreadable": int,  # 读不到正文的节点数（权限或API问题）
        "empty": [{title, node_token, obj_token, text_length}, ...]
      }
    """
    nodes = list_all_nodes(client, space_id, skip_prefixes=skip_prefixes, max_nodes=max_scan)
    empty: List[Dict[str, Any]] = []
    checked = 0
    skipped_unreadable = 0

    for n in nodes:
        obj_type = n.get("obj_type") or ""
        if obj_type != "docx":
            continue
        if n.get("has_child"):
            continue

        text_len = get_document_text_length(client, n["obj_token"])
        if text_len is None:
            skipped_unreadable += 1
            continue  # 读不到，绝不判定为空
        checked += 1
        if text_len < min_text_chars:
            empty.append({
                "title": n["title"],
                "node_token": n["node_token"],
                "obj_token": n["obj_token"],
                "obj_type": obj_type,
                "text_length": text_len,
            })

    return {
        "scanned": len(nodes),
        "checked": checked,
        "skipped_unreadable": skipped_unreadable,
        "empty": empty,
    }


def delete_wiki_node(client, space_id: str, node_token: str, obj_token: str, obj_type: str = "docx") -> Dict[str, Any]:
    """
    删除 Wiki 节点 —— 飞书 Wiki v2 没有公开的 DeleteNode API（2024+），
    通过删除底层 docx/sheet 文件实现：
      DELETE /open-apis/drive/v1/files/{obj_token}?type={obj_type}

    注意：obj_token 不等于 node_token。必须传底层文件的 token。
    删除后 Wiki 节点会显示"文档已被删除"，该 Wiki 节点本身需要用户在 UI 手动清理。
    被删的 docx 进飞书回收站，30 天内可恢复。

    Returns: {ok, error, ui_residual}
    """
    try:
        from lark_oapi.api.drive.v1 import DeleteFileRequest
        req = (
            DeleteFileRequest.builder()
            .file_token(obj_token)
            .type(obj_type)
            .build()
        )
        resp = client.drive.v1.file.delete(req)
        if resp.success():
            return {
                "ok": True, "error": "",
                "ui_residual": "docx 已入回收站（30 天可恢复）；Wiki 节点壳需用户在飞书 UI 手动清理",
            }
        return {"ok": False, "error": f"{resp.code} {resp.msg}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def move_wiki_node(
    client, space_id: str, node_token: str, target_parent_token: str
) -> Dict[str, Any]:
    """移动 Wiki 节点到新父节点下"""
    try:
        from lark_oapi.api.wiki.v2 import MoveSpaceNodeRequest, MoveSpaceNodeRequestBody

        body = (
            MoveSpaceNodeRequestBody.builder()
            .target_parent_token(target_parent_token)
            .build()
        )
        req = (
            MoveSpaceNodeRequest.builder()
            .space_id(space_id)
            .node_token(node_token)
            .request_body(body)
            .build()
        )
        resp = client.wiki.v2.space_node.move(req)
        if resp.success():
            return {"ok": True, "error": ""}
        return {"ok": False, "error": f"{resp.code} {resp.msg}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
