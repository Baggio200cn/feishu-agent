"""
飞书文档写入器 — 将 Markdown / 代码内容转换为飞书 Wiki 页面
使用 lark_oapi SDK 的 Block 对象（非原始 dict）构建文档内容。
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _build_text_elements(text: str):
    """构建 TextElement 列表（SDK 对象）"""
    from lark_oapi.api.docx.v1 import TextElement, TextRun

    return [TextElement.builder().text_run(TextRun.builder().content(text).build()).build()]


def _build_block(block_type: int, **kwargs):
    """通用 Block 构建器"""
    from lark_oapi.api.docx.v1 import Block

    builder = Block.builder().block_type(block_type)
    for key, value in kwargs.items():
        getattr(builder, key)(value)
    return builder.build()


def _text_block(text: str):
    from lark_oapi.api.docx.v1 import Block, Text
    return (
        Block.builder()
        .block_type(2)
        .text(Text.builder().elements(_build_text_elements(text)).build())
        .build()
    )


def _heading_block(level: int, text: str):
    """level: 1-4 对应 block_type 3-6 及 heading1-heading4"""
    from lark_oapi.api.docx.v1 import Block, Text

    block_type = level + 2  # h1=3, h2=4, h3=5, h4=6
    heading_obj = Text.builder().elements(_build_text_elements(text)).build()
    builder = Block.builder().block_type(block_type)
    # 根据 level 设置对应的 heading 字段
    heading_setters = {1: "heading1", 2: "heading2", 3: "heading3", 4: "heading4"}
    getattr(builder, heading_setters[level])(heading_obj)
    return builder.build()


def _bullet_block(text: str):
    from lark_oapi.api.docx.v1 import Block, Text
    return (
        Block.builder()
        .block_type(13)
        .bullet(Text.builder().elements(_build_text_elements(text)).build())
        .build()
    )


def _ordered_block(text: str):
    from lark_oapi.api.docx.v1 import Block, Text
    return (
        Block.builder()
        .block_type(12)
        .ordered(Text.builder().elements(_build_text_elements(text)).build())
        .build()
    )


def _code_block(code: str, language: int = 0):
    """
    构建代码块。language 为语言枚举值（int）。
    """
    from lark_oapi.api.docx.v1 import Block, Text

    return (
        Block.builder()
        .block_type(14)
        .code(
            Text.builder()
            .elements(_build_text_elements(code))
            .build()
        )
        .build()
    )


def _divider_block():
    from lark_oapi.api.docx.v1 import Block
    return Block.builder().block_type(22).build()


# 语言名称 → lark_oapi 代码块语言枚举值（常用子集）
LANG_MAP = {
    "python": 49, "javascript": 33, "typescript": 67, "go": 29,
    "rust": 56, "java": 32, "bash": 9, "shell": 9, "sh": 9,
    "json": 35, "yaml": 73, "yml": 73, "toml": 65, "markdown": 41,
    "html": 30, "css": 16, "sql": 60, "plaintext": 0, "": 0,
}


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

    def _build_repo_blocks(self, repo: Dict) -> List:
        """构建仓库文档的 Block 列表（SDK 对象）"""
        blocks = []

        # 仓库基本信息
        blocks.append(_text_block(
            f"Stars: {repo.get('stars', 0)}  |  "
            f"语言: {repo.get('language', 'N/A')}  |  "
            f"链接: {repo.get('url', '')}"
        ))
        if repo.get("description"):
            blocks.append(_text_block(repo["description"]))
        if repo.get("topics"):
            blocks.append(_text_block("标签: " + "、".join(repo["topics"])))
        blocks.append(_divider_block())

        # README 内容
        if repo.get("readme"):
            blocks.append(_heading_block(2, "README"))
            readme_blocks = self._markdown_to_blocks(repo["readme"])
            blocks.extend(readme_blocks[:80])

        # 核心代码文件
        for cf in repo.get("core_files", []):
            blocks.append(_divider_block())
            blocks.append(_heading_block(2, f"📄 {cf['path']}"))
            lang_name = self._detect_language(cf["path"]).lower()
            lang_id = LANG_MAP.get(lang_name, 0)
            blocks.append(_code_block(cf["content"][:3000], lang_id))

        return blocks

    def _markdown_to_blocks(self, markdown: str) -> List:
        """将 Markdown 文本逐行转换为飞书 Block 列表（SDK 对象）"""
        blocks = []
        in_code_block = False
        code_lines = []
        code_lang = ""

        for line in markdown.splitlines():
            if line.startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_lines = []
                else:
                    in_code_block = False
                    lang_id = LANG_MAP.get(code_lang.lower(), 0)
                    blocks.append(_code_block("\n".join(code_lines), lang_id))
                    code_lines = []
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            stripped = line.rstrip()
            if not stripped:
                continue

            if stripped.startswith("#### "):
                blocks.append(_heading_block(4, stripped[5:]))
            elif stripped.startswith("### "):
                blocks.append(_heading_block(3, stripped[4:]))
            elif stripped.startswith("## "):
                blocks.append(_heading_block(2, stripped[3:]))
            elif stripped.startswith("# "):
                blocks.append(_heading_block(1, stripped[2:]))
            elif stripped == "---" or stripped == "***":
                blocks.append(_divider_block())
            elif stripped.startswith("- ") or stripped.startswith("* "):
                blocks.append(_bullet_block(self._strip_inline_md(stripped[2:])))
            elif re.match(r"^\d+\. ", stripped):
                text = re.sub(r"^\d+\. ", "", stripped)
                blocks.append(_ordered_block(self._strip_inline_md(text)))
            else:
                text = self._strip_inline_md(stripped)
                if text:
                    blocks.append(_text_block(text))

        return blocks

    @staticmethod
    def _strip_inline_md(text: str) -> str:
        """移除内联 Markdown 标记，转为纯文本"""
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)     # 图片
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # 链接
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)        # 粗体
        text = re.sub(r"\*([^*]+)\*", r"\1", text)             # 斜体
        text = re.sub(r"`([^`]+)`", r"\1", text)               # 行内代码
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

    def _create_wiki_page(self, title: str, blocks: List) -> Optional[str]:
        """创建 Wiki 页面节点并写入内容，返回 node_token"""
        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

            body_builder = (
                CreateSpaceNodeRequestBody.builder()
                .obj_type("doc")
                .node_type("origin")
                .title(title)
            )
            if self.target_folder_token:
                body_builder.parent_node_token(self.target_folder_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .request_body(body_builder.build())
                .build()
            )
            resp = self._client.wiki.v2.space_node.create(req)
            if not resp.success():
                logger.warning(f"创建 Wiki 页面失败: {resp.msg}")
                return None

            node_token = resp.data.node.node_token
            obj_token = resp.data.node.obj_token

            if blocks:
                self._populate_blocks(obj_token, blocks)

            return node_token

        except Exception as e:
            logger.warning(f"创建 Wiki 页面异常: {e}")
            return None

    def _populate_blocks(self, document_id: str, blocks: List) -> None:
        """批量写入文档内容块（分批，每批最多 50 个 SDK Block 对象）"""
        try:
            from lark_oapi.api.docx.v1 import (
                BatchCreateDocumentBlockChildrenRequest,
                BatchCreateDocumentBlockChildrenRequestBody,
            )
            batch_size = 50
            for i in range(0, len(blocks), batch_size):
                batch = blocks[i:i + batch_size]
                body = (
                    BatchCreateDocumentBlockChildrenRequestBody.builder()
                    .children(batch)
                    .build()
                )
                req = (
                    BatchCreateDocumentBlockChildrenRequest.builder()
                    .document_id(document_id)
                    .block_id(document_id)
                    .request_body(body)
                    .build()
                )
                resp = self._client.docx.v1.document_block_children.batch_create(req)
                if not resp.success():
                    logger.warning(f"写入内容块失败 (batch {i // batch_size}): {resp.msg}")
        except Exception as e:
            logger.warning(f"写入文档内容异常: {e}")
