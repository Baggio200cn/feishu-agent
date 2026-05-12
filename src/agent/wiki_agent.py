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

    # 移动节点（"把 X 移到 Y / 放到 Y / 归到 Y"）
    move_kw = ["移到", "移动到", "放到", "归到", "存放到", "存到", "迁移到", "归类到", "搬到"]
    if any(k in q for k in move_kw):
        return "move_nodes"
    # 兼容："把 X 重新放在 Y 下" / "X 重新存放到 Y"
    if "重新" in q and any(v in q for v in ["放", "存"]):
        return "move_nodes"

    # 导出为 Markdown（高优先级：避免被 suggest 的"整理"误吞）
    # 规则: 出现明确的 md / markdown / .md 标识 + 任一动词（整理 / 合并 / 汇总 / 导出 / 打包 / 做成）
    has_md_token = any(t in q for t in [".md", "markdown", " md ", "md文档", "md档"]) or q.endswith("md")
    has_export_verb = any(v in q for v in ["整理", "合并", "汇总", "导出", "打包", "做成", "导成"])
    if has_md_token and has_export_verb:
        return "export_md"
    # 兜底: 强动词 + 文档（"把 XX 合并成一个文档"）
    if any(v in q for v in ["合并成", "汇总成", "打包成", "做成"]) and ("文档" in q or "一个" in q or "一份" in q):
        return "export_md"

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

    # 建议类（只在用户**明确求建议/分析**时触发，不要因为出现"目录""分类"这种泛词就吞）
    suggest_kw = [
        "整理建议", "整理思路", "整理工作", "重新整理", "重新组织",
        "怎么整理", "怎么分类", "怎么组织", "怎么优化",
        "如何整理", "如何分类", "如何组织", "如何优化",
        "你觉得", "你认为", "帮我看看", "帮我想想", "给建议", "提建议",
        "提出整理", "提出思路", "提出方案", "诊断一下", "分析一下",
    ]
    # 注: 删去了 "建议" "结构" "分类" "目录" "重组" "优化" "诊断" 这种单字泛词，
    # 它们出现在指令的修饰位置时（如"老巴疯啦目录"）会误路由
    if any(k in q for k in suggest_kw):
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
# 整理建议
# ---------------------------------------------------------------------------
def summarize_tree_for_llm(nodes: List[Dict[str, Any]], max_top: int = 20) -> Dict[str, Any]:
    """把 Wiki 树压缩成 LLM 能消化的结构摘要：顶层目录 + 每个目录下的节点计数 + 样本标题。"""
    top_nodes = [n for n in nodes if n["depth"] == 0]
    # 把非顶层节点按"顶层祖先"分组
    tok_to_parent: Dict[str, str] = {}
    for n in nodes:
        tok_to_parent[n["node_token"]] = n.get("parent_node_token", "")

    def _root_of(tok: str) -> str:
        cur = tok
        for _ in range(10):
            p = tok_to_parent.get(cur, "")
            if not p:
                return cur
            cur = p
        return cur

    by_root: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        root = _root_of(n["node_token"])
        by_root.setdefault(root, []).append(n)

    top_summary = []
    for t in top_nodes[:max_top]:
        children = by_root.get(t["node_token"], [])
        titles = [c.get("title", "") for c in children if c.get("title")][:6]
        top_summary.append({
            "title": t.get("title", ""),
            "node_count": len(children),
            "sample_titles": titles,
        })

    return {
        "total_nodes": len(nodes),
        "top_level_count": len(top_nodes),
        "top_folders": top_summary,
    }


def generate_suggestions(nodes: List[Dict[str, Any]], empty_result: Dict[str, Any]) -> List[str]:
    """基于规则的结构性建议（LLM 不可用时的 fallback）。"""
    tips: List[str] = []
    total = len(nodes)
    if total == 0:
        return ["Wiki 空间看起来是空的。"]

    by_depth: Dict[int, int] = {}
    for n in nodes:
        by_depth[n["depth"]] = by_depth.get(n["depth"], 0) + 1

    top = by_depth.get(0, 0)
    if top > 15:
        tips.append(f"顶层节点有 {top} 个，建议合并到 5-8 个主题文件夹下，降低认知负担。")

    empties = len(empty_result.get("empty", []))
    read_ok = empty_result.get("read_ok", 0) or 1
    if empties >= 5 or empties / read_ok > 0.1:
        tips.append(f"发现 {empties} 个空节点（正文 ≤ {empty_result.get('body_threshold')} 字），建议批量标记 🗑 或清理。")

    max_depth = max(by_depth.keys()) if by_depth else 0
    if max_depth >= 5:
        tips.append(f"最大层级达 {max_depth}，嵌套过深不易检索，建议压平到 3 层以内。")

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


