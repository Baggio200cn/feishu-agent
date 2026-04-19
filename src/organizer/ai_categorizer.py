"""
AI 文档分类器 — 使用豆包 AI 对文档标题+摘要进行自动分类
"""
import json
import logging
import re
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = ["技术文档", "工作记录", "学习笔记", "项目文档", "AI 工具", "参考资料", "其他"]


class AICategorizer:
    """
    调用豆包 API，根据文档标题（和可选摘要）自动判断分类。
    支持关键词规则优先覆盖 AI 结果。
    """

    def __init__(self, ai_config: Dict, categories_config: Dict):
        self.api_key = ai_config.get("api_key", "")
        self.model = ai_config.get("model", "doubao-seed-2-0-mini-260215")
        self.base_url = ai_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        self._categories = categories_config.get("categories", [])
        self._default = categories_config.get("default_category", "其他")
        self._category_names = [c["name"] for c in self._categories] or DEFAULT_CATEGORIES

    def categorize(self, title: str, preview: str = "") -> str:
        """返回文档所属分类名称"""
        # 1. 关键词规则优先
        rule_result = self._match_keywords(title)
        if rule_result:
            return rule_result

        # 2. AI 分类
        if self.api_key and not self.api_key.startswith("your_"):
            ai_result = self._ask_ai(title, preview)
            if ai_result:
                return ai_result

        return self._default

    def categorize_batch(self, docs: List[Dict]) -> List[Dict]:
        """
        批量分类。docs 每项需包含 title 字段，可选 preview。
        原地添加 category 字段后返回。
        """
        for doc in docs:
            title = doc.get("title", "")
            preview = doc.get("preview_content", "")
            doc["category"] = self.categorize(title, preview)
        return docs

    def _match_keywords(self, title: str) -> Optional[str]:
        """按关键词规则匹配，返回匹配的分类名或 None"""
        title_lower = title.lower()
        for cat in self._categories:
            for kw in cat.get("keywords", []):
                if kw.lower() in title_lower:
                    return cat["name"]
        return None

    def _ask_ai(self, title: str, preview: str) -> Optional[str]:
        """调用豆包 Chat API 判断分类。为提速：temperature=0、max_tokens=20、timeout=12"""
        categories_str = "、".join(self._category_names)
        content = f"文档标题：{title}"
        if preview:
            content += f"\n内容摘要：{preview[:150]}"
        content += (
            f"\n\n请从以下分类中选择最合适的一个，只返回分类名称，不要推理过程，"
            f"不要任何额外文字：\n{categories_str}"
        )

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": 20,
                    "temperature": 0.0,
                },
                timeout=12,
            )
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()
            # 确保返回值在已知分类列表中
            for name in self._category_names:
                if name in result:
                    return name
        except Exception as e:
            logger.warning(f"AI 分类调用失败 (title={title[:30]}): {e}")

        return None
