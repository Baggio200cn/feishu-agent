"""
每日内容写入器 — 生成 Markdown 文件并通过飞书 import_tasks 导入到 Wiki 专区
"""
import io
import logging
import os
import requests
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = "data/agent.db"
FEISHU_BASE = "https://open.feishu.cn/open-apis"


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
    """将 Reddit / GitHub Trending 内容以 .md 文件导入飞书 Wiki 专区"""

    REDDIT_FOLDER = "🤖 agent专区"
    GITHUB_FOLDER = "📈 github专区"

    def __init__(self, personal_client, wiki_space_id: str):
        self._client = personal_client
        self.space_id = wiki_space_id
        self._db = _get_db()
        self._folder_map: Dict[str, str] = {
            row[1]: row[0]
            for row in self._db.execute(
                "SELECT node_token, folder_name FROM organizer_folders"
            ).fetchall()
        }
        self._token: str = ""
        self._token_expire: float = 0

    # ── Token 管理 ────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """获取飞书 tenant_access_token（带缓存，自动续期）"""
        if self._token and time.time() < self._token_expire:
            return self._token

        # 从 lark_oapi 客户端配置中取 app_id / app_secret
        cfg = self._client._config.app_settings
        resp = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": cfg.app_id, "app_secret": cfg.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("tenant_access_token", "")
        self._token_expire = time.time() + data.get("expire", 7200) - 60
        return self._token

    def _headers(self) -> Dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def write_reddit_posts(
        self, posts: List[Dict], folder_name: str = REDDIT_FOLDER
    ) -> Dict:
        """将 Reddit 帖子逐篇以 .md 导入 Wiki"""
        folder_token = self._ensure_folder(folder_name)
        today = datetime.now().strftime("%Y-%m-%d")
        report = {"written": 0, "failed": 0, "folder": folder_name, "date": today}

        for post in posts:
            try:
                title_cn = post.get("title_cn") or post.get("title", "无标题")
                wiki_title = f"[Reddit·AI_Agents] {title_cn[:60]} ({today})"
                md = self._build_reddit_md(post)
                node_token = self._import_md_to_wiki(wiki_title, md, folder_token)
                if node_token:
                    report["written"] += 1
                    logger.info(f"已导入: {wiki_title[:50]}")
                else:
                    report["failed"] += 1
            except Exception as e:
                logger.warning(f"写入帖子失败 [{post.get('title', '')[:30]}]: {e}")
                report["failed"] += 1

        return report

    def write_github_trending(
        self, repos: List[Dict], folder_name: str = GITHUB_FOLDER
    ) -> Dict:
        """将 GitHub Trending 日报（汇总为一篇）以 .md 导入 Wiki"""
        folder_token = self._ensure_folder(folder_name)
        today = datetime.now().strftime("%Y-%m-%d")
        wiki_title = f"GitHub Trending 日报 {today}"
        md = self._build_github_trending_md(repos, today)
        node_token = self._import_md_to_wiki(wiki_title, md, folder_token)

        if node_token:
            logger.info(f"GitHub Trending 日报已导入: {wiki_title}")
            return {"written": len(repos), "failed": 0, "folder": folder_name, "date": today}
        return {"written": 0, "failed": len(repos), "folder": folder_name, "date": today}

    # ── Markdown 内容构建 ─────────────────────────────────────────────────────

    def _build_reddit_md(self, post: Dict) -> str:
        lines = []

        created = post.get("created_utc", 0)
        if created:
            dt = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"**时间**: {dt}")
        if post.get("author"):
            lines.append(f"**作者**: u/{post['author']}")
        lines.append(f"**热度**: 👍 {post.get('score', 0)} | 💬 {post.get('num_comments', 0)} 评论")
        lines.append(f"**原帖**: {post.get('permalink', '')}")
        if post.get("flair"):
            lines.append(f"**标签**: {post['flair']}")
        lines.append("")
        lines.append("---")
        lines.append("")

        images = post.get("images", [])
        if images:
            lines.append("## 图片")
            for img_url in images:
                lines.append(f"![]({img_url})")
            lines.append("")

        title_cn = post.get("title_cn") or post.get("title", "")
        selftext_cn = post.get("selftext_cn") or post.get("selftext", "")

        lines.append("## 中文标题")
        lines.append(title_cn)
        lines.append("")

        if selftext_cn:
            lines.append("## 中文正文")
            lines.append(selftext_cn)
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## 原文 (English)")
        lines.append(f"### {post.get('title', '')}")
        if post.get("selftext"):
            lines.append("")
            lines.append(post["selftext"][:2000])

        return "\n".join(lines)

    def _build_github_trending_md(self, repos: List[Dict], date: str) -> str:
        lines = [
            f"# GitHub Trending 日报 {date}",
            "",
            f"> 共 {len(repos)} 个热门仓库 | 生成时间: {date}",
            "",
            "---",
            "",
        ]

        for i, repo in enumerate(repos, 1):
            url = repo.get("url", f"https://github.com/{repo['full_name']}")
            lines.append(f"## {i}. [{repo['full_name']}]({url})")
            lines.append("")
            lines.append(f"🔗 **仓库链接**: {url}")
            lines.append("")

            info = [f"⭐ 今日 +{repo.get('stars_today', 0)} stars"]
            if repo.get("total_stars"):
                info.append(f"共 {repo['total_stars']:,} stars")
            if repo.get("language"):
                info.append(f"语言: `{repo['language']}`")
            lines.append(" | ".join(info))
            lines.append("")

            summary = repo.get("summary_cn") or repo.get("description_cn") or repo.get("description", "")
            if summary:
                lines.append(f"> {summary}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    # ── Feishu API 操作 ───────────────────────────────────────────────────────

    def _ensure_folder(self, folder_name: str) -> str:
        """确保 Wiki 文件夹节点存在，返回 node_token"""
        if folder_name in self._folder_map:
            return self._folder_map[folder_name]

        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node

        node = (
            Node.builder()
            .obj_type("docx")
            .node_type("origin")
            .title(folder_name)
            .build()
        )
        req = (
            CreateSpaceNodeRequest.builder()
            .space_id(self.space_id)
            .request_body(node)
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

        logger.info(f"创建文件夹节点: {folder_name} (token={token})")
        return token

    def _import_md_to_wiki(
        self, title: str, md_content: str, parent_token: str = ""
    ) -> Optional[str]:
        """
        将 Markdown 内容作为 .md 文件导入到飞书 Wiki：
        1. 上传 .md 文件到云空间
        2. 创建 import_task（mount_type=4 挂载到 Wiki 节点）
        3. 轮询等待导入完成，返回 node_token
        """
        try:
            md_bytes = md_content.encode("utf-8")
            file_name = f"{title}.md"

            # Step 1: 上传 .md 文件
            upload_resp = requests.post(
                f"{FEISHU_BASE}/drive/v1/files/upload_all",
                headers=self._headers(),
                files={"file": (file_name, io.BytesIO(md_bytes), "text/markdown")},
                data={
                    "file_name": file_name,
                    "parent_type": "explorer",
                    "parent_node": "root",
                    "size": str(len(md_bytes)),
                },
                timeout=30,
            )
            upload_resp.raise_for_status()
            upload_data = upload_resp.json()
            if upload_data.get("code") != 0:
                logger.warning(f"上传文件失败: {upload_data.get('msg')}")
                return None
            file_token = upload_data["data"]["file_token"]

            # Step 2: 创建导入任务，挂载到 Wiki 节点
            mount_key = parent_token if parent_token else self.space_id
            imp_resp = requests.post(
                f"{FEISHU_BASE}/drive/v1/import_tasks",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={
                    "file_extension": "md",
                    "file_token": file_token,
                    "type": "docx",
                    "file_name": title,
                    "point": {
                        "mount_type": 4,      # 4 = Wiki 节点
                        "mount_key": mount_key,
                    },
                },
                timeout=30,
            )
            imp_resp.raise_for_status()
            imp_data = imp_resp.json()
            if imp_data.get("code") != 0:
                logger.warning(f"创建导入任务失败: {imp_data.get('msg')}")
                return None
            ticket = imp_data["data"]["ticket"]

            # Step 3: 轮询等待完成（最多等 60s）
            for _ in range(30):
                time.sleep(2)
                poll = requests.get(
                    f"{FEISHU_BASE}/drive/v1/import_tasks/{ticket}",
                    headers=self._headers(),
                    timeout=10,
                )
                poll_data = poll.json()
                if poll_data.get("code") != 0:
                    continue
                job = poll_data["data"]["result"]
                status = job.get("job_status")
                if status == 0:
                    node_token = job.get("token", "")
                    logger.info(f"导入成功: {title} (token={node_token})")
                    return node_token
                elif status in (2, 3):
                    logger.warning(
                        f"导入失败: status={status}, "
                        f"error={job.get('job_error_msg', '')}"
                    )
                    return None

            logger.warning(f"导入任务超时: {title}")
            return None

        except Exception as e:
            logger.warning(f"Markdown 导入异常: {e}")
            return None
