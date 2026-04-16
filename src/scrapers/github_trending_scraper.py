"""
GitHub Trending 爬虫
抓取每日热门仓库，使用 Claude API 生成中文摘要
"""
import json
import logging
import re
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class GitHubTrendingScraper:
    """抓取 GitHub Trending 热门仓库，可选 Claude 生成中文摘要"""

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-haiku-4-5-20251001",
        github_token: str = "",
    ):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if github_token:
            self.session.headers["Authorization"] = f"token {github_token}"

    def fetch_trending(
        self,
        since: str = "daily",
        language: str = "",
        top_n: int = 15,
    ) -> List[Dict]:
        """
        抓取 GitHub Trending 页面。
        since: 'daily' / 'weekly' / 'monthly'
        language: 语言过滤（空 = 全部）
        top_n: 最多返回几个仓库
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("请安装 beautifulsoup4: pip install beautifulsoup4")

        params: Dict[str, str] = {"since": since}
        if language:
            params["l"] = language

        try:
            resp = self.session.get(TRENDING_URL, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"抓取 GitHub Trending 失败: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        repos: List[Dict] = []

        for article in soup.select("article.Box-row"):
            repo = self._parse_repo(article)
            if repo:
                repos.append(repo)
                if len(repos) >= top_n:
                    break

        logger.info(f"GitHub Trending 抓取到 {len(repos)} 个仓库")
        return repos

    def _parse_repo(self, article) -> Optional[Dict]:
        try:
            h2 = article.select_one("h2 a")
            if not h2:
                return None

            href = h2.get("href", "").strip("/")
            parts = href.split("/")
            if len(parts) != 2:
                return None
            owner, name = parts[0].strip(), parts[1].strip()
            full_name = f"{owner}/{name}"

            desc_el = article.select_one("p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            lang_el = article.select_one("span[itemprop='programmingLanguage']")
            language = lang_el.get_text(strip=True) if lang_el else ""

            # 今日 stars
            stars_today = 0
            for span in article.select("span.d-inline-block"):
                text = span.get_text(strip=True)
                if "star" in text.lower():
                    m = re.search(r"([\d,]+)", text)
                    if m:
                        stars_today = int(m.group(1).replace(",", ""))
                    break

            # 总 stars
            total_stars = 0
            for a_tag in article.select("a"):
                if a_tag.get("href", "").endswith("/stargazers"):
                    m = re.search(r"([\d,]+)", a_tag.get_text(strip=True))
                    if m:
                        total_stars = int(m.group(1).replace(",", ""))
                    break

            return {
                "full_name": full_name,
                "owner": owner,
                "name": name,
                "url": f"https://github.com/{full_name}",
                "description": description,
                "language": language,
                "stars_today": stars_today,
                "total_stars": total_stars,
                "description_cn": "",
                "summary_cn": "",
            }
        except Exception as e:
            logger.warning(f"解析仓库条目失败: {e}")
            return None

    # ── 摘要生成 ──────────────────────────────────────────────────────────────

    def generate_summaries(self, repos: List[Dict]) -> List[Dict]:
        """批量为仓库生成中文摘要（每批 5 个）"""
        if not self.api_key or not repos:
            for r in repos:
                r["description_cn"] = r.get("description", "")
                r["summary_cn"] = r.get("description", "（暂无摘要）")
            return repos

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            logger.warning("anthropic 未安装，跳过摘要生成")
            for r in repos:
                r["description_cn"] = r.get("description", "")
                r["summary_cn"] = r.get("description", "")
            return repos

        for i in range(0, len(repos), 5):
            self._summarize_batch(client, repos[i:i + 5])
            if i + 5 < len(repos):
                time.sleep(0.5)

        return repos

    def _summarize_batch(self, client, repos: List[Dict]):
        items = []
        for j, r in enumerate(repos):
            items.append(
                f"{j + 1}. [{r['full_name']}] {r.get('description', '')} "
                f"(语言: {r.get('language', 'N/A')}, 今日star: {r.get('stars_today', 0)})"
            )

        prompt = (
            f"你是技术专家，请为以下 {len(repos)} 个 GitHub 热门仓库生成简洁中文简介（每个 30-60 字）。"
            "包含：功能定位、主要特点、适用场景。保留 AI、LLM、API 等专业术语。\n\n"
            "仓库列表：\n" + "\n".join(items) + "\n\n"
            "请严格按以下 JSON 格式返回，不要其他文字：\n"
            '[{"index": 1, "description_cn": "...", "summary_cn": "..."}, ...]'
        )

        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

            data = json.loads(text)
            for item in data:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(repos):
                    repos[idx]["description_cn"] = item.get(
                        "description_cn", repos[idx].get("description", "")
                    )
                    repos[idx]["summary_cn"] = item.get(
                        "summary_cn", repos[idx].get("description", "")
                    )

        except Exception as e:
            logger.warning(f"批量摘要生成失败: {e}")
            for r in repos:
                if not r.get("description_cn"):
                    r["description_cn"] = r.get("description", "")
                if not r.get("summary_cn"):
                    r["summary_cn"] = r.get("description", "（暂无摘要）")