def ai_suggestions(
    summarizer,
    tree_overview: Dict[str, Any],
    empty_count: int,
    user_question: str = "",
    timeout: int = 60,
) -> Optional[str]:
    """把树摘要喂豆包，返回一段中文整理建议。summarizer 必须已 configured。"""
    if not summarizer or not summarizer.configured():
        return None
    try:
        overview_json = json.dumps(tree_overview, ensure_ascii=False)[:4000]
        user_prompt = (
            f"我是一个飞书 Wiki 管家 Agent。用户想让我分析他的个人 Wiki 结构并给整理建议。\n\n"
            f"用户原话: {user_question or '请对我的 Wiki 做一次整理分析'}\n\n"
            f"Wiki 结构概览 (JSON):\n{overview_json}\n\n"
            f"全 Wiki 里还有 {empty_count} 个正文为空的节点。\n\n"
            f"请用中文给 4-7 条具体可执行的整理建议，每条 1-2 句。"
            f"重点覆盖: 顶层目录命名是否清晰、是否该合并或拆分、"
            f"是否有明显重复/归类错误、空节点怎么处理、深层嵌套是否需要压平。"
            f"每条建议必须引用具体的目录名（从 top_folders 里挑），不要泛泛而谈。\n\n"
            f"返回严格 JSON: "
            '{"one_liner": "一句话总结", "detail": "用 • 开头的分条建议，换行分隔"}'
        )
        out = summarizer._chat_json(
            tag="wiki-suggest",
            user_prompt=user_prompt,
            timeout=timeout,
            retries=1,
        )
        if out:
            return out.get("detail") or out.get("one_liner") or None
    except Exception as e:
        logger.warning(f"ai_suggestions 失败: {e}")
    return None


# ---------------------------------------------------------------------------
# 指令参数抽取
# ---------------------------------------------------------------------------
def parse_index_range(query: str) -> Optional[Tuple[int, int]]:
    """
    从 query 抽数字范围。返回 (start, end)（闭区间，1-based）。
    支持:
      - "13到21" / "13至21" / "13-21" / "13~21"
      - "第 5 条到第 10 条"
      - "前 5 条" → (1, 5)
      - "后 3 条" → (-3, -1)   末尾负数表示"倒数 N 条"
    注: 先去掉 YYYY-MM-DD 这种日期，避免日期数字被误当范围。
    """
    import re as _re
    cleaned = _re.sub(r"\d{4}-\d{2}-\d{2}", " ", query)
    # 直接范围: 数字-数字（前后不是更多数字以防被长 ID 切到）
    m = _re.search(r"(?<!\d)(\d{1,3})\s*[-~到至]\s*(\d{1,3})(?!\d)", cleaned)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    # 第 N 条到第 M 条
    m = _re.search(r"第\s*(\d{1,3})\s*[条个]?\s*到\s*第?\s*(\d{1,3})", cleaned)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    # 前 N 条
    m = _re.search(r"前\s*(\d{1,3})\s*[条个]", cleaned)
    if m:
        return (1, int(m.group(1)))
    # 后 N 条 (用负数表示从末尾)
    m = _re.search(r"后\s*(\d{1,3})\s*[条个]", cleaned)
    if m:
        n = int(m.group(1))
        return (-n, -1)
    return None


