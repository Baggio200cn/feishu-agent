"""
GitHub 仓库导入器 — 获取 README + 核心代码文件，供后续写入飞书
"""
import base64
import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CORE_FILE_PATTERNS = [
    "README.md", "readme.md", "README.rst",
    "main.py", "app.py", "index.py", "cli.py",
    "main.js", "index.js", "app.js",
    "main.ts", "index.ts",
    "setup.py", "pyproject.toml", "package.json",
]


class GitHubImporter:
    """从 GitHub 获取仓库内容，返回结构化数据供飞书文档写入器使用"""

    def __init__(self, token: str = ""):
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        self.authenticated = False
        if token and not token.startswith("ghp_your_"):
            self.headers["Authorization"] = f"token {token}"
            self.authenticated = True
        else:
            logger.warning(
                "GitHub token 未配置或仍为占位值，将使用匿名访问（60 次/小时，易被限流）"
            )
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.rate_limit_remaining: Optional[int] = None
        self.rate_limit_reset: Optional[int] = None

    def _request_with_retry(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """带限频感知和指数退避的请求封装"""
        backoff = 2
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, timeout=15, **kwargs)
            except requests.RequestException as e:
                logger.warning(f"[GitHub] 请求异常 {url}: {e}, 第 {attempt + 1}/3 次")
                time.sleep(backoff)
                backoff *= 2
                continue

            # 记录限频状态
            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset = resp.headers.get("X-RateLimit-Reset")
            if remaining is not None:
                self.rate_limit_remaining = int(remaining)
            if reset is not None:
                self.rate_limit_reset = int(reset)

            # 403/429 = 限频，等到 reset 再重试
            if resp.status_code in (403, 429):
                wait = max(1, (self.rate_limit_reset or 0) - int(time.time()))
                if wait > 300:
                    logger.error(f"[GitHub] 触发限频，距离重置 {wait}s（超过 5 分钟），放弃本次")
                    return resp
                logger.warning(f"[GitHub] 触发限频，等待 {wait}s 后重试（剩余 {self.rate_limit_remaining}）")
                time.sleep(wait + 1)
                continue

            return resp

        logger.error(f"[GitHub] 请求 {url} 连续失败，跳过")
        return None

    def import_repo_list(self, repo_list: List[str]) -> List[Dict[str, Any]]:
        """导入指定仓库列表，格式：['owner/repo', ...]"""
        results = []
        for repo_full_name in repo_list:
            logger.info(f"导入仓库: {repo_full_name}")
            data = self.fetch_repo(repo_full_name)
            if data:
                results.append(data)
        return results

    def search_by_topics(self, topics: List[str], per_topic: int = 5) -> List[Dict[str, Any]]:
        """按主题搜索 GitHub 仓库，取 stars 最高的几个"""
        results = []
        for topic in topics:
            logger.info(f"搜索主题: {topic}")
            repos = self._search_repos(topic, per_topic)
            for repo in repos:
                data = self.fetch_repo(repo["full_name"])
                if data:
                    results.append(data)
        return results

    def fetch_repo(self, repo_full_name: str) -> Optional[Dict[str, Any]]:
        """
        获取单个仓库的元数据 + README + 核心代码文件。
        """
        try:
            meta = self._get_repo_meta(repo_full_name)
            if not meta:
                return None

            readme = self._get_readme(repo_full_name)
            core_files = self._get_core_files(repo_full_name)

            return {
                "name": meta.get("name", ""),
                "full_name": meta.get("full_name", ""),
                "description": meta.get("description", ""),
                "url": meta.get("html_url", ""),
                "stars": meta.get("stargazers_count", 0),
                "language": meta.get("language", ""),
                "topics": meta.get("topics", []),
                "readme": readme,
                "core_files": core_files,
            }
        except Exception as e:
            logger.warning(f"获取仓库失败 [{repo_full_name}]: {e}")
            return None

    def _get_repo_meta(self, repo_full_name: str) -> Optional[Dict]:
        resp = self._request_with_retry("GET", f"https://api.github.com/repos/{repo_full_name}")
        if not resp:
            return None
        if resp.status_code == 404:
            logger.warning(f"仓库不存在: {repo_full_name}")
            return None
        if resp.status_code != 200:
            logger.warning(f"获取仓库元信息失败 [{repo_full_name}]: HTTP {resp.status_code}")
            return None
        return resp.json()

    def _get_readme(self, repo_full_name: str) -> str:
        """获取 README 内容（Markdown 格式）"""
        resp = self._request_with_retry("GET", f"https://api.github.com/repos/{repo_full_name}/readme")
        if not resp or resp.status_code != 200:
            return ""
        try:
            data = resp.json()
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"解析 README 失败 [{repo_full_name}]: {e}")
            return ""

    def _get_core_files(self, repo_full_name: str) -> List[Dict[str, str]]:
        """尝试获取核心代码文件（最多取 3 个）"""
        found = []
        for filename in CORE_FILE_PATTERNS:
            if filename.lower() == "readme.md":
                continue
            resp = self._request_with_retry(
                "GET", f"https://api.github.com/repos/{repo_full_name}/contents/{filename}"
            )
            if not resp or resp.status_code != 200:
                continue
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("encoding") == "base64":
                    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                    found.append({"path": filename, "content": content})
                    if len(found) >= 3:
                        break
            except Exception:
                continue
        return found

    def _search_repos(self, topic: str, limit: int) -> List[Dict]:
        resp = self._request_with_retry(
            "GET",
            "https://api.github.com/search/repositories",
            params={"q": f"topic:{topic}", "sort": "stars", "per_page": limit},
        )
        if not resp or resp.status_code != 200:
            logger.warning(f"搜索主题失败 [{topic}]: HTTP {resp.status_code if resp else 'N/A'}")
            return []
        return resp.json().get("items", [])
