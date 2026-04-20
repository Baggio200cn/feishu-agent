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
from requests.exceptions import ConnectionError as _ReqConnectionError, SSLError, Timeout

logger = logging.getLogger(__name__)

SESSION_DIR = os.path.join("logs", "agent_sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

# ============ 网络重试配置 ============
_RETRY_EXCEPTIONS = (SSLError, _ReqConnectionError, Timeout)
_RETRY_BACKOFF = (1.0, 2.5, 5.0)   # 三次，间隔递增


def _http_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """requests 调用 + SSL/连接错误自动重试三次。"""
    last: Optional[Exception] = None
    for attempt, delay in enumerate([0.0, *_RETRY_BACKOFF]):
        if delay:
            time.sleep(delay)
        try:
            return requests.request(method, url, **kwargs)
        except _RETRY_EXCEPTIONS as e:
            last = e
            logger.warning(f"{method} {url[:80]} 第 {attempt+1} 次失败: {type(e).__name__} {e}")
            continue
    if last:
        raise last
    raise RuntimeError("retry exhausted without exception")


def _sdk_call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """给 lark-oapi SDK 调用加上重试（SDK 内部也是 requests）。"""
    last: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        if attempt:
            time.sleep(_RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)])
        try:
            return fn(*args, **kwargs)
        except _RETRY_EXCEPTIONS as e:
            last = e
            logger.warning(f"SDK 调用第 {attempt+1} 次失败: {type(e).__name__} {e}")
            continue
    if last:
        raise last

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

    # 打标记（改名加 🗑[空] 前缀 —— 比真删权限要求低）
    if any(k in q for k in ["标记", "改名", "打标", "贴标", "加前缀"]) and "空" in q:
        return "mark_empty"
    if "改名" in q and ("空" in q or "empty" in q):
        return "mark_empty"

    # 删空类（优先级次高：先匹配动作 + 对象）
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
            req = b.build()
            try:
                r = _sdk_call_with_retry(client.wiki.v2.space_node.list, req)
            except Exception as e:
                logger.warning(f"ListSpaceNode 网络异常 parent={parent_token}: {e}，跳过此子树")
                return
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
        r = _http_with_retry(
            "GET", url,
            headers={"Authorization": f"Bearer {tenant_token}"},
            timeout=timeout,
        )
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
    ⚠️ 跳过 has_child=True 的节点（目录型父页通常本身正文也空但子下还有内容，不能删）。
    其他类型（doc / sheet / file）也跳过。
    """
    nodes = scan_wiki_tree(client, wiki_space_id, max_nodes=max_nodes)
    scanned = len(nodes)
    read_ok = 0
    skipped: List[Dict[str, Any]] = []
    empty_list: List[Dict[str, Any]] = []

    for n in nodes:
        obj_type = (n.get("obj_type") or "").lower()
        obj_token = n.get("obj_token") or ""
        # 有子节点 = 目录型，即便正文空也不视为"可删空节点"
        if n.get("has_child"):
            skipped.append({**n, "reason": "目录型（有子节点），不视为空"})
            continue
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
        r = _http_with_retry(
            "POST",
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
# 删除 —— 只走 drive（wiki v2 API 没有 delete-node 方法）
# ---------------------------------------------------------------------------
# 历史背景：我曾以为 `DELETE /wiki/v2/spaces/:sid/nodes/:tk` 是有效端点，
# 实测返回 404 page not found。查 lark-oapi 1.5.4 SDK 源码确认 Wiki v2 根本没封
# 装任何 Delete 类 Request（只有 Create / Get / List / Copy / Move / UpdateTitle）。
# 唯一能用的是 drive DELETE，删掉底层 docx，Wiki 节点随之消失。
# 但 drive DELETE 要求应用对该 docx 有管理权限；个人 Wiki 下由用户创建的 docx
# owner 是用户本人，应用 tenant_access_token 通常返 1061004 forbidden。
# 所以：删不了不是 bug，是飞书 API 限制。
def delete_node_via_drive(
    tenant_token: str,
    node_token: str,
    obj_token: str,
    obj_type: str = "docx",
    timeout: int = 15,
) -> Tuple[bool, str]:
    """
    返回 (成功?, 错误信息).
    端点: DELETE /open-apis/drive/v1/files/:file_token?type=:obj_type
    """
    if not obj_token:
        return False, "无 obj_token"
    obj_type_clean = (obj_type or "docx").lower()
    if obj_type_clean not in ("docx", "doc", "sheet", "bitable", "mindnote", "file", "slides"):
        obj_type_clean = "docx"

    url = f"https://open.feishu.cn/open-apis/drive/v1/files/{obj_token}"
    headers = {"Authorization": f"Bearer {tenant_token}"}
    try:
        r = _http_with_retry("DELETE", url, headers=headers, params={"type": obj_type_clean}, timeout=timeout)
        if r.status_code == 200:
            try:
                j = r.json()
            except Exception:
                j = {}
            if j.get("code", -1) == 0:
                return True, ""
            return False, f"code={j.get('code')} msg={j.get('msg')}"
        return False, f"HTTP {r.status_code} {r.text[:160]}"
    except Exception as e:
        return False, f"异常 {e}"


def batch_delete_nodes(
    tenant_token: str,
    wiki_space_id: str,
    targets: List[Dict[str, str]],
    sleep_between: float = 0.15,
) -> Dict[str, List[Dict[str, Any]]]:
    """targets: [{title, node_token, obj_token, obj_type}, ...]"""
    deleted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for t in targets:
        ok, err = delete_node_via_drive(
            tenant_token=tenant_token,
            node_token=t.get("node_token", ""),
            obj_token=t.get("obj_token", ""),
            obj_type=t.get("obj_type", ""),
        )
        if ok:
            deleted.append({**t, "path": "drive"})
            logger.info(f"✅ 已删除: {t.get('title')}")
        else:
            failed.append({**t, "error": err})
            logger.warning(f"❌ 删除失败: {t.get('title')} — {err}")
        time.sleep(sleep_between)
    return {"deleted": deleted, "failed": failed}


# ---------------------------------------------------------------------------
# 替代路径：改名打标记（用 SDK 的 UpdateTitleSpaceNode）
# ---------------------------------------------------------------------------
# drive DELETE 失败时的替代：把空节点标题加 🗑[空] 前缀，用户在 Wiki UI 里
# 一眼可识别、肉眼批量选删。UpdateTitle 的权限宽松得多（应用作为节点成员就行）。
def mark_node_titles(
    client,
    wiki_space_id: str,
    targets: List[Dict[str, Any]],
    prefix: str = "🗑[空] ",
    sleep_between: float = 0.15,
) -> Dict[str, List[Dict[str, Any]]]:
    from lark_oapi.api.wiki.v2 import (
        UpdateTitleSpaceNodeRequest,
        UpdateTitleSpaceNodeRequestBody,
    )
    marked: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for t in targets:
        title = t.get("title") or ""
        if title.startswith(prefix):
            marked.append({**t, "new_title": title})
            continue
        new_title = (prefix + title)[:80]
        try:
            body = UpdateTitleSpaceNodeRequestBody.builder().title(new_title).build()
            req = (UpdateTitleSpaceNodeRequest.builder()
                   .space_id(wiki_space_id)
                   .node_token(t["node_token"])
                   .request_body(body)
                   .build())
            resp = _sdk_call_with_retry(client.wiki.v2.space_node.update_title, req)
            if resp.success():
                marked.append({**t, "new_title": new_title})
                logger.info(f"🏷  已标记: {title} → {new_title}")
            else:
                failed.append({**t, "error": f"code={resp.code} msg={resp.msg}"})
                logger.warning(f"标记失败: {title} — {resp.code} {resp.msg}")
        except Exception as e:
            failed.append({**t, "error": f"异常 {e}"})
            logger.exception(f"标记异常: {title}")
        time.sleep(sleep_between)
    return {"marked": marked, "failed": failed}


def build_wiki_urls(targets: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """给前端展示用：把 targets 转为 [{title, url}]，点 URL 可在 Wiki UI 里打开节点手动处理。"""
    return [
        {"title": t.get("title", ""),
         "url": f"https://open.feishu.cn/wiki/{t['node_token']}"}
        for t in targets if t.get("node_token")
    ]


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
