"""
飞书文档写入器 — 将 Markdown / 代码内容转换为飞书 Wiki 页面
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 飞书文档 Block 类型常量
BLOCK_PAGE = 1
BLOCK_TEXT = 2
BLOCK_H1 = 3
BLOCK_H2 = 4
BLOCK_H3 = 5
BLOCK_H4 = 6
BLOCK_ORDERED_LIST = 12
BLOCK_BULLET = 13
BLOCK_CODE = 14
BLOCK_DIVIDER = 22


class FeishuDocWriter:
    """将 GitHub 仓库数据写入飞书 Wiki 页面"""

    def __init__(self, personal_client, wiki_space_id: str, target_folder_token: str = ""):
        self._client = personal_client
        self.space_id = wiki_space_id
        self.target_folder_token = target_folder_token

    def write_github_repo(self, repo_data: Dict[str, Any]) -> Optional[str]:
        """
        为单个 GitHub 仓库创建飞书 Wiki 页面。
        返回创建的 Wiki 页面 URL，失败返回 None。
        """
        title = f"[GitHub] {repo_data['full_name']}"
        blocks = self._build_repo_blocks(repo_data)

        node_token = self._create_wiki_page(title, blocks)
        if node_token:
            url = f"https://open.feishu.cn/wiki/{node_token}"
            logger.info(f"Wiki 页面已创建: {title} → {url}")
            return url
        return None

    def write_github_repos_batch(self, repos: List[Dict]) -> List[str]:
        """批量写入，返回成功创建的页面 URL 列表"""
        urls = []
        for repo in repos:
            url = self.write_github_repo(repo)
            if url:
                urls.append(url)
        return urls

    def _build_repo_blocks(self, repo: Dict) -> List[Dict]:
        """构建仓库文档的 Block 列表"""
        blocks = []

        # 仓库基本信息
        blocks.append(self._text_block(f"⭐ Stars: {repo.get('stars', 0)}  |  语言: {repo.get('language', 'N/A')}  |  链接: {repo.get('url', '')}"))
        if repo.get("description"):
            blocks.append(self._text_block(repo["description"]))
        if repo.get("topics"):
            blocks.append(self._text_block("标签: " + "、".join(repo["topics"])))
        blocks.append(self._divider())

        # README 内容
        if repo.get("readme"):
            blocks.append(self._h2_block("README"))
            readme_blocks = self._markdown_to_blocks(repo["readme"])
            blocks.extend(readme_blocks[:80])  # 限制长度避免超出 API 限制

        # 核心代码文件
        for cf in repo.get("core_files", []):
            blocks.append(self._divider())
            blocks.append(self._h2_block(f"📄 {cf['path']}"))
            lang = self._detect_language(cf["path"])
            blocks.append(self._code_block(cf["content"][:3000], lang))

        return blocks

    def _markdown_to_blocks(self, markdown: str) -> List[Dict]:
        """将 Markdown 文本逐行转换为飞书 Block 列表"""
        blocks = []
        in_code_block = False
        code_lines = []
        code_lang = ""

        for line in markdown.splitlines():
            # 代码块处理
            if line.startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_lines = []
                else:
                    in_code_block = False
                    blocks.append(self._code_block("\n".join(code_lines), code_lang))
                    code_lines = []
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            stripped = line.rstrip()
            if not stripped:
                continue

            if stripped.startswith("#### "):
                blocks.append(self._h4_block(stripped[5:]))
            elif stripped.startswith("### "):
                blocks.append(self._h3_block(stripped[4:]))
            elif stripped.startswith("## "):
                blocks.append(self._h2_block(stripped[3:]))
            elif stripped.startswith("# "):
                blocks.append(self._h1_block(stripped[2:]))
            elif stripped == "---" or stripped == "***":
                blocks.append(self._divider())
            elif stripped.startswith("- ") or stripped.startswith("* "):
                blocks.append(self._bullet_block(self._strip_inline_md(stripped[2:])))
            elif re.match(r"^\d+\. ", stripped):
                text = re.sub(r"^\d+\. ", "", stripped)
                blocks.append(self._ordered_block(self._strip_inline_md(text)))
            else:
                text = self._strip_inline_md(stripped)
                if text:
                    blocks.append(self._text_block(text))

        return blocks

    @staticmethod
    def _strip_inline_md(text: str) -> str:
        """移除内联 Markdown 标记，转为纯文本"""
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)   # 图片
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # 链接
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # 粗体
        text = re.sub(r"\*([^*]+)\*", r"\1", text)       # 斜体
        text = re.sub(r"`([^`]+)`", r"\1", text)          # 行内代码
        return text.strip()

    @staticmethod
    def _detect_language(filename: str) -> str:
        ext_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".java": "Java",
            ".sh": "Bash", ".md": "Markdown", ".json": "JSON",
            ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
        }
        for ext, lang in ext_map.items():
            if filename.endswith(ext):
                return lang
        return "PlainText"

    # --- Block 构造器 ---

    def _text_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_TEXT, "text": {"elements": [{"text_run": {"content": text}}]}}

    def _h1_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H1, "heading1": {"elements": [{"text_run": {"content": text}}]}}

    def _h2_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H2, "heading2": {"elements": [{"text_run": {"content": text}}]}}

    def _h3_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H3, "heading3": {"elements": [{"text_run": {"content": text}}]}}

    def _h4_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_H4, "heading4": {"elements": [{"text_run": {"content": text}}]}}

    def _bullet_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_BULLET, "bullet": {"elements": [{"text_run": {"content": text}}]}}

    def _ordered_block(self, text: str) -> Dict:
        return {"block_type": BLOCK_ORDERED_LIST, "ordered": {"elements": [{"text_run": {"content": text}}]}}

    def _code_block(self, code: str, language: str = "PlainText") -> Dict:
        return {"block_type": BLOCK_CODE, "code": {"language": language, "elements": [{"text_run": {"content": code}}]}}

    def _divider(self) -> Dict:
        return {"block_type": BLOCK_DIVIDER}

    def _create_wiki_page(self, title: str, blocks: List[Dict]) -> Optional[str]:
        """创建 Wiki 页面节点并写入内容，返回 node_token"""
        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node

            node_builder = (
                Node.builder()
                .obj_type("docx")
                .node_type("origin")
                .title(title)
            )
            if self.target_folder_token:
                node_builder.parent_node_token(self.target_folder_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .request_body(node_builder.build())
                .build()
            )
            resp = self._client.wiki.v2.space_node.create(req)
            if not resp.success():
                logger.warning(f"创建 Wiki 页面失败: {resp.code} {resp.msg}")
                return None

            node_token = resp.data.node.node_token
            obj_token = resp.data.node.obj_token

            # 写入内容块
            if blocks:
                self._populate_blocks(obj_token, blocks)

            return node_token

        except Exception as e:
            logger.warning(f"创建 Wiki 页面异常: {e}")
            return None

    def _populate_blocks(self, document_id: str, blocks: List[Dict]) -> None:
        """批量写入文档内容块（分批，每批最多 50 个）"""
        try:
            from lark_oapi.api.docx.v1 import (
                CreateDocumentBlockChildrenRequest,
                CreateDocumentBlockChildrenRequestBody,
            )
            batch_size = 50
            for i in range(0, len(blocks), batch_size):
                batch = blocks[i:i + batch_size]
                body = (
                    CreateDocumentBlockChildrenRequestBody.builder()
                    .children(batch)
                    .index(-1)
                    .build()
                )
                req = (
                    CreateDocumentBlockChildrenRequest.builder()
                    .document_id(document_id)
                    .block_id(document_id)
                    .request_body(body)
                    .build()
                )
                resp = self._client.docx.v1.document_block_children.create(req)
                if not resp.success():
                    logger.warning(f"写入内容块失败 (batch {i//batch_size}): {resp.code} {resp.msg}")
        except Exception as e:
            logger.warning(f"写入文档内容异常: {e}")
