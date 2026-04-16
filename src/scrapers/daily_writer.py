"""
每日内容写入器 — 将 Reddit / GitHub Trending 内容写入飞书 Wiki 专区文件夹
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = "data/agent.db"

# 飞书文档 Block 类型常量
BLOCK_TEXT = 2
BLOCK_H1 = 3
BLOCK_H2 = 4
BLOCK_H3 = 5
BLOCK_BULLET = 13
BLOCK_DIVIDER = 22


def _get_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS organizer_folders (
            node_token TEXT PRIMARY KEY,
            folder_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


class DailyWriter:
    """将 Reddit / GitHub Trending 内容写入飞书 Wiki 专区"""

    REDDIT_FOLDER = "🤖 agent专区"
    GITHUB_FOLDER = "📈 github专区"

    def __init__(self, personal_client, wiki_space_id: str):
        self._client = personal_client
        self.space_id = wiki_space_id
        self._db = _get_db()
        # name → token 缓存
        self._folder_map: Dict[str, str] = {
            row[1]: row[0]
            for row in self._db.execute(
                "SELECT node_token, folder_name FROM organizer_folders"
            ).fetchall()
        }

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def write_reddit_posts(
        self, posts: List[Dict], folder_name: str = REDDIT_FOLDER
    ) -> Dict:
        """将 Reddit 帖子逐篇写入 Wiki 专区文件夹"""
        folder_token = self._ensure_folder(folder_name)
        today = datetime.now().strftime("%Y-%m-%d")
        report = {"written": 0, "failed": 0, "folder": folder_name, "date": today}

        for post in posts:
            try:
                title_cn = post.get("title_cn") or post.get("title", "无标题")
                wiki_title = f"[Reddit·AI_Agents] {title_cn[:60]} ({today})"
                blocks = self._build_reddit_blocks(post)
                node_token = self._create_wiki_page(wiki_title, blocks, folder_token)
                if node_token:
                    report["written"] += 1
                    logger.info(f"已写入: {wiki_title[:50]}")
                else:
                    report["failed"] += 1
            except Exception as e:
                logger.warning(f"写入帖子失败 [{post.get('title', '')[:30]}]: {e}")
                report["failed"] += 1

        return report

    def write_github_trending(
        self, repos: List[Dict], folder_name: str = GITHUB_FOLDER
    ) -> Dict:
        """将 GitHub Trending 日报（所有仓库汇总为一篇）写入 Wiki"""
        folder_token = self._ensure_folder(folder_name)
        today = datetime.now().strftime("%Y-%m-%d")
        wiki_title = f"GitHub Trending 日报 {today}"
        blocks = self._build_github_trending_blocks(repos, today)
        node_token = self._create_wiki_page(wiki_title, blocks, folder_token)

        if node_token:
            logger.info(f"GitHub Trending 日报已写入: {wiki_title}")
            return {"written": len(repos), "failed": 0, "folder": folder_name, "date": today}
        return {"written": 0, "failed": len(repos), "folder": folder_name, "date": today}

    # ── Block 构建 ────────────────────────────────────────────────────────────

    def _build_reddit_blocks(self, post: Dict) -> List[Dict]:
        blocks: List[Dict] = []

        # 元信息行
        parts = []
        created = post.get("created_utc", 0)
        if created:
            dt = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"时间: {dt}")
        if post.get("author"):
            parts.append(f"作者: u/{post['author']}")
        parts.append(f"👍 {post.get('score', 0)}  💬 {post.get('num_comments', 0)} 评论")
        blocks.append(_text(", ".join(parts)))
        blocks.append(_text(f"原帖链接: {post.get('permalink', '')}"))
        if post.get("flair"):
            blocks.append(_text(f"分类标签: {post['flair']}"))
        blocks.append(_divider())

        # 图片链接
        images = post.get("images", [])
        if images:
            blocks.append(_h2("图片"))
            for i, img_url in enumerate(images, 1):
                blocks.append(_text(f"图片 {i}: {img_url}"))
            blocks.append(_divider())

        # 中文内容
        title_cn = post.get("title_cn") or post.get("title", "")
        selftext_cn = post.get("selftext_cn") or post.get("selftext", "")

        blocks.append(_h2("中文标题"))
        blocks.append(_text(title_cn))

        if selftext_cn:
            blocks.append(_h2("中文正文"))
            for para in selftext_cn.split("\n\n")[:20]:
                para = para.strip()
                if para:
                    blocks.append(_text(para))

        blocks.append(_divider())

        # 原文
        blocks.append(_h2("原文 (English)"))
        blocks.append(_text(post.get("title", "")))
        if post.get("selftext"):
            blocks.append(_text(post["selftext"][:2000]))

        return blocks

    def _build_github_trending_blocks(self, repos: List[Dict], date: str) -> List[Dict]:
        blocks: List[Dict] = []
        blocks.append(_text(f"生成时间: {date}  |  共 {len(repos)} 个热门仓库"))
        blocks.append(_divider())

        for i, repo in enumerate(repos, 1):
            blocks.append(_h2(f"{i}. {repo['full_name']}"))

            info_parts = [f"⭐ 今日 +{repo.get('stars_today', 0)} stars"]
            if repo.get("total_stars"):
                info_parts.append(f"累计 {repo['total_stars']:,} stars")
            if repo.get("language"):
                info_parts.append(f"语言: {repo['language']}")
            blocks.append(_text("  ".join(info_parts)))
            blocks.append(_text(f"链接: {repo.get('url', '')}"))

            summary = repo.get("summary_cn") or repo.get("description_cn") or repo.get("description", "")
            if summary:
                blocks.append(_text(f"简介: {summary}"))

            blocks.append(_divider())

        return blocks

    # ── Wiki 操作 ─────────────────────────────────────────────────────────────

    def _ensure_folder(self, folder_name: str) -> str:
        """确保文件夹节点存在，返回 node_token"""
        if folder_name in self._folder_map:
            return self._folder_map[folder_name]

        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

        body = (
            CreateSpaceNodeRequestBody.builder()
            .obj_type("doc")
            .node_type("origin")
            .title(folder_name)
            .build()
        )
        req = (
            CreateSpaceNodeRequest.builder()
            .space_id(self.space_id)
            .request_body(body)
            .build()
        )
        resp = self._client.wiki.v2.space_node.create(req)
        if not resp.success():
            raise RuntimeError(f"创建文件夹失败: {resp.msg}")

        token = resp.data.node.node_token
        self._folder_map[folder_name] = token
        try:
            self._db.execute(
                "INSERT OR IGNORE INTO organizer_folders (node_token, folder_name) VALUES (?, ?)",
                (token, folder_name),
            )
            self._db.commit()
        except Exception as e:
            logger.warning(f"文件夹 token 缓存写入失败: {e}")

        logger.info(f"创建文件夹: {folder_name} (token={token})")
        return token

    def _create_wiki_page(
        self, title: str, blocks: List[Dict], parent_token: str = ""
    ) -> Optional[str]:
        """创建 Wiki 页面节点并写入内容块，返回 node_token"""
        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

            body_builder = (
                CreateSpaceNodeRequestBody.builder()
                .obj_type("doc")
                .node_type("origin")
                .title(title)
            )
            if parent_token:
                body_builder.parent_node_token(parent_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .request_body(body_builder.build())
                .build()
            )
            resp = self._client.wiki.v2.space_node.create(req)
            if not resp.success():
                logger.warning(f"创建页面失败: {resp.msg}")
                return None

            node_token = resp.data.node.node_token
            obj_token = resp.data.node.obj_token

            if blocks:
                self._populate_blocks(obj_token, blocks)

            return node_token

        except Exception as e:
            logger.warning(f"创建页面异常: {e}")
            return None

    def _populate_blocks(self, document_id: str, blocks: List[Dict]) -> None:
        try:
            from lark_oapi.api.docx.v1 import (
                BatchCreateDocumentBlockChildrenRequest,
                BatchCreateDocumentBlockChildrenRequestBody,
            )
            for i in range(0, len(blocks), 50):
                batch = blocks[i:i + 50]
                body = (
                    BatchCreateDocumentBlockChildrenRequestBody.builder()
                    .children(batch)
                    .build()
                )
                req = (
                    BatchCreateDocumentBlockChildrenRequest.builder()
                    .document_id(document_id)
                    .block_id(document_id)
                    .request_body(body)
                    .build()
                )
                resp = self._client.docx.v1.document_block_children.batch_create(req)
                if not resp.success():
                    logger.warning(f"写入内容块失败 (batch {i // 50}): {resp.msg}")
        except Exception as e:
            logger.warning(f"写入文档内容异常: {e}")


# ── Block 工厂函数 ────────────────────────────────────────────────────────────

def _text(content: str) -> Dict:
    return {"block_type": BLOCK_TEXT, "text": {"elements": [{"text_run": {"content": content}}]}}

def _h1(content: str) -> Dict:
    return {"block_type": BLOCK_H1, "heading1": {"elements": [{"text_run": {"content": content}}]}}

def _h2(content: str) -> Dict:
    return {"block_type": BLOCK_H2, "heading2": {"elements": [{"text_run": {"content": content}}]}}

def _h3(content: str) -> Dict:
    return {"block_type": BLOCK_H3, "heading3": {"elements": [{"text_run": {"content": content}}]}}

def _bullet(content: str) -> Dict:
    return {"block_type": BLOCK_BULLET, "bullet": {"elements": [{"text_run": {"content": content}}]}}

def _divider() -> Dict:
    return {"block_type": BLOCK_DIVIDER}
