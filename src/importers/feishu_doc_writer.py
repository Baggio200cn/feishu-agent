"""
飞书文档写入器 — 将 Markdown / 代码内容转换为飞书 Wiki 页面
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 飞书文档 Block 类型常量
BLOCK_PAGE = 1
BLOCK_TEXT = 2
BLOCK_H1 = 3
BLOCK_H2 = 4
BLOCK_H3 = 5
BLOCK_H4 = 6
BLOCK_ORDERED_LIST = 12
BLOCK_BULLET = 13
BLOCK_CODE = 14
BLOCK_DIVIDER = 22


class FeishuDocWriter:
    """将 GitHub 仓库数据写入飞书 Wiki 页面"""

    def __init__(self, personal_client, wiki_space_id: str, target_folder_token: str = ""):
        self._client = personal_client
        self.space_id = wiki_space_id
        self.target_folder_token = target_folder_token

    def write_github_repo(self, repo_data: Dict[str, Any]) -> Optional[str]:
        """
        为单个 GitHub 仓库创建飞书 Wiki 页面。
        返回创建的 Wiki 页面 URL，失败返回 None。
        """
        title = f"[GitHub] {repo_data['full_name']}"
        blocks = self._build_repo_blocks(repo_data)

        node_token = self._create_wiki_page(title, blocks)
        if node_token:
            url = f"https://open.feishu.cn/wiki/{node_token}"
            logger.info(f"Wiki 页面已创建: {title} → {url}")
            return url
        return None

    def write_github_repos_batch(self, repos: List[Dict]) -> List[str]:
        """批量写入，返回成功创建的页面 URL 列表"""
        urls = []
        for repo in repos:
            url = self.write_github_repo(repo)
            if url:
                urls.append(url)
        return urls

    def _build_repo_blocks(self, repo: Dict) -> List[Dict]:
        """构建仓库文档的 Block 列表"""
        blocks = []

        # 仓库基本信息
        blocks.append(self._text_block(f"⭐ Stars: {repo.get('stars', 0)}  |  语言: {repo.get('language', 'N/A')}  |  链接: {repo.get('url', '')}"))
        if repo.get("description"):
            blocks.append(self._text_block(repo["description"]))
        if repo.get("topics"):
            blocks.append(self._text_block("标签: " + "、".join(repo["topics"])))
        blocks.append(self._divider())

        # README 内容
        if repo.get("readme"):
            blocks.append(self._h2_block("README"))
            readme_blocks = self._markdown_to_blocks(repo["readme"])
            blocks.extend(readme_blocks[:80])  # 限制长度避免超出 API 限制

        # 核心代码文件
        for cf in repo.get("core_files", []):
            blocks.append(self._divider())
            blocks.append(self._h2_block(f"📄 {cf['path']}"))
            lang = self._detect_language(cf["path"])
            blocks.append(self._code_block(cf["content"][:3000], lang))

        return blocks

    def _markdown_to_blocks(self, markdown: str) -> List[Dict]:
        """将 Markdown 文本逐行转换为飞书 Block 列表"""
        blocks = []
        in_code_block = False
        code_lines = []
        code_lang = ""

        for line in markdown.splitlines():
            # 代码块处理
            if line.startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_lines = []
                else:
                    in_code_block = False
                    blocks.append(self._code_block("\n".join(code_lines), code_lang))
                    code_lines = []
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            stripped = line.rstrip()
            if not stripped:
                continue

            if stripped.startswith("#### "):
                blocks.append(self._h4_block(stripped[5:]))
            elif stripped.startswith("### "):
                blocks.append(self._h3_block(stripped[4:]))
            elif stripped.startswith("## "):
                blocks.append(self._h2_block(stripped[3:]))
            elif stripped.startswith("# "):
                blocks.append(self._h1_block(stripped[2:]))
            elif stripped == "---" or stripped == "***":
                blocks.append(self._divider())
            elif stripped.startswith("- ") or stripped.startswith("* "):
                blocks.append(self._bullet_block(self._strip_inline_md(stripped[2:])))
            elif re.match(r"^\d+\. ", stripped):
                text = re.sub(r"^\d+\. ", "", stripped)
                blocks.append(self._ordered_block(self._strip_inline_md(text)))
            else:
                text = self._strip_inline_md(stripped)
                if text:
                    blocks.append(self._text_block(text))

        return blocks

    @staticmethod
    def _strip_inline_md(text: str) -> str:
        """移除内联 Markdown 标记，转为纯文本"""
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)   # 图片
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # 链接
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # 粗体
        text = re.sub(r"\*([^*]+)\*", r"\1", text)       # 斜体
        text = re.sub(r"`([^`]+)`", r"\1", text)          # 行内代码
        return text.strip()

    @staticmethod
    def _detect_language(filename: str) -> str:
        ext_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".java": "Java",
            ".sh": "Bash", ".md": "Markdown", ".json": "JSON",
            ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
        }
        for ext, lang in ext_map.items():
            if filename.endswith(ext):
                return lang
        return "PlainText"

    # --- Block 构造器 ---

    def _text_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_TEXT, "text": {"elements": [{"text_run": {"content": text}}]}}

    def _h1_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H1, "heading1": {"elements": [{"text_run": {"content": text}}]}}

    def _h2_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H2, "heading2": {"elements": [{"text_run": {"content": text}}]}}

    def _h3_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H3, "heading3": {"elements": [{"text_run": {"content": text}}]}}

    def _h4_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H4, "heading4": {"elements": [{"text_run": {"content": text}}]}}

    def _bullet_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_BULLET, "bullet": {"elements": [{"text_run": {"content": text}}]}}

    def _ordered_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_ORDERED_LIST, "ordered": {"elements": [{"text_run": {"content": text}}]}}

    def _code_block(self, code: str, language: str = "PlainText") -> Dict:
        return {"block_type": BLOCK_CODE, "code": {"language": language, "elements": [{"text_run": {"content": code}}]}}

    def _divider(self) -> Dict:
        return {"block_type": BLOCK_DIVIDER}

    def _create_wiki_page(self, title: str, blocks: List[Dict]) -> Optional[str]:
        """创建 Wiki 页面节点并写入内容，返回 node_token"""
        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node

            node_builder = (
                Node.builder()
                .obj_type("docx")
                .node_type("origin")
                .title(title)
            )
            if self.target_folder_token:
                node_builder.parent_node_token(self.target_folder_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .request_body(node_builder.build())
                .build()
            )
            resp = self._client.wiki.v2.space_node.create(req)
            if not resp.success():
                logger.warning(f"创建 Wiki 页面失败: {resp.code} {resp.msg}")
                return None

            node_token = resp.data.node.node_token
            obj_token = resp.data.node.obj_token

            # 写入内容块
            if blocks:
                self._populate_blocks(obj_token, blocks)

            return node_token

        except Exception as e:
            logger.warning(f"创建 Wiki 页面异常: {e}")
            return None

    def _populate_blocks(self, document_id: str, blocks: List[Dict]) -> None:
        """批量写入文档内容块（分批，每批最多 50 个）"""
        try:
            from lark_oapi.api.docx.v1 import (
                CreateDocumentBlockChildrenRequest,
                CreateDocumentBlockChildrenRequestBody,
            )
            batch_size = 50
            for i in range(0, len(blocks), batch_size):
                batch = blocks[i:i + batch_size]
                body = (
                    CreateDocumentBlockChildrenRequestBody.builder()
                    .children(batch)
                    .index(-1)
                    .build()
                )
                req = (
                    CreateDocumentBlockChildrenRequest.builder()
                    .document_id(document_id)
                    .block_id(document_id)
                    .request_body(body)
                    .build()
                )
                resp = self._client.docx.v1.document_block_children.create(req)
                if not resp.success():
                    # 打印完整响应体，便于诊断 invalid param 到底嫌弃哪个字段
                    raw_body = ""
                    try:
                        raw_body = resp.raw.content.decode("utf-8", errors="replace")[:800]
                    except Exception:
                        pass
                    logger.warning(
                        f"写入内容块失败 (batch {i//batch_size}): "
                        f"{resp.code} {resp.msg} · body={raw_body}"
                    )
        except Exception as e:
            logger.warning(f"写入文档内容异常: {e}")

    # =========================================================
    # GitHub Trending 日报写入
    # =========================================================

    def find_node_by_title(self, title: str, parent_node_token: str = None) -> Optional[str]:
        """
        按标题在 Wiki 空间里查找节点，返回 node_token（找不到返回 None）。

        - parent_node_token=None 时在空间根下查找
        - 遍历最多 5 页（250 个节点）
        - 匹配规则：先精确匹配，再大小写 + 前后空格容错匹配
        """
        try:
            from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest
        except ImportError as e:
            logger.warning(f"lark-oapi 缺少 ListSpaceNodeRequest: {e}")
            return None

        target_normalized = (title or "").strip().lower()
        exact_hit = None
        fuzzy_hit = None

        page_token = None
        for _ in range(5):
            builder = ListSpaceNodeRequest.builder().space_id(self.space_id).page_size(50)
            if parent_node_token:
                builder.parent_node_token(parent_node_token)
            if page_token:
                builder.page_token(page_token)
            resp = self._client.wiki.v2.space_node.list(builder.build())
            if not resp.success():
                logger.warning(f"查询节点失败: {resp.code} {resp.msg}")
                return None
            items = getattr(resp.data, "items", None) or []
            for item in items:
                item_title = getattr(item, "title", None) or ""
                if item_title == title:
                    exact_hit = item.node_token
                    break
                if item_title.strip().lower() == target_normalized and not fuzzy_hit:
                    fuzzy_hit = (item.node_token, item_title)
            if exact_hit:
                break
            if not getattr(resp.data, "has_more", False):
                break
            page_token = getattr(resp.data, "page_token", None)
            if not page_token:
                break

        if exact_hit:
            return exact_hit
        if fuzzy_hit:
            logger.info(f"按'{title}'模糊匹配到节点: '{fuzzy_hit[1]}'")
            return fuzzy_hit[0]
        return None

    def write_daily_trending_report(
        self,
        items_with_summary: List[Dict[str, Any]],
        parent_folder_title: str = "github专区",
        date_str: Optional[str] = None,
        auto_create_parent: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        在 parent_folder_title 父节点下创建一个当日"文件夹"节点，
        然后在该文件夹下为每个仓库创建一个独立页面。

        Args:
            items_with_summary: [{"repo": {...}, "summary": {one_liner, detail}}, ...]
            parent_folder_title: 父节点标题，默认 "github专区"
            date_str: 日期，默认为今天（YYYY-MM-DD）
            auto_create_parent: 找不到父节点时是否在空间根下自动创建

        Returns:
            {
                "folder_url": str,         # 日报文件夹 URL
                "folder_token": str,
                "repo_pages": [{"title": str, "url": str, "created": bool}, ...],
                "created_count": int,
                "skipped_count": int,
            }
            失败返回 None。
        """
        if not items_with_summary:
            logger.warning("日报内容为空，跳过")
            return None

        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        folder_title = f"GitHub Trending 日报 {date_str}"

        parent_token = self.find_node_by_title(parent_folder_title)
        if not parent_token:
            if not auto_create_parent:
                logger.error(
                    f"父节点 '{parent_folder_title}' 不存在且未开启自动创建"
                )
                return None
            logger.warning(
                f"父节点 '{parent_folder_title}' 不存在，自动在空间根下创建"
            )
            parent_token, _ = self._create_wiki_node(
                title=parent_folder_title,
                parent_node_token=None,
                blocks=[
                    self._h1_block(parent_folder_title),
                    self._text_block(
                        "自动生成的 GitHub Trending 日报归档。"
                        "每天会在本节点下创建一个日期文件夹，文件夹里每个仓库一页。"
                    ),
                ],
            )
            if not parent_token:
                logger.error(f"自动创建父节点 '{parent_folder_title}' 失败")
                return None
            logger.info(f"✅ 自动创建父节点: {parent_folder_title} (token={parent_token})")

        # 找/建 当日日报文件夹
        folder_token = self.find_node_by_title(folder_title, parent_node_token=parent_token)
        folder_created = False
        if folder_token:
            logger.info(f"当日文件夹已存在，续跑: {folder_title} (token={folder_token})")
        else:
            folder_token, _ = self._create_wiki_node(
                title=folder_title,
                parent_node_token=parent_token,
                blocks=self._build_folder_cover_blocks(items_with_summary, date_str),
            )
            if not folder_token:
                logger.error(f"创建日报文件夹失败: {folder_title}")
                return None
            folder_created = True
            logger.info(f"✅ 日报文件夹已创建: {folder_title}")

        # 逐个仓库创建子页面（已存在则跳过）
        repo_pages: List[Dict[str, Any]] = []
        created_count = 0
        skipped_count = 0

        for idx, item in enumerate(items_with_summary, 1):
            repo = item.get("repo", {})
            summary = item.get("summary") or {}
            full_name = repo.get("full_name", f"unknown-{idx}")
            page_title = f"{idx}. {full_name}"

            # 去重：子节点中按标题查
            existing = self.find_node_by_title(page_title, parent_node_token=folder_token)
            if existing:
                logger.info(f"  [{idx}/{len(items_with_summary)}] 跳过（已存在）: {page_title}")
                repo_pages.append({
                    "title": page_title,
                    "url": f"https://open.feishu.cn/wiki/{existing}",
                    "created": False,
                })
                skipped_count += 1
                continue

            child_token, _ = self._create_wiki_node(
                title=page_title,
                parent_node_token=folder_token,
                blocks=self._build_single_repo_blocks(repo, summary),
            )
            if child_token:
                repo_pages.append({
                    "title": page_title,
                    "url": f"https://open.feishu.cn/wiki/{child_token}",
                    "created": True,
                })
                created_count += 1
                logger.info(f"  [{idx}/{len(items_with_summary)}] 已创建: {page_title}")
            else:
                repo_pages.append({
                    "title": page_title,
                    "url": "",
                    "created": False,
                })
                logger.warning(f"  [{idx}/{len(items_with_summary)}] 创建失败: {page_title}")

        folder_url = f"https://open.feishu.cn/wiki/{folder_token}"
        logger.info(
            f"日报完成: 文件夹 {'新建' if folder_created else '续跑'} · "
            f"新建 {created_count} 页 · 跳过 {skipped_count} 页"
        )

        return {
            "folder_url": folder_url,
            "folder_token": folder_token,
            "repo_pages": repo_pages,
            "created_count": created_count,
            "skipped_count": skipped_count,
        }

    def _create_wiki_node(
        self, title: str, parent_node_token: Optional[str], blocks: Optional[List[Dict]] = None
    ) -> tuple:
        """创建一个 Wiki 节点并可选写入内容块。parent_node_token=None 表示在空间根下创建。
        返回 (node_token, obj_token) 或 (None, None)。"""
        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node

            node_builder = (
                Node.builder()
                .obj_type("docx")
                .node_type("origin")
                .title(title)
            )
            if parent_node_token:
                node_builder.parent_node_token(parent_node_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .request_body(node_builder.build())
                .build()
            )
            resp = self._client.wiki.v2.space_node.create(req)
            if not resp.success():
                logger.error(f"创建节点 '{title}' 失败: {resp.code} {resp.msg}")
                return None, None

            node_token = resp.data.node.node_token
            obj_token = resp.data.node.obj_token

            if blocks:
                self._populate_blocks(obj_token, blocks)

            return node_token, obj_token
        except Exception as e:
            logger.exception(f"创建节点 '{title}' 异常: {e}")
            return None, None

    def _build_folder_cover_blocks(
        self, items_with_summary: List[Dict[str, Any]], date_str: str
    ) -> List[Dict]:
        """日报文件夹封面：概述 + 当日 N 个仓库的简短列表"""
        blocks: List[Dict] = []
        blocks.append(self._h1_block(f"GitHub Trending 日报 · {date_str}"))
        blocks.append(
            self._text_block(
                f"自动抓取 https://github.com/trending 今日热门项目（共 {len(items_with_summary)} 个），"
                f"每个项目详情见下方子页面。"
            )
        )
        blocks.append(self._divider())

        for idx, item in enumerate(items_with_summary, 1):
            repo = item.get("repo", {})
            summary = item.get("summary") or {}
            full_name = repo.get("full_name", "?")
            one_liner = summary.get("one_liner") or repo.get("description") or "（无摘要）"
            stars_today = repo.get("stars_today", 0)
            stars_total = repo.get("stars_total", 0)
            language = repo.get("language", "N/A")

            blocks.append(self._h3_block(f"{idx}. {full_name}"))
            blocks.append(self._text_block(f"📌 {one_liner}"))
            blocks.append(
                self._text_block(
                    f"⭐ 今日 +{stars_today} · 共 {stars_total:,} · 语言: {language} · "
                    f"🔗 {repo.get('url', '')}"
                )
            )

        return blocks

    def _build_single_repo_blocks(
        self, repo: Dict[str, Any], summary: Dict[str, Any]
    ) -> List[Dict]:
        """单个仓库独立页面的内容块"""
        blocks: List[Dict] = []

        url = repo.get("url", "")
        stars_total = repo.get("stars_total", 0)
        stars_today = repo.get("stars_today", 0)
        language = repo.get("language", "未知")

        blocks.append(self._text_block(f"🔗 仓库链接: {url}"))
        blocks.append(
            self._text_block(
                f"⭐ 今日 +{stars_today} stars · 共 {stars_total:,} stars · 语言: {language}"
            )
        )

        one_liner = summary.get("one_liner") or repo.get("description") or "（无摘要）"
        blocks.append(self._text_block(f"📌 {one_liner}"))
        blocks.append(self._divider())

        blocks.append(self._h2_block("详细介绍"))
        detail = summary.get("detail") or "（AI 摘要失败，请查看原仓库 README）"
        for paragraph in self._split_paragraphs(detail):
            blocks.append(self._text_block(paragraph))

        return blocks

    @staticmethod
    def _split_paragraphs(text: str) -> List[str]:
        """按空行或换行拆段，过滤空串"""
        parts = re.split(r"\n\s*\n", text.strip())
        result = []
        for p in parts:
            p = p.strip()
            if p:
                result.append(p)
        return result or [text]
