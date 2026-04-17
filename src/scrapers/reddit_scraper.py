"""
Reddit r/AI_Agents 爬虫
抓取 new + hot 帖子，使用 Claude API 翻译成中文
"""
import html
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SUBREDDIT = "AI_Agents"
BASE_URL = f"https://www.reddit.com/r/{SUBREDDIT}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; feishu-agent/1.0)",
    "Accept": "application/json",
}


class RedditScraper:
    """抓取 r/AI_Agents 最新和热门帖子，可选 Claude 翻译"""

    def __init__(self, api_key: str = "", model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

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
        url = f"{BASE_URL}/{sort}.json"
        try:
            resp = self.session.get(url, params={"limit": limit}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            posts = []
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                post = self._parse_post(d)
                if post:
                    posts.append(post)
            return posts
        except Exception as e:
            logger.warning(f"抓取 r/{SUBREDDIT}/{sort} 失败: {e}")
            return []

    def _parse_post(self, d: Dict) -> Optional[Dict]:
        title = d.get("title", "").strip()
        if not title:
            return None

        selftext = d.get("selftext", "").strip()
        if selftext in ("[removed]", "[deleted]"):
            selftext = ""

        return {
            "id": d.get("id", ""),
            "title": title,
            "title_cn": "",
            "selftext": selftext,
            "selftext_cn": "",
            "url": d.get("url", ""),
            "permalink": "https://www.reddit.com" + d.get("permalink", ""),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "author": d.get("author", ""),
            "created_utc": d.get("created_utc", 0),
            "images": self._extract_images(d),
            "flair": d.get("link_flair_text", ""),
        }

    def _extract_images(self, d: Dict) -> List[str]:
        images: List[str] = []

        # 方法1: preview.images
        for img in d.get("preview", {}).get("images", []):
            url = img.get("source", {}).get("url", "")
            if url:
                images.append(html.unescape(url))

        # 方法2: 帖子本身是图片链接
        post_url = d.get("url", "")
        if d.get("post_hint") == "image" or any(
            post_url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")
        ):
            if post_url not in images:
                images.append(post_url)

        # 方法3: gallery
        if d.get("is_gallery") and d.get("gallery_data"):
            media_meta = d.get("media_metadata", {})
            for item in d["gallery_data"].get("items", []):
                mid = item.get("media_id", "")
                meta = media_meta.get(mid, {})
                if meta.get("e") == "Image":
                    src = meta.get("s", {}).get("u", "")
                    if src:
                        images.append(html.unescape(src))

        return images[:3]

    # ── 翻译 ─────────────────────────────────────────────────────────────────

    def translate_posts(self, posts: List[Dict]) -> List[Dict]:
        """批量翻译帖子标题和正文（每批 5 篇）"""
        if not self.api_key:
            for p in posts:
                p["title_cn"] = p["title"]
                p["selftext_cn"] = p["selftext"]
            return posts

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            logger.warning("anthropic 未安装，跳过翻译")
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
            "格式：\n---帖子1---\n标题：<中文标题>\n正文：<中文正文，无正文写\"(无正文)\">\n"
            "---帖子2---\n...\n\n原文：\n" + "\n".join(items_text)
        )

        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
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
