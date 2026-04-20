"""
Wiki 管家 Agent — 对话式 Wiki 管理能力。

职责:
  1. 意图识别（关键词路由 → search / scan_empty / delete_empty / cleanup_prefix / suggest / confirm / cancel）
  2. Wiki 全树扫描（含 obj_token / obj_type）
  3. 空节点检测（读取 docx 正文，按字数阈值判定）
  4. 安全删除（wiki DELETE 主路径 → drive DELETE 兜底，解决 1061004 forbidden）
  5. 会话状态持久化（logs/agent_sessions/{session_id}.json，支持预览 → 确认执行两轮）
  6. 前缀批量删对接已有 cleanup-wiki 逻辑
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

SESSION_DIR = os.path.join("logs", "agent_sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

# ============ 空节点判定阈值 ============
# 正文去除空白后字数 <= 此值视为"真正空"
EMPTY_BODY_CHAR_THRESHOLD = 8
# 扫描上限（避免大 wiki 卡爆）
MAX_NODES_TO_SCAN = 500
MAX_TREE_DEPTH = 6


# ---------------------------------------------------------------------------
# 会话持久化
# ---------------------------------------------------------------------------
def new_session_id() -> str:
    return "sess-" + uuid.uuid4().hex[:12]


def load_session(session_id: str) -> Dict[str, Any]:
    if not session_id:
        return {"id": new_session_id(), "history": [], "pending_action": None}
    path = os.path.join(SESSION_DIR, f"{session_id}.json")
    if not os.path.exists(path):
        return {"id": session_id, "history": [], "pending_action": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("id", session_id)
        s.setdefault("history", [])
        s.setdefault("pending_action", None)
        return s
    except Exception:
        return {"id": session_id, "history": [], "pending_action": None}


def save_session(session: Dict[str, Any]) -> None:
    if not session.get("id"):
        session["id"] = new_session_id()
    path = os.path.join(SESSION_DIR, f"{session['id']}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"save_session 失败: {e}")


# ---------------------------------------------------------------------------
# 意图识别
# ---------------------------------------------------------------------------
def classify_intent(query: str, session: Dict[str, Any]) -> str:
    q = (query or "").strip().lower()
    if not q:
        return "search"

    # 如果有 pending_action，优先识别确认 / 取消
    has_pending = bool(session.get("pending_action"))
    if has_pending:
        if any(k in q for k in ["确认", "执行", "确认执行", "同意", "是的", "ok", "好", "yes", "go", "删吧", "继续"]):
            return "confirm"
        if any(k in q for k in ["取消", "不删", "不要", "算了", "no", "停", "放弃"]):
            return "cancel"

    # 删空类（优先级最高：先匹配动作 + 对象）
    if ("删" in q or "清" in q) and ("空" in q or "empty" in q):
        return "delete_empty"

    # 找空
    if any(k in q for k in ["找空", "查空", "扫空", "扫描空", "空文档", "空节点", "空页面", "哪些空", "空的"]):
        return "scan_empty"

    # 前缀删
    if "前缀" in q or "prefix" in q:
        return "cleanup_prefix"
    if ("批量删" in q or "全删" in q or "都删" in q) and "空" not in q:
        return "cleanup_prefix"

    # 建议
    if any(k in q for k in ["建议", "整理建议", "怎么整理", "怎么优化", "结构", "诊断"]):
        return "suggest"

    # 默认：搜 Wiki
    return "search"


# ---------------------------------------------------------------------------
# Wiki 树扫描
# ---------------------------------------------------------------------------
def scan_wiki_tree(client, wiki_space_id: str, max_nodes: int = MAX_NODES_TO_SCAN) -> List[Dict[str, Any]]:
    """
    DFS 扫全部 wiki 节点，返回:
      [{title, node_token, obj_token, obj_type, parent_node_token, has_child, depth}, ...]
    """
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    nodes: List[Dict[str, Any]] = []

    def _visit(parent_token: Optional[str], depth: int):
        if len(nodes) >= max_nodes or depth > MAX_TREE_DEPTH:
            return
        page_token = None
        for _ in range(20):
            b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50)
            if parent_token:
                b.parent_node_token(parent_token)
            if page_token:
                b.page_token(page_token)
            r = client.wiki.v2.space_node.list(b.build())
            if not r.success():
                logger.warning(f"ListSpaceNode 失败 parent={parent_token} code={r.code} msg={r.msg}")
                return
            for it in (getattr(r.data, "items", None) or []):
                entry = {
                    "title": getattr(it, "title", "") or "(未命名)",
                    "node_token": it.node_token,
                    "obj_token": getattr(it, "obj_token", "") or "",
                    "obj_type": getattr(it, "obj_type", "") or "",
                    "parent_node_token": parent_token or "",
                    "has_child": bool(getattr(it, "has_child", False)),
                    "depth": depth,
                }
                nodes.append(entry)
                if len(nodes) >= max_nodes:
                    return
                if entry["has_child"] and depth < MAX_TREE_DEPTH:
                    _visit(it.node_token, depth + 1)
                    if len(nodes) >= max_nodes:
                        return
            if not getattr(r.data, "has_more", False):
                break
            page_token = getattr(r.data, "page_token", None)
            if not page_token:
                break

    _visit(None, 0)
    return nodes


# ---------------------------------------------------------------------------
# 读 docx 正文
# ---------------------------------------------------------------------------
def read_docx_raw_content(tenant_token: str, doc_id: str, timeout: int = 10) -> Optional[str]:
    """GET /open-apis/docx/v1/documents/:doc_id/raw_content → plain text"""
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/raw_content"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {tenant_token}"}, timeout=timeout)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("code", -1) != 0:
            return None
        return (j.get("data") or {}).get("content") or ""
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 空节点检测
# ---------------------------------------------------------------------------
def detect_empty_nodes(
    client,
    tenant_token: str,
    wiki_space_id: str,
    body_threshold: int = EMPTY_BODY_CHAR_THRESHOLD,
    max_nodes: int = MAX_NODES_TO_SCAN,
) -> Dict[str, Any]:
    """
    扫全部节点 → 对每个 docx 读正文 → 判断是否空。
    其他类型（doc / sheet / file）跳过。
    """
    nodes = scan_wiki_tree(client, wiki_space_id, max_nodes=max_nodes)
    scanned = len(nodes)
    read_ok = 0
    skipped: List[Dict[str, Any]] = []
    empty_list: List[Dict[str, Any]] = []

    for n in nodes:
        obj_type = (n.get("obj_type") or "").lower()
        obj_token = n.get("obj_token") or ""
        if obj_type != "docx" or not obj_token:
            skipped.append({**n, "reason": f"不支持的类型 {obj_type or '未知'}"})
            continue
        text = read_docx_raw_content(tenant_token, obj_token)
        if text is None:
            skipped.append({**n, "reason": "读取失败（权限或网络）"})
            continue
        read_ok += 1
        stripped = re.sub(r"\s+", "", text or "")
        body_chars = len(stripped)
        if body_chars <= body_threshold:
            empty_list.append({**n, "body_chars": body_chars, "preview": (text or "").strip()[:40]})

    return {
        "scanned": scanned,
        "read_ok": read_ok,
        "skipped": skipped,
        "empty": empty_list,
        "body_threshold": body_threshold,
    }


# ---------------------------------------------------------------------------
# 飞书 tenant_access_token 获取
# ---------------------------------------------------------------------------
def get_tenant_token(app_id: str, app_secret: str, timeout: int = 10) -> Optional[str]:
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("tenant_access_token") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 删除 —— wiki DELETE 主路径 + drive DELETE 兜底
# ---------------------------------------------------------------------------
def delete_node_with_fallback(
    tenant_token: str,
    wiki_space_id: str,
    node_token: str,
    obj_token: str = "",
    obj_type: str = "",
    timeout: int = 15,
) -> Tuple[bool, str, str]:
    """
    返回 (成功?, 实际走的路径, 错误信息).

    路径 1: DELETE /wiki/v2/spaces/:sid/nodes/:tk
      → 需要应用是 wiki 空间 admin，通常返回 1061004 forbidden
    路径 2 (兜底): DELETE /drive/v1/files/:obj_token?type=:obj_type
      → 删底层 docx / doc，Wiki 节点随之消失；只要应用有 drive 读写权限即可
    """
    headers = {"Authorization": f"Bearer {tenant_token}"}

    # 路径 1: wiki node delete
    url1 = (
        f"https://open.feishu.cn/open-apis/wiki/v2/spaces/"
        f"{wiki_space_id}/nodes/{node_token}"
    )
    err1 = ""
    try:
        r1 = requests.delete(url1, headers=headers, timeout=timeout)
        if r1.status_code == 200:
            try:
                j1 = r1.json()
            except Exception:
                j1 = {}
            if j1.get("code", -1) == 0:
                return True, "wiki", ""
            err1 = f"wiki code={j1.get('code')} msg={j1.get('msg')}"
        else:
            err1 = f"wiki HTTP {r1.status_code} {r1.text[:120]}"
    except Exception as e:
        err1 = f"wiki 异常 {e}"

    logger.info(f"wiki delete 失败，尝试 drive 兜底: {err1}")

    # 路径 2: drive file delete（docx / doc / sheet）
    if not obj_token:
        return False, "none", f"{err1}；且无 obj_token 无法兜底"

    obj_type_drive = (obj_type or "docx").lower()
    if obj_type_drive not in ("docx", "doc", "sheet", "bitable", "mindnote", "file"):
        obj_type_drive = "docx"

    url2 = f"https://open.feishu.cn/open-apis/drive/v1/files/{obj_token}"
    err2 = ""
    try:
        r2 = requests.delete(
            url2,
            headers=headers,
            params={"type": obj_type_drive},
            timeout=timeout,
        )
        if r2.status_code == 200:
            try:
                j2 = r2.json()
            except Exception:
                j2 = {}
            if j2.get("code", -1) == 0:
                return True, "drive", ""
            err2 = f"drive code={j2.get('code')} msg={j2.get('msg')}"
        else:
            err2 = f"drive HTTP {r2.status_code} {r2.text[:120]}"
    except Exception as e:
        err2 = f"drive 异常 {e}"

    return False, "none", f"{err1}；兜底失败 {err2}"


def batch_delete_nodes(
    tenant_token: str,
    wiki_space_id: str,
    targets: List[Dict[str, str]],
    sleep_between: float = 0.2,
) -> Dict[str, List[Dict[str, Any]]]:
    """targets: [{title, node_token, obj_token, obj_type}, ...]"""
    deleted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for t in targets:
        ok, path, err = delete_node_with_fallback(
            tenant_token=tenant_token,
            wiki_space_id=wiki_space_id,
            node_token=t.get("node_token", ""),
            obj_token=t.get("obj_token", ""),
            obj_type=t.get("obj_type", ""),
        )
        if ok:
            deleted.append({**t, "path": path})
            logger.info(f"✅ 已删除 [{path}]: {t.get('title')}")
        else:
            failed.append({**t, "error": err})
            logger.warning(f"❌ 删除失败: {t.get('title')} — {err}")
        time.sleep(sleep_between)
    return {"deleted": deleted, "failed": failed}


# ---------------------------------------------------------------------------
# 整理建议（非 AI，按规则 + 统计）
# ---------------------------------------------------------------------------
def generate_suggestions(nodes: List[Dict[str, Any]], empty_result: Dict[str, Any]) -> List[str]:
    tips: List[str] = []
    total = len(nodes)
    if total == 0:
        return ["Wiki 空间看起来是空的。"]

    by_depth: Dict[int, int] = {}
    for n in nodes:
        by_depth[n["depth"]] = by_depth.get(n["depth"], 0) + 1

    # 顶层节点数
    top = by_depth.get(0, 0)
    if top > 15:
        tips.append(f"顶层节点有 {top} 个，建议合并到 5-8 个主题文件夹下，降低认知负担。")

    # 空节点占比
    empties = len(empty_result.get("empty", []))
    read_ok = empty_result.get("read_ok", 0) or 1
    if empties >= 5 or empties / read_ok > 0.1:
        tips.append(f"发现 {empties} 个空节点（正文 ≤ {empty_result.get('body_threshold')} 字），建议整批清理。")

    # 深度告警
    max_depth = max(by_depth.keys()) if by_depth else 0
    if max_depth >= 5:
        tips.append(f"最大层级达 {max_depth}，太深的嵌套不易检索，建议压平到 3 层以内。")

    # 重复标题
    title_map: Dict[str, int] = {}
    for n in nodes:
        t = (n.get("title") or "").strip()
        if t:
            title_map[t] = title_map.get(t, 0) + 1
    dupes = [(t, c) for t, c in title_map.items() if c >= 2]
    if dupes:
        sample = "、".join(f"{t}(×{c})" for t, c in dupes[:3])
        tips.append(f"存在同名节点 {len(dupes)} 组，如 {sample}，建议合并或加前缀区分。")

    if not tips:
        tips.append("结构看起来不错 👍 暂无明显清理点。")
    return tips
