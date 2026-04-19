"""
GitHub Trending 抓取器 — 爬 https://github.com/trending 的 HTML 页面，
解析出仓库列表（含今日新增 stars、总 stars、语言等元数据）。

GitHub 没有官方的 Trending API，只能从 HTML 解析。
"""
import logging
import re
import time
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class GitHubTrending:
    """抓取 GitHub Trending 列表"""

    def __init__(self, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout = timeout

    def fetch(
        self,
        period: str = "daily",
        language: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        抓取 Trending 列表。

        Args:
            period: 'daily' | 'weekly' | 'monthly'
            language: '' 表示全部语言；或 'python' / 'typescript' / 'go' 等
            limit:   最多返回多少条

        Returns:
            List of {
                'full_name': 'owner/repo',
                'url': 'https://github.com/owner/repo',
                'description': str,
                'language': str,
                'stars_total': int,
                'stars_today': int,   # 今日 / 本周 / 本月新增
                'forks': int,
            }
        """
        if period not in ("daily", "weekly", "monthly"):
            period = "daily"

        url = f"{TRENDING_URL}/{language.lower()}" if language else TRENDING_URL
        params = {"since": period}

        html = self._fetch_with_retry(url, params)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        articles = soup.select("article.Box-row")
        logger.info(f"[Trending] 解析出 {len(articles)} 个仓库（期间={period}, 语言={language or '全部'}）")

        results: List[Dict[str, Any]] = []
        for article in articles[:limit]:
            repo = self._parse_article(article)
            if repo:
                results.append(repo)
        return results

    def _fetch_with_retry(self, url: str, params: Dict) -> str:
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200 and len(resp.text) > 10000:
                    return resp.text
                logger.warning(f"[Trending] 第 {attempt+1}/3 次响应异常：HTTP {resp.status_code}, size={len(resp.text)}")
            except requests.RequestException as e:
                logger.warning(f"[Trending] 第 {attempt+1}/3 次请求异常：{e}")
            time.sleep(2 * (attempt + 1))
        logger.error("[Trending] 连续 3 次请求失败，放弃")
        return ""

    def _parse_article(self, article) -> Dict[str, Any]:
        """从单个 <article> 提取字段"""
        try:
            h2_a = article.select_one("h2 a")
            if not h2_a:
                return None
            href = (h2_a.get("href") or "").strip("/")
            if not href or "/" not in href:
                return None
            full_name = href

            desc_tag = article.select_one("p")
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            lang_tag = article.select_one("span[itemprop='programmingLanguage']")
            language = lang_tag.get_text(strip=True) if lang_tag else ""

            # 总 stars / forks
            muted_links = article.select("a.Link--muted")
            stars_total = 0
            forks = 0
            for link in muted_links:
                href = link.get("href") or ""
                num = self._parse_number(link.get_text(strip=True))
                if "/stargazers" in href:
                    stars_total = num
                elif "/forks" in href:
                    forks = num

            # 今日新增
            today_tag = article.select_one("span.d-inline-block.float-sm-right")
            stars_today = 0
            if today_tag:
                stars_today = self._parse_number(today_tag.get_text(strip=True))

            return {
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": description,
                "language": language,
                "stars_total": stars_total,
                "stars_today": stars_today,
                "forks": forks,
            }
        except Exception as e:
            logger.warning(f"[Trending] 解析单项失败: {e}")
            return None

    @staticmethod
    def _parse_number(text: str) -> int:
        """把 '1,280' / '45.2k' / '458 stars today' 转成整数"""
        if not text:
            return 0
        match = re.search(r"[\d,\.]+(?:k|K|m|M)?", text)
        if not match:
            return 0
        raw = match.group(0).replace(",", "")
        multiplier = 1
        if raw.lower().endswith("k"):
            multiplier = 1000
            raw = raw[:-1]
        elif raw.lower().endswith("m"):
            multiplier = 1_000_000
            raw = raw[:-1]
        try:
            return int(float(raw) * multiplier)
        except ValueError:
            return 0
