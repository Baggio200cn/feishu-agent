"""
Agent 大脑 — 调豆包 API 做意图识别和结果总结。

输入：用户自然语言 + 可选上下文
输出：intent JSON（告诉 Python 要调什么工具、用什么参数）
"""
import json
import logging
import re
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# 受支持的意图（工具函数的映射在 wiki_agent_runner 里）
SUPPORTED_INTENTS = [
    "search",              # 搜 Wiki 按关键词
    "find_empty",          # 找空节点（不删）
    "delete_empty",        # 找 + 删空节点
    "delete_by_prefix",    # 按标题前缀批量删
    "list_folder",         # 列某文件夹下的节点（占位，V2 再做）
    "organize_preview",    # 走 organize --dry-run
    "unknown",             # 无法识别，降级为 search
]


SYSTEM_PROMPT = """你是飞书 Wiki 知识库的管理 Agent。用户用中文自然语言告诉你要做什么，你的任务是：
1. 识别用户真实意图
2. 返回结构化 JSON，告诉后端要调哪个工具、带哪些参数

你【不能】直接动 Wiki，真正的执行由 Python 代码完成。你只是"大脑"，负责理解 + 编排。

可用意图类型：
- search: 用户想检索信息
- find_empty: 用户想知道哪些是空文档/空文件夹（只列出不删）
- delete_empty: 用户想删除空文档/空文件夹
- delete_by_prefix: 用户想删除标题以某某开头的节点（如"删除 2026-04-17 的日报"）
- organize_preview: 用户想整理/分类 Wiki 文档，给出分类预览
- unknown: 听不懂，建议用户用更具体的话或点界面按钮

严格要求：
- 只返回 JSON，不要任何 Markdown 围栏或解释文字
- 所有删除类意图必须标记 dangerous=true
- 如果用户说"帮我删掉所有空的"→ delete_empty
- 如果用户说"删除 GitHub Trending 日报"→ delete_by_prefix, params.prefix="GitHub Trending 日报"
- 如果用户说"查询一下 Claude 有啥资料"→ search, params.query="Claude"
- 看不准就 unknown，宁可降级也不误删"""


PROMPT_TEMPLATE = """用户说："{query}"

参考上下文（当前 Wiki 顶层节点标题，只作判断参考，不必每个都 revisit）：
{context}

返回格式（严格 JSON，一行或多行都可以，不要围栏）：
{{
  "intent": "<上面 6 个之一>",
  "reasoning": "<15-30 字中文说清你为什么选这个 intent>",
  "params": {{
    "query": "...",        // 仅 search 时必填
    "prefix": "..."        // 仅 delete_by_prefix 时必填
  }},
  "dangerous": true | false,
  "user_facing_plan": "<一句中文告诉用户你理解到的动作，如：我将扫描 Wiki 找出空节点，共预估 N 个>"
}}"""


class AgentBrain:
    def __init__(self, ai_config: Dict):
        self.base_url = (ai_config.get("base_url") or "").rstrip("/")
        self.api_key = ai_config.get("api_key") or ""
        self.model = ai_config.get("model") or ""
        self.session = requests.Session()

    def configured(self) -> bool:
        if not (self.base_url and self.model and self.api_key):
            return False
        if self.api_key.startswith("your_"):
            return False
        return True

    def parse_intent(self, query: str, context_titles: list = None) -> Dict[str, Any]:
        """
        让豆包把用户自然语言转成结构化 intent。

        Returns: dict with keys {intent, reasoning, params, dangerous, user_facing_plan}
                 失败返回 intent="unknown"
        """
        if not self.configured():
            return {
                "intent": "unknown",
                "reasoning": "豆包未配置",
                "params": {},
                "dangerous": False,
                "user_facing_plan": "AI 未配置，无法识别意图。请直接点主界面的具体按钮。",
            }

        ctx = "\n".join(f"  - {t}" for t in (context_titles or [])[:15]) or "  （未提供）"
        prompt = PROMPT_TEMPLATE.format(query=query[:300], context=ctx)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            r = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
        except requests.RequestException as e:
            logger.warning(f"[Brain] 豆包调用异常: {e}")
            return self._fallback(query, f"AI 调用异常: {e}")

        if r.status_code != 200:
            logger.warning(f"[Brain] 豆包 HTTP {r.status_code}: {r.text[:200]}")
            return self._fallback(query, f"AI HTTP {r.status_code}")

        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            return self._fallback(query, f"解析响应失败: {e}")

        parsed = self._extract_json(content)
        if not parsed or "intent" not in parsed:
            return self._fallback(query, "AI 返回不是合法 JSON")

        intent = parsed.get("intent") or "unknown"
        if intent not in SUPPORTED_INTENTS:
            logger.warning(f"[Brain] 未支持的 intent '{intent}'，降级")
            intent = "unknown"

        return {
            "intent": intent,
            "reasoning": parsed.get("reasoning", ""),
            "params": parsed.get("params") or {},
            "dangerous": bool(parsed.get("dangerous", False)),
            "user_facing_plan": parsed.get("user_facing_plan", ""),
        }

    def summarize_result(self, intent: str, action_result: Dict[str, Any]) -> str:
        """让豆包把执行结果写成友好中文总结（可选，失败则返回原始 message）"""
        if not self.configured():
            return action_result.get("message", "") or "执行完成"

        prompt = (
            f"刚刚执行了意图 '{intent}'，结果如下：\n\n"
            f"{json.dumps(action_result, ensure_ascii=False, indent=2)[:2000]}\n\n"
            f"请用 50 字以内中文友好地总结给用户，突出成功/失败/数量；不要复述 JSON 字段名。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你用简短中文总结操作结果。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 200,
        }
        try:
            r = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug(f"总结失败: {e}")
        return action_result.get("message", "") or "执行完成"

    @staticmethod
    def _fallback(query: str, reason: str) -> Dict[str, Any]:
        return {
            "intent": "search",  # 兜底走搜索
            "reasoning": f"AI 识别失败（{reason}），按搜索处理",
            "params": {"query": query},
            "dangerous": False,
            "user_facing_plan": f"⚠️ 无法精确识别意图，降级为搜索「{query}」",
        }

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            if s >= 0 and e > s:
                try:
                    return json.loads(text[s : e + 1])
                except json.JSONDecodeError:
                    pass
            return None
