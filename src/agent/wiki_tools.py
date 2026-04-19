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
    """
    查一个 docx 文档的 block 数。返回 None 表示调用失败（节点被误判前略过，不冒险）。
    空文档通常只有 1 个 root page block。
    """
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


def find_empty_nodes(
    client,
    space_id: str,
    skip_prefixes: tuple = (),
    max_scan: int = 200,
) -> Dict[str, Any]:
    """
    扫描 Wiki 找"空节点"。定义：
      - docx 节点：block 数 <= 1（只有 root page，没有实际内容）
      - 非 docx 但 has_child=False 的节点也算（保守跳过，后期扩展）

    返回：
      {
        "scanned": int,
        "empty": [{title, node_token, obj_type}, ...]
      }
    """
    nodes = list_all_nodes(client, space_id, skip_prefixes=skip_prefixes, max_nodes=max_scan)
    empty: List[Dict[str, Any]] = []
    for n in nodes:
        obj_type = n.get("obj_type") or ""
        if obj_type != "docx":
            # 非 docx 暂不判断（sheet/mindnote/bitable 的"空"定义不同）
            continue
        # 有子节点的直接视为非空（它至少是个目录壳）
        if n.get("has_child"):
            continue
        block_count = count_document_blocks(client, n["obj_token"])
        # block_count None = 读取失败，保守不当空
        if block_count is not None and block_count <= 1:
            empty.append({
                "title": n["title"],
                "node_token": n["node_token"],
                "obj_token": n["obj_token"],
                "obj_type": obj_type,
                "block_count": block_count,
            })
    return {"scanned": len(nodes), "empty": empty}


def delete_wiki_node(app_id: str, app_secret: str, space_id: str, node_token: str) -> Dict[str, Any]:
    """
    真删 Wiki 节点 —— 用 raw REST（lark-oapi 没包 DeleteSpaceNode）。
    返回 {ok: bool, error: str}
    """
    import requests

    auth_resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    auth_json = auth_resp.json() if auth_resp.status_code == 200 else {}
    token = auth_json.get("tenant_access_token", "")
    if not token:
        return {"ok": False, "error": f"获取 tenant_access_token 失败: {auth_json}"}

    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}"
    try:
        r = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        body = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and body.get("code", -1) == 0:
            return {"ok": True, "error": ""}
        return {"ok": False, "error": f"HTTP {r.status_code} {r.text[:200]}"}
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
