"""
Reddit RSS 收集器（标准库，无需 feedparser / API 密钥）

调用示例：
    from src.scrapers.reddit_rss_collector import RedditRSSCollector
    from src.scrapers.reddit_image_downloader import download_post_images

    collector = RedditRSSCollector()
    posts = collector.fetch("ClaudeCode", limit=5)
    for post in posts:
        paths = download_post_images(post["image_urls"], "images/", post["id"])
        print(post["title"], "→", paths)

错误路径（CLAUDE.md §1）：
  Layer 1 — HTTP 请求最多重试 2 次，指数退避
  Layer 2 — 单条 entry 解析失败 → 跳过，继续下一条
  Layer 3 — 整个 subreddit 请求失败 → 结构化日志，返回 []
"""

import html
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

_RSS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BASE = "https://www.reddit.com"

# XML 命名空间
_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}


# ── 数据结构 ───────────────────────────────────────────────────────────────────

@dataclass
class RunState:
    """
    单次运行的工作内存（CLAUDE.md §2）。
    支持 checkpoint 断点续跑：seen_ids 记录已处理帖子，避免重复。
    """
    output_dir: str
    seen_ids: Set[str]      = field(default_factory=set)
    posts_fetched: int      = 0
    images_downloaded: int  = 0
    errors: List[str]       = field(default_factory=list)

    @classmethod
    def load(cls, output_dir: str) -> "RunState":
        """从 checkpoint.json 恢复；文件不存在或损坏则创建新实例。"""
        path = os.path.join(output_dir, "checkpoint.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                state = cls(
                    output_dir=output_dir,
                    seen_ids=set(d.get("seen_ids", [])),
                    posts_fetched=d.get("posts_fetched", 0),
                    images_downloaded=d.get("images_downloaded", 0),
                    errors=d.get("errors", []),
                )
                logger.info("checkpoint_loaded seen=%d", len(state.seen_ids))
                return state
            except Exception as e:
                logger.warning("checkpoint_corrupt error=%s, starting fresh", e)
        os.makedirs(output_dir, exist_ok=True)
        return cls(output_dir=output_dir)

    def save(self) -> None:
        """每处理一条帖子后调用（CLAUDE.md §2：checkpoint after each unit）。"""
        path = os.path.join(self.output_dir, "checkpoint.json")
        try:
            data = asdict(self)
            data["seen_ids"] = list(self.seen_ids)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("checkpoint_save_failed error=%s", e)


# ── 收集器 ─────────────────────────────────────────────────────────────────────