def parse_dest_folder(query: str) -> Optional[str]:
    """
    从 query 抽目标目录名。规则（按优先级，先强信号后弱信号）:
      1) "即 XXX 新目录" / "即 XXX 目录"（最强：显式声明目标）
      2) "移到/放到/归到/存到 XXX"（动词后的名词）
      3) 引号 / 书名号 / 方括号包围的内容
      4) 日期 YYYY-MM-DD（弱兜底：当上面都没匹配，常见归档场景）
    """
    import re as _re
    # 1) 即 XXX 新目录
    m = _re.search(r"即\s*([^\s,，。下]+?)\s*(?:新)?(?:目录|文件夹)", query)
    if m:
        return m.group(1).strip()
    # 2) 移到/放到 等动词带目的地
    m = _re.search(
        r"(?:移到|移动到|放到|归到|存放到|存到|迁移到|搬到|放在)\s*"
        r"[\"「『]?([^\"「』」\s,，。！\?]{2,30})[\"」』]?\s*(?:下|里|中|内)?",
        query,
    )
    if m:
        return m.group(1).strip().rstrip("下里中内")
    # 3) 引号包围
    for pattern in [r"[「『]([^」』]+)[」』]", r"\"([^\"]+)\"", r"\[([^\]]+)\]"]:
        m = _re.search(pattern, query)
        if m:
            return m.group(1).strip()
    # 4) 日期（弱兜底）
    m = _re.search(r"\d{4}-\d{2}-\d{2}", query)
    if m:
        return m.group(0)
    return None


def parse_source_folder(query: str) -> Optional[str]:
    """从 query 抽"X 目录中的"中的 X。"""
    import re as _re
    patterns = [
        r"把\s*[\"「『]?([^\"「』」\s]{2,20})[\"」』]?\s*(?:目录|文件夹|专区|节点)",
        r"在\s*[\"「『]?([^\"「』」\s]{2,20})[\"」』]?\s*(?:目录|文件夹|专区|节点)\s*(?:中|下|里|内)",
        r"[\"「『]?([^\"「』」\s]{2,20})[\"」』]?\s*(?:目录|文件夹|专区|节点)\s*(?:中|下|里|内)",
    ]
    for p in patterns:
        m = _re.search(p, query)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# 移动节点
# ---------------------------------------------------------------------------
def move_nodes_by_index_range(
    client,
    wiki_space_id: str,
    source_parent: Dict[str, Any],
    index_range: Tuple[int, int],
    dest_parent_node_token: str,
) -> Dict[str, Any]:
    """
    把 source_parent 下索引在 [start, end] 范围内的子节点移到 dest_parent_node_token 下。
    索引基于 ListSpaceNode 返回顺序（1-based）。
    支持负数索引（从末尾倒数）。
    """
    from lark_oapi.api.wiki.v2 import (
        ListSpaceNodeRequest,
        MoveSpaceNodeRequest,
        MoveSpaceNodeRequestBody,
    )

    # 1. 列源父节点的全部子节点
    children: List[Dict[str, Any]] = []
    page_token = None
    for _ in range(20):
        b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50).parent_node_token(source_parent["node_token"])
        if page_token:
            b.page_token(page_token)
        r = _sdk_call_with_retry(client.wiki.v2.space_node.list, b.build())
        if not r.success():
            return {"moved": [], "failed": [],
                    "error": f"列源子节点失败: {r.code} {r.msg}"}
        for it in (getattr(r.data, "items", None) or []):
            children.append({
                "title": getattr(it, "title", "") or "(未命名)",
                "node_token": it.node_token,
            })
        if not getattr(r.data, "has_more", False):
            break
        page_token = getattr(r.data, "page_token", None)
        if not page_token:
            break

    if not children:
        return {"moved": [], "failed": [], "error": "源目录下没有子节点"}

    # 2. 解析范围（支持负数）
    total = len(children)
    orig_start, orig_end = index_range
    start, end = orig_start, orig_end
    if start < 0:
        start = max(1, total + start + 1)
    if end < 0:
        end = total + end + 1
    start = max(1, start)
    end = min(total, end)
    if start > end or end < 1:
        sample_titles = ", ".join(c["title"][:30] for c in children[:5])
        return {
            "moved": [], "failed": [],
            "error": (
                f"源目录只有 {total} 个子节点，无法取范围 [{orig_start}, {orig_end}]。"
                f"前 5 条标题: {sample_titles}"
                + ("..." if total > 5 else "")
            ),
        }

    selected = children[start - 1:end]
    if not selected:
        return {"moved": [], "failed": [], "error": "范围内无节点"}

    # 3. 逐个 move
    moved: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for node in selected:
        try:
            body = (MoveSpaceNodeRequestBody.builder()
                    .target_parent_token(dest_parent_node_token)
                    .build())
            req = (MoveSpaceNodeRequest.builder()
                   .space_id(wiki_space_id)
                   .node_token(node["node_token"])
                   .request_body(body)
                   .build())
            r = _sdk_call_with_retry(client.wiki.v2.space_node.move, req)
            if r.success():
                moved.append(node)
                logger.info(f"✅ 移动: {node['title']}")
            else:
                failed.append({**node, "error": f"{r.code} {r.msg}"})
                logger.warning(f"❌ 移动失败: {node['title']} — {r.code} {r.msg}")
        except Exception as e:
            failed.append({**node, "error": str(e)})
            logger.exception(f"移动异常: {node['title']}")
        time.sleep(0.2)

    return {
        "moved": moved, "failed": failed,
        "total_children": total,
        "range_resolved": [start, end],
    }


