"""
Reddit r/AI_Agents 爬虫
使用 RSS Feed 抓取帖子（无需 OAuth，支持代理），使用豆包 API 翻译成中文
"""
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SUBREDDIT = "AI_Agents"
RSS_BASE = "https://www.reddit.com/r/{subreddit}/{sort}.rss"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; feishu-agent/1.0; RSS reader)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

ATOM = "http://www.w3.org/2005/Atom"


def _atag(name: str) -> str:
    return f"{{{ATOM}}}{name}"


class RedditScraper:
    """通过 RSS Feed 抓取 r/AI_Agents 最新和热门帖子，可选豆包翻译"""

    def __init__(
        self,
        api_key: str = "",
        model: str = "doubao-seed-2-0-code-preview-260215",
        base_url: str = DOUBAO_BASE_URL,
        proxy: str = "",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def fetch_posts(self, limit: int = 20) -> List[Dict]:
        """抓取 new + hot 帖子，按帖子 ID 去重后返回"""
        new_posts = self._fetch_sort("new", limit)
        hot_posts = self._fetch_sort("hot", limit)

        seen_ids: set = set()
        all_posts: List[Dict] = []
        for post in new_posts + hot_posts:
            pid = post.get("id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                all_posts.append(post)

        logger.info(
            f"r/{SUBREDDIT}: 共 {len(all_posts)} 篇不重复帖子 "
            f"(new={len(new_posts)}, hot={len(hot_posts)})"
        )
        return all_posts

    def _fetch_sort(self, sort: str, limit: int) -> List[Dict]:
        url = RSS_BASE.format(subreddit=SUBREDDIT, sort=sort)
        try:
            resp = self.session.get(url, params={"limit": limit}, timeout=20)
            resp.raise_for_status()
            return self._parse_rss(resp.text)
        except Exception as e:
            logger.warning(f"抓取 r/{SUBREDDIT}/{sort} RSS 失败: {e}")
            return []

    def _parse_rss(self, xml_text: str) -> List[Dict]:
        """解析 Reddit Atom Feed，返回帖子列表"""
        posts: List[Dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"RSS XML 解析失败: {e}")
            return posts

        # Reddit 返回 Atom feed，所有标签带完整命名空间
        entries = root.findall(_atag("entry"))
        if not entries:
            # 兜底：RSS 2.0 <item>
            entries = root.findall(".//item")

        logger.debug(f"找到 {len(entries)} 个 RSS 条目")
        for entry in entries:
            post = self._parse_entry(entry)
            if post:
                posts.append(post)

        return posts

    def _parse_entry(self, entry) -> Optional[Dict]:
        try:
            title_el = entry.find(_atag("title")) or entry.find("title")
            title = html.unescape((title_el.text or "").strip()) if title_el is not None else ""
            if not title:
                return None

            # 链接：Atom 用 <link href="...">
            link_el = entry.find(_atag("link"))
            if link_el is not None:
                url = link_el.get("href", "")
            else:
                link_el = entry.find("link")
                url = (link_el.text or "") if link_el is not None else ""

            # ID → Reddit post ID (t3_xxxxx)
            id_el = entry.find(_atag("id")) or entry.find("id")
            raw_id = (id_el.text or "") if id_el is not None else url
            id_match = re.search(r"t3_([a-z0-9]+)", raw_id)
            post_id = id_match.group(1) if id_match else re.sub(r"[^a-z0-9]", "", raw_id)[-8:]

            # 正文：Atom 用 <content type="html">
            content_el = (
                entry.find(_atag("content"))
                or entry.find(_atag("summary"))
                or entry.find("description")
            )
            raw_content = (content_el.text or "") if content_el is not None else ""
            selftext = self._strip_html(html.unescape(raw_content))

            # 作者
            author_el = entry.find(f"{_atag('author')}/{_atag('name')}")
            if author_el is None:
                author_el = entry.find("author/name")
            author = (author_el.text or "").strip() if author_el is not None else ""

            # 发布时间
            updated_el = entry.find(_atag("updated")) or entry.find(_atag("published")) or entry.find("pubDate")
            created_utc = 0
            if updated_el is not None and updated_el.text:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(updated_el.text.replace("Z", "+00:00"))
                    created_utc = int(dt.timestamp())
                except Exception:
                    pass

            permalink = url if "reddit.com" in url else f"https://www.reddit.com/r/{SUBREDDIT}/comments/{post_id}/"

            return {
                "id": post_id,
                "title": title,
                "title_cn": "",
                "selftext": selftext[:2000],
                "selftext_cn": "",
                "url": url,
                "permalink": permalink,
                "score": 0,
                "num_comments": 0,
                "author": author,
                "created_utc": created_utc,
                "images": [],
                "flair": "",
            }
        except Exception as e:
            logger.warning(f"解析 RSS 条目失败: {e}")
            return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """简单去除 HTML 标签"""
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    # ── 翻译 ──────────────────────────────────────────────────────────────────

    def translate_posts(self, posts: List[Dict]) -> List[Dict]:
        """批量翻译帖子标题和正文（每批 5 篇）"""
        if not self.api_key:
            for p in posts:
                p["title_cn"] = p["title"]
                p["selftext_cn"] = p["selftext"]
            return posts

        try:
            from openai import OpenAI
            client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        except ImportError:
            logger.warning("openai 未安装，跳过翻译。请运行: pip install openai")
            for p in posts:
                p["title_cn"] = p["title"]
                p["selftext_cn"] = p["selftext"]
            return posts

        for i in range(0, len(posts), 5):
            self._translate_batch(client, posts[i:i + 5])
            if i + 5 < len(posts):
                time.sleep(0.5)

        return posts

    def _translate_batch(self, client, posts: List[Dict]):
        items_text = []
        for j, p in enumerate(posts):
            content = p["title"]
            if p["selftext"]:
                content += "\n" + p["selftext"][:400]
            items_text.append(f"---帖子{j + 1}---\n{content}")

        prompt = (
            f"请将以下 {len(posts)} 篇 Reddit 帖子翻译成中文。"
            "保留 AI、LLM、API 等专业缩写。\n"
            "格式：\n---帖子1---\n标题：<中文标题>\n正文：<中文正文，无正文填\"（无正文）\">\n"
            "---帖子2---\n...\n\n原文：\n" + "\n".join(items_text)
        )

        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
            sections = [s.strip() for s in re.split(r"---帖子\d+---", text) if s.strip()]

            for j, section in enumerate(sections):
                if j >= len(posts):
                    break
                title_m = re.search(r"标题[：:]\s*(.+)", section)
                body_m = re.search(r"正文[：:]\s*([\s\S]+)", section)

                posts[j]["title_cn"] = title_m.group(1).strip() if title_m else posts[j]["title"]
                if body_m:
                    body = body_m.group(1).strip()
                    posts[j]["selftext_cn"] = "" if body == "（无正文）" else body
                else:
                    posts[j]["selftext_cn"] = posts[j]["selftext"]

        except Exception as e:
            logger.warning(f"批量翻译失败: {e}")
            for p in posts:
                if not p.get("title_cn"):
                    p["title_cn"] = p["title"]
                if not p.get("selftext_cn"):
                    p["selftext_cn"] = p["selftext"]
