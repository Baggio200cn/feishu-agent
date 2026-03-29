"""
GitHub 仓库导入器 — 获取 README + 核心代码文件，供后续写入飞书
"""
import base64
import logging
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
        if token and not token.startswith("ghp_your_"):
            self.headers["Authorization"] = f"token {token}"
        self.session = requests.Session()
        self.session.headers.update(self.headers)

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
        返回格式：
        {
            "name": str, "full_name": str, "description": str,
            "url": str, "stars": int, "language": str,
            "topics": [str], "readme": str,
            "core_files": [{"path": str, "content": str}]
        }
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
        resp = self.session.get(
            f"https://api.github.com/repos/{repo_full_name}", timeout=15
        )
        if resp.status_code == 404:
            logger.warning(f"仓库不存在: {repo_full_name}")
            return None
        resp.raise_for_status()
        return resp.json()

    def _get_readme(self, repo_full_name: str) -> str:
        """获取 README 内容（Markdown 格式）"""
        try:
            resp = self.session.get(
                f"https://api.github.com/repos/{repo_full_name}/readme", timeout=15
            )
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return content
        except Exception as e:
            logger.warning(f"获取 README 失败 [{repo_full_name}]: {e}")
            return ""

    def _get_core_files(self, repo_full_name: str) -> List[Dict[str, str]]:
        """尝试获取核心代码文件"""
        found = []
        for filename in CORE_FILE_PATTERNS:
            if filename.lower() == "readme.md":
                continue  # README 已单独处理
            try:
                resp = self.session.get(
                    f"https://api.github.com/repos/{repo_full_name}/contents/{filename}",
                    timeout=10,
                )
                if resp.status_code == 200:
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
        try:
            resp = self.session.get(
                "https://api.github.com/search/repositories",
                params={"q": f"topic:{topic}", "sort": "stars", "per_page": limit},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as e:
            logger.warning(f"搜索主题失败 [{topic}]: {e}")
            return []