def create_wiki_folder_node(
    client, wiki_space_id: str, title: str, parent_node_token: Optional[str] = None
) -> Optional[str]:
    """在指定父节点下创建一个空 docx 节点，返回 node_token。"""
    from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node
    body = (Node.builder()
            .obj_type("docx")
            .node_type("origin")
            .title(title)
            .build())
    builder = (CreateSpaceNodeRequest.builder()
               .space_id(wiki_space_id)
               .request_body(body))
    if parent_node_token:
        # Node 上有 parent_node_token 字段，需要在 body 里设
        body2 = (Node.builder()
                 .obj_type("docx")
                 .node_type("origin")
                 .parent_node_token(parent_node_token)
                 .title(title)
                 .build())
        builder = (CreateSpaceNodeRequest.builder()
                   .space_id(wiki_space_id)
                   .request_body(body2))
    req = builder.build()
    try:
        r = _sdk_call_with_retry(client.wiki.v2.space_node.create, req)
        if r.success() and r.data and r.data.node:
            return r.data.node.node_token
    except Exception as e:
        logger.warning(f"create_wiki_folder_node 异常: {e}")
    return None


# ---------------------------------------------------------------------------
# 导出 Wiki 子树为 Markdown
# ---------------------------------------------------------------------------
EXPORT_DIR = os.path.join("logs", "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


def find_matching_parent_node(
    nodes: List[Dict[str, Any]], query: str
) -> Optional[Dict[str, Any]]:
    """
    在已扫好的节点列表里找一个标题最匹配 query 的"目录型节点"(has_child=True)。

    分级匹配（高优先级先返回，不进入下一级）:
      Tier 1  标题完全等于 query                                    → 立即返回
      Tier 2  query 里有日期 YYYY-MM-DD，且某节点标题完全等于该日期  → 立即返回
      Tier 3  query 里有日期，某节点标题包含该日期（取最短标题）     → 立即返回
      Tier 4  query 里有引号/书名号/方括号包围的内容，匹配该内容    → 立即返回
      Tier 5  query 整句是某标题的子串（query 长度 2-30）           → 立即返回
      Tier 6  token 集合匹配（去停用词后 2-8 字中文 / 英文）         → 取最高分
    """
    import re as _re

    q = (query or "").strip()
    q_lower = q.lower()
    has_child_nodes = [n for n in nodes if n.get("has_child")]

    if not has_child_nodes:
        return None

    # Tier 1: 完全相等
    for n in has_child_nodes:
        if (n.get("title") or "").strip() == q:
            return n

    # Tier 2 + 3: 日期信号最强
    date_match = _re.search(r"\d{4}-\d{2}-\d{2}", q)
    if date_match:
        target_date = date_match.group(0)
        # 标题正好等于日期
        for n in has_child_nodes:
            if (n.get("title") or "").strip() == target_date:
                return n
        # 标题包含日期（取最短，最精确）
        date_candidates = [n for n in has_child_nodes
                           if target_date in (n.get("title") or "")]
        if date_candidates:
            return min(date_candidates, key=lambda x: len(x.get("title") or ""))

    # Tier 4: 引号 / 书名号 / 方括号包围
    for pattern in [r"[「『]([^」』]+)[」』]", r'"([^"]+)"', r"\[([^\]]+)\]"]:
        m = _re.search(pattern, q)
        if m:
            exact = m.group(1).strip()
            for n in has_child_nodes:
                if (n.get("title") or "").strip() == exact:
                    return n
            for n in has_child_nodes:
                if exact and exact in (n.get("title") or ""):
                    return n

    # Tier 5: 短 query 整体是标题子串
    if q_lower and 2 <= len(q_lower) <= 30:
        candidates = [n for n in has_child_nodes
                      if q_lower in (n.get("title") or "").lower()]
        if candidates:
            return min(candidates, key=lambda x: len(x.get("title") or ""))

    # Tier 6: token 模糊匹配（最后兜底，必须扣掉停用词）
    stop_phrases = {
        "请把", "把", "请", "目录", "文件", "整理", "文档", "下的", "里的", "中的",
        "你理解", "你", "理解", "成单独", "单独的", "重新", "存放", "存到", "放到",
        "我", "他", "她", "我们", "你们", "他们", "的", "了", "吗", "啊", "嗯",
        "需要", "想要", "可以", "应该", "如何", "怎么", "什么",
    }
    q_tokens = set()
    for m in _re.findall(r"[一-龥]{2,8}", q):
        if m not in stop_phrases:
            q_tokens.add(m)
    for m in _re.findall(r"[A-Za-z]{3,30}", q):
        q_tokens.add(m.lower())

    best = None
    best_score = 0
    for n in has_child_nodes:
        title = (n.get("title") or "").strip()
        if not title:
            continue
        score = 0
        for tok in q_tokens:
            if tok and tok in title.lower():
                score += 20
        if score > best_score:
            best_score = score
            best = n
    return best if best_score >= 30 else None


def export_subtree_to_md(
    client,
    tenant_token: str,
    wiki_space_id: str,
    parent_node: Dict[str, Any],
    max_children: int = 50,
) -> Dict[str, Any]:
    """
    把 parent_node 下所有子节点（仅 docx）整理成一份 Markdown 文档。

    返回: {
      "parent_title": str,
      "children_count": int,
      "children_exported": int,
      "skipped": [{title, reason}, ...],
      "md_path": str,           # 本地落盘绝对路径
      "preview": str,           # 前 ~300 字预览
      "byte_size": int,
    }
    """
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    parent_title = parent_node["title"]
    parent_token = parent_node["node_token"]

    # 1. 列出 parent 下的直接子节点
    children: List[Dict[str, Any]] = []
    page_token = None
    for _ in range(20):
        b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50).parent_node_token(parent_token)
        if page_token:
            b.page_token(page_token)
        try:
            r = _sdk_call_with_retry(client.wiki.v2.space_node.list, b.build())
        except Exception as e:
            logger.warning(f"export 列子节点失败: {e}")
            break
        if not r.success():
            logger.warning(f"export ListSpaceNode 失败 code={r.code} msg={r.msg}")
            break
        for it in (getattr(r.data, "items", None) or []):
            children.append({
                "title": getattr(it, "title", "") or "(未命名)",
                "node_token": it.node_token,
                "obj_token": getattr(it, "obj_token", "") or "",
                "obj_type": getattr(it, "obj_type", "") or "",
                "has_child": bool(getattr(it, "has_child", False)),
            })
            if len(children) >= max_children:
                break
        if len(children) >= max_children or not getattr(r.data, "has_more", False):
            break
        page_token = getattr(r.data, "page_token", None)
        if not page_token:
            break

    # 2. 拼接 markdown
    md_lines: List[str] = []
    md_lines.append(f"# {parent_title}")
    md_lines.append("")
    md_lines.append(f"_由 Wiki 管家 Agent 整理 · 共 {len(children)} 条子页_")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    exported = 0
    skipped: List[Dict[str, Any]] = []
    for idx, c in enumerate(children, 1):
        title = c["title"]
        obj_type = (c.get("obj_type") or "").lower()
        obj_token = c.get("obj_token") or ""
        if obj_type != "docx" or not obj_token:
            skipped.append({"title": title, "reason": f"非 docx ({obj_type or '未知'})"})
            md_lines.append(f"## {idx}. {title}")
            md_lines.append("")
            md_lines.append(f"_（{obj_type or '未知类型'}，无法读取正文）_")
            md_lines.append("")
            continue

        text = read_docx_raw_content(tenant_token, obj_token)
        if text is None:
            skipped.append({"title": title, "reason": "读取失败"})
            md_lines.append(f"## {idx}. {title}")
            md_lines.append("")
            md_lines.append("_（读取正文失败）_")
            md_lines.append("")
            continue

        md_lines.append(f"## {idx}. {title}")
        md_lines.append("")
        # 飞书 docx raw_content 是纯文本，按双换行分段保留
        for para in re.split(r"\n\s*\n", text.strip()):
            para = para.strip()
            if para:
                md_lines.append(para)
                md_lines.append("")
        md_lines.append("---")
        md_lines.append("")
        exported += 1

    md_text = "\n".join(md_lines)

    # 3. 落盘
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", parent_title)[:80] or "export"
    out_path = os.path.abspath(os.path.join(EXPORT_DIR, f"{safe_name}.md"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    return {
        "parent_title": parent_title,
        "children_count": len(children),
        "children_exported": exported,
        "skipped": skipped,
        "md_path": out_path,
        "preview": md_text[:300] + ("..." if len(md_text) > 300 else ""),
        "byte_size": len(md_text.encode("utf-8")),
    }


# ---------------------------------------------------------------------------
# LLM 兜底意图分类 + 参数抽取
# ---------------------------------------------------------------------------
# 思路: 当规则的 classify_intent 路由到 "search"（兜底意图）时，可能是规则没覆盖
# 的复杂指令。这时让豆包做一次完整的意图判断 + 参数抽取，把结果反馈给 dispatcher。
LLM_INTENT_SYSTEM_PROMPT = """你是 Wiki 管家 Agent 的意图分类器。用户用中文给你一条指令或问题，你要判断意图并抽取参数。

可选意图（intent 字段必填，只能是其中之一）：
- search        : 查 Wiki 页面（关键词检索）
- scan_empty    : 找空节点（正文为空的页面）
- delete_empty  : 删空节点
- mark_empty    : 标记空节点（标题加 🗑[空] 前缀）
- cleanup_prefix: 按标题前缀批量删
- suggest       : 给整理建议 / 结构分析
- export_md     : 把某个 Wiki 子树整理成一份 .md 文档
- move_nodes    : 把某些节点移动到另一个目录
- confirm       : 用户在确认一个待定的操作
- cancel        : 用户取消待定操作

参数字段（按意图填，可缺）:
- target_folder : 操作的目标父目录名（如"老巴疯啦"、"github专区"）
- range_start   : 数字范围起始（1-based）
- range_end     : 数字范围结束
- dest_folder   : 目标父目录名（用于 move_nodes）
- prefix        : 标题前缀（用于 cleanup_prefix）
- keyword       : 搜索关键词（用于 search）

返回严格 JSON，例如:
  用户: "把老巴疯啦目录中 13 到 21 的文件移到 2026-05-13 下"
  你: {"intent": "move_nodes", "target_folder": "老巴疯啦", "range_start": 13, "range_end": 21, "dest_folder": "2026-05-13"}

  用户: "找 Claude 相关的资料"
  你: {"intent": "search", "keyword": "Claude"}

  用户: "整理老巴疯啦 2026-05-12 为 .md"
  你: {"intent": "export_md", "target_folder": "老巴疯啦 2026-05-12"}

不要解释，只返回 JSON。
"""


def llm_classify_intent(query: str, summarizer) -> Optional[Dict[str, Any]]:
    """
    返回 {intent, ...args} 或 None（豆包不可用 / 解析失败时）。
    """
    if not summarizer or not summarizer.configured():
        return None
    try:
        # 直接调豆包 chat（不走 summarizer 的现有 reddit/repo prompt）
        import requests as _req
        payload = {
            "model": summarizer.model,
            "messages": [
                {"role": "system", "content": LLM_INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {summarizer.api_key}",
            "Content-Type": "application/json",
        }
        r = _req.post(
            f"{summarizer.base_url}/chat/completions",
            headers=headers, json=payload, timeout=60,
        )
        if r.status_code != 200:
            logger.warning(f"llm_classify_intent HTTP {r.status_code}")
            return None
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content) if content else None
        if parsed and parsed.get("intent"):
            logger.info(f"LLM 意图分类: {parsed}")
            return parsed
    except Exception as e:
        logger.warning(f"llm_classify_intent 异常: {e}")
    return None