class RedditRSSCollector:
    """
    通过 RSS Feed 抓取 Reddit 热帖。

    不需要 API 密钥，不需要第三方库。
    """

    def __init__(self, proxy: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = _RSS_UA
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
            logger.info("proxy_enabled url=%s", proxy)

    # ── 公共方法 ───────────────────────────────────────────────────────────────

    def fetch(
        self,
        subreddit: str,
        limit: int = 10,
        mode: str = "hot",
        seen_ids: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        抓取 r/{subreddit} 的 {limit} 条帖子。

        Args:
            subreddit: 不含 r/ 前缀，如 "ClaudeCode"
            limit:     最多返回条数
            mode:      "hot"（默认）或 "top"
            seen_ids:  已见过的帖子 ID 集合，自动跳过

        Returns:
            帖子列表，每条包含：
            id, title, url, author, score, created_date,
            selftext, post_type, image_urls, subreddit
        """
        seen_ids = seen_ids or set()
        rss_url  = f"{_BASE}/r/{subreddit}/{mode}.rss"
        params   = {"limit": 100}  # 多取，过滤后再截断

        logger.info("rss_fetch subreddit=%s mode=%s url=%s", subreddit, mode, rss_url)

        # Layer 1：重试
        xml_text = self._fetch_with_retry(rss_url, params)
        if xml_text is None:
            # 抛出让 fetch_multi Layer 2 捕获并写入 state.errors
            raise RuntimeError(f"rss_fetch_failed subreddit={subreddit}")

        logger.info("rss_fetched subreddit=%s mode=%s", subreddit, mode)
        return self._parse(xml_text, subreddit, limit, seen_ids)

    def fetch_multi(
        self,
        subreddits: List[str],
        limit_each: int = 5,
        mode: str = "hot",
        state: Optional[RunState] = None,
    ) -> List[Dict]:
        """
        批量抓取多个 subreddit，每条帖子处理后写 checkpoint。
        单个 subreddit 失败不影响其余（CLAUDE.md §1 Layer 2）。
        """
        all_posts: List[Dict] = []
        seen = state.seen_ids if state else set()

        for sub in subreddits:
            try:
                posts = self.fetch(sub, limit=limit_each, mode=mode, seen_ids=seen)
            except Exception as e:
                # Layer 2：单个 subreddit 降级
                logger.warning("subreddit_skip subreddit=%s error=%s", sub, e)
                if state:
                    state.errors.append(f"subreddit={sub}: {e}")
                continue

            for post in posts:
                seen.add(post["id"])
                if state:
                    state.posts_fetched += 1
                    state.save()   # CLAUDE.md §2：每条帖子后 checkpoint
                all_posts.append(post)

        return all_posts

    # ── 内部实现 ───────────────────────────────────────────────────────────────

    def _fetch_with_retry(self, url: str, params: dict, retries: int = 2) -> Optional[str]:
        """CLAUDE.md §1 具体重试模式。"""
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=30)
                r.raise_for_status()
                return r.text
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(2 ** attempt)   # 1s → 2s
                    continue
                logger.warning("fetch_failed url=%s error=%s", url, e)
                return None

    def _parse(
        self,
        xml_text: str,
        subreddit: str,
        limit: int,
        seen_ids: Set[str],
    ) -> List[Dict]:
        """解析 Atom XML，每条 entry 独立处理（Layer 2：单条失败跳过）。"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("xml_parse_failed subreddit=%s error=%s", subreddit, e)
            return []

        posts: List[Dict] = []
        for entry in root.findall("atom:entry", _NS):
            if len(posts) >= limit:
                break
            try:
                post = self._parse_entry(entry, subreddit)
            except Exception as e:
                # Layer 2：单条解析失败 → 跳过
                logger.warning("entry_parse_failed subreddit=%s error=%s", subreddit, e)
                continue

            if not post or post["id"] in seen_ids:
                continue
            posts.append(post)

        logger.info("rss_parsed subreddit=%s count=%d", subreddit, len(posts))
        return posts

    def _parse_entry(self, entry: ET.Element, subreddit: str) -> Optional[Dict]:
        """从单条 Atom <entry> 提取标准化帖子数据。"""
        # ID：格式 "t3_xxxxxx"
        raw_id = _text(entry, "atom:id", _NS) or ""
        post_id = raw_id.split("_")[-1] if "_" in raw_id else raw_id
        if not post_id:
            return None

        title = html.unescape(_text(entry, "atom:title", _NS) or "")
        link  = entry.find("atom:link", _NS)
        url   = link.get("href", "") if link is not None else ""

        # 作者
        author_el = entry.find("atom:author/atom:name", _NS)
        author = author_el.text if author_el is not None else "Unknown"

        # 时间
        updated = _text(entry, "atom:updated", _NS) or ""
        try:
            created_date = datetime.fromisoformat(
                updated.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            created_date = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 正文（HTML → 纯文字，截断 8000 字符，CLAUDE.md §2 token budget）
        content_el = entry.find("atom:content", _NS)
        content_html = content_el.text if content_el is not None else ""
        selftext = _strip_html(content_html)[:8000]

        # 图片（media:thumbnail 优先）
        image_urls = _extract_images(entry, content_html)

        return {
            "id":           post_id,
            "title":        title,
            "url":          url,
            "author":       author,
            "score":        0,          # RSS 不提供分数
            "created_date": created_date,
            "selftext":     selftext,
            "post_type":    _guess_type(url, image_urls),
            "image_urls":   image_urls,
            "subreddit":    subreddit,
        }


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _text(el: ET.Element, tag: str, ns: dict) -> Optional[str]:
    child = el.find(tag, ns)
    return child.text if child is not None else None


def _extract_images(entry: ET.Element, content_html: str) -> List[str]:
    """
    从 <media:thumbnail> 和 HTML content 中提取图片 URL（最多 3 张）。
    全部经 html.unescape() 解码。
    """
    urls: List[str] = []

    # 1. media:thumbnail（最可靠）
    for thumb in entry.findall("media:thumbnail", _NS):
        if len(urls) >= 3:
            break
        raw = thumb.get("url", "")
        url = html.unescape(raw.strip())
        if url and url not in urls:
            urls.append(url)

    # 2. media:content
    for mc in entry.findall("media:content", _NS):
        if len(urls) >= 3:
            break
        raw = mc.get("url", "")
        url = html.unescape(raw.strip())
        if url and url not in urls:
            urls.append(url)

    # 3. <img src> in HTML content（兜底）
    if len(urls) < 3 and content_html:
        for raw in re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']', content_html, re.IGNORECASE
        ):
            if len(urls) >= 3:
                break
            url = html.unescape(raw.strip())
            if not url or url.startswith("data:"):
                continue
            if any(kw in url for kw in ("emoji", "icon", "snoo", "static")):
                continue
            if url not in urls:
                urls.append(url)

    return urls[:3]


def _strip_html(html_text: str) -> str:
    """移除 HTML 标签，还原实体。"""
    if not html_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = html.unescape(text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _guess_type(url: str, image_urls: List[str]) -> str:
    if image_urls:
        return "image" if len(image_urls) == 1 else "gallery"
    if "reddit.com/r/" in url and "/comments/" in url:
        return "self"
    return "link"
