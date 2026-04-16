import json
import logging
import os
import re
import sqlite3
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _clean_json(text: str) -> str:
    """Strip markdown code fences from Claude JSON responses."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

DEFAULT_CATEGORIES = ["技术文档", "工作记录", "学习笔记", "项目文档", "AI 工具", "参考资料", "其他"]
DB_PATH = "data/agent.db"


def _init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doc_cache (
            title TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


class AICategorizer:
    def __init__(self, ai_config: Dict, categories_config: Dict):
        self.api_key = ai_config.get("api_key", "")
        self.model = ai_config.get("classify_model", "claude-haiku-4-5-20251001")
        self._categories = categories_config.get("categories", [])
        self._default = categories_config.get("default_category", "其他")
        self._category_names = [c["name"] for c in self._categories] or DEFAULT_CATEGORIES
        self._db = _init_db()

    def categorize(self, title: str, preview: str = "") -> str:
        cached = self._db.execute(
            "SELECT category FROM doc_cache WHERE title = ?", (title,)
        ).fetchone()
        if cached:
            logger.info(f"缓存命中: {title[:30]} -> {cached[0]}")
            return cached[0]

        rule_result = self._match_keywords(title)
        if rule_result:
            self._save_cache(title, rule_result)
            return rule_result

        if self.api_key:
            ai_result = self._ask_claude(title, preview)
            if ai_result:
                self._save_cache(title, ai_result)
                return ai_result

        self._save_cache(title, self._default)
        return self._default

    def categorize_batch(self, docs: List[Dict]) -> List[Dict]:
        uncached = []
        for doc in docs:
            title = doc.get("title", "")
            cached = self._db.execute(
                "SELECT category FROM doc_cache WHERE title = ?", (title,)
            ).fetchone()
            if cached:
                doc["category"] = cached[0]
                logger.info(f"缓存命中: {title[:30]} -> {cached[0]}")
            else:
                uncached.append(doc)

        for i in range(0, len(uncached), 5):
            batch = uncached[i:i+5]
            self._categorize_batch_claude(batch)

        missing = [d for d in docs if not d.get("category")]
        if missing:
            logger.warning(f"{len(missing)} 篇文档分类缺失，归入默认分类「{self._default}」")
            for d in missing:
                d["category"] = self._default
                self._save_cache(d.get("title", ""), self._default)

        return docs

    def _categorize_batch_claude(self, docs: List[Dict]):
        if not self.api_key or not docs:
            for doc in docs:
                doc["category"] = self._default
            return

        categories_str = "、".join(self._category_names)
        items = "\n".join([f"{i+1}. 《{d.get('title', '')}》" for i, d in enumerate(docs)])
        prompt = f"请对以下{len(docs)}篇文档进行分类，从分类列表中为每篇选择最合适的一个。\n\n分类列表：{categories_str}\n\n文档列表：\n{items}\n\n请严格按以下JSON格式返回，不要其他文字：\n{{\"results\": [{{\"index\": 1, \"category\": \"分类名\"}}, ...]}}"

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            text = _clean_json(message.content[0].text)
            data = json.loads(text)
            for item in data.get("results", []):
                idx = item["index"] - 1
                if 0 <= idx < len(docs):
                    cat = item["category"]
                    if cat not in self._category_names:
                        cat = self._default
                    docs[idx]["category"] = cat
                    self._save_cache(docs[idx].get("title", ""), cat)
        except Exception as e:
            logger.warning(f"批量 Claude 分类失败: {e}")
            for doc in docs:
                if "category" not in doc:
                    doc["category"] = self._default

    def _ask_claude(self, title: str, preview: str) -> Optional[str]:
        categories_str = "、".join(self._category_names)
        content = f"文档标题：{title}"
        if preview:
            content += f"\n内容摘要：{preview[:200]}"
        content += f"\n\n请从以下分类中选择最合适的一个，只返回分类名称：\n{categories_str}"

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=20,
                messages=[{"role": "user", "content": content}]
            )
            result = message.content[0].text.strip()
            for name in self._category_names:
                if name in result:
                    return name
        except Exception as e:
            logger.warning(f"Claude 分类失败 (title={title[:30]}): {e}")
        return None

    def _match_keywords(self, title: str) -> Optional[str]:
        title_lower = title.lower()
        for cat in self._categories:
            for kw in cat.get("keywords", []):
                if kw.lower() in title_lower:
                    return cat["name"]
        return None

    def _save_cache(self, title: str, category: str):
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO doc_cache (title, category) VALUES (?, ?)",
                (title, category)
            )
            self._db.commit()
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
