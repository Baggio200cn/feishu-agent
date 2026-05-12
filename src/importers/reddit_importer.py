"""
Reddit 帖子抓取器 — 从多个 subreddit 拉取 top of day 帖子，含正文 + 高赞评论。

用 Reddit 的 public JSON 端点（不需要 OAuth），只要 UA：
  https://www.reddit.com/r/{sub}/top.json?t=day&limit=10
  https://www.reddit.com/r/{sub}/comments/{post_id}.json?limit=N&sort=top

注：Reddit 在中国大陆被墙，必须走 VPN。本模块依赖 requests 库，
会自动识别 HTTP_PROXY / HTTPS_PROXY 环境变量。
"""
import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class RedditImporter:
    """按 subreddit + 排序 抓取帖子列表，并拉取每个帖子的高赞评论"""

    def __init__(
        self,
        subreddits: List[str],
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 20,
    ):
        self.subreddits = subreddits
        self.timeout = timeout
        self.session = requests.Session()
        # 浏览器风格 UA + 明确要 JSON，绕过 Reddit 对"简陋 UA"的反爬
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def fetch_daily(
        self,
        period: str = "day",
        per_sub_fetch: int = 10,
        limit_total: int = 10,
        top_comments: int = 6,
    ) -> List[Dict[str, Any]]:
        """
        从所有订阅 subreddit 抓 top of {period}，合并去重排序，取前 limit_total 条。

        Returns:
            [{
                'id': str,
                'subreddit': str,
                'title': str,
                'author': str,
                'score': int,
                'num_comments': int,
                'url': str,           # 外链 URL（self post = reddit 帖子本身）
                'permalink': str,     # https://reddit.com/r/xxx/comments/yyy
                'is_self': bool,
                'selftext': str,
                'top_comments': [{'author': str, 'score': int, 'body': str}, ...],
            }, ...]
        """
        all_posts: List[Dict[str, Any]] = []
        for sub in self.subreddits:
            posts = self._fetch_subreddit_top(sub, period=period, limit=per_sub_fetch)
            all_posts.extend(posts)
            time.sleep(1)  # Reddit 不限速就好，每个 sub 间隔 1s

        # 去重
        seen: set = set()
        deduped: List[Dict[str, Any]] = []
        for p in all_posts:
            pid = p.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                deduped.append(p)

        # 按 score 降序
        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = deduped[:limit_total]
        logger.info(
            f"[Reddit] 共抓 {len(all_posts)} 条，去重 {len(deduped)} 条，取前 {len(top)} 条"
        )

        # 抓评论
        for i, post in enumerate(top, 1):
            logger.info(
                f"[Reddit] [{i}/{len(top)}] 抓评论 r/{post['subreddit']}/{post['id']}"
            )
            post["top_comments"] = self._fetch_top_comments(
                post["subreddit"], post["id"],
                limit=top_comments,
                post_author=post.get("author", ""),
            )
            time.sleep(0.5)

        return top

    def _fetch_subreddit_top(
        self, sub: str, period: str = "day", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """拉某个 subreddit 的 top 帖子列表"""
        url = f"https://www.reddit.com/r/{sub}/top.json"
        params = {"t": period, "limit": limit, "raw_json": 1}

        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                logger.warning(f"[Reddit] r/{sub} 第 {attempt+1}/3 次请求异常: {e}")
                time.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 200:
                break
            logger.warning(
                f"[Reddit] r/{sub} 第 {attempt+1}/3 次返回 HTTP {resp.status_code}"
            )
            time.sleep(2 * (attempt + 1))
        else:
            logger.error(f"[Reddit] r/{sub} 连续 3 次失败，跳过")
            return []

        try:
            data = resp.json()
        except ValueError as e:
            logger.warning(f"[Reddit] r/{sub} JSON 解析失败: {e}")
            return []

        items = data.get("data", {}).get("children", [])
        results: List[Dict[str, Any]] = []
        for item in items:
            d = item.get("data", {})
            pid = d.get("id")
            if not pid:
                continue
            permalink_path = d.get("permalink", "")
            results.append({
                "id": pid,
                "subreddit": d.get("subreddit", sub),
                "title": d.get("title", ""),
                "author": d.get("author", ""),
                "score": int(d.get("score", 0) or 0),
                "num_comments": int(d.get("num_comments", 0) or 0),
                "url": d.get("url", ""),
                "permalink": f"https://www.reddit.com{permalink_path}" if permalink_path else "",
                "is_self": bool(d.get("is_self", False)),
                "selftext": d.get("selftext", "") or "",
                "created_utc": d.get("created_utc", 0),
                "link_flair_text": d.get("link_flair_text", "") or "",
            })
        logger.info(f"[Reddit] r/{sub} 拿到 {len(results)} 条 top-of-{period}")
        return results

    def _fetch_top_comments(
        self, sub: str, post_id: str, limit: int = 3, post_author: str = ""
    ) -> List[Dict[str, Any]]:
        """
        拉某个帖子的评论（只取顶层，深度 1），返回:
          [{author, score, body, is_stickied, is_op}, ...]
        排序规则:
          1) 置顶评论（is_stickied）优先
          2) 原帖作者的顶层回复优先
          3) 剩余按 score 降序
        limit 是"非置顶/非 OP 高赞评论"的数量；置顶 / OP 另外额外保留（全部）。
        """
        url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json"
        params = {"limit": max(limit * 3, 20), "sort": "top", "depth": 1, "raw_json": 1}

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning(f"[Reddit] 评论请求异常 {post_id}: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"[Reddit] 评论 HTTP {resp.status_code} {post_id}")
            return []

        try:
            data = resp.json()
        except ValueError:
            return []

        # Reddit 评论接口返回 [post_listing, comments_listing]
        if not isinstance(data, list) or len(data) < 2:
            return []

        children = data[1].get("data", {}).get("children", [])
        stickied_list: List[Dict[str, Any]] = []
        op_list: List[Dict[str, Any]] = []
        other_list: List[Dict[str, Any]] = []

        for c in children:
            if c.get("kind") != "t1":
                continue
            cd = c.get("data", {})
            body = (cd.get("body") or "").strip()
            if not body or body in ("[deleted]", "[removed]"):
                continue
            author = cd.get("author", "") or ""
            entry = {
                "author": author,
                "score": int(cd.get("score", 0) or 0),
                "body": body[:1200],  # 扩到 1200 字
                "is_stickied": bool(cd.get("stickied", False)),
                "is_op": bool(post_author and author and author.lower() == post_author.lower()),
            }
            if entry["is_stickied"]:
                stickied_list.append(entry)
            elif entry["is_op"]:
                op_list.append(entry)
            else:
                other_list.append(entry)

        other_list.sort(key=lambda c: c["score"], reverse=True)
        op_list.sort(key=lambda c: c["score"], reverse=True)

        # 置顶 + OP 不计入 limit，额外保留
        return stickied_list + op_list + other_list[:limit]
