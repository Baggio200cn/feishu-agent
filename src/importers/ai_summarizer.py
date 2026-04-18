"""
AI 摘要器 — 调用豆包 API 对 GitHub 仓库 README 生成中文摘要。

输出严格的 JSON：{ one_liner, detail }
- one_liner: 15-30 字一句话定位
- detail:    500+ 字中文详细介绍，四段式（项目背景/核心功能/技术亮点/适用场景）
             重点突出技术先进性 + 应用场景
"""
import json
import logging
import re
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "你是一位资深的中文技术分析师，熟悉 AI、后端、前端、DevOps 等技术栈，"
    "擅长把 GitHub 开源项目讲给中文开发者听，注重技术先进性和业务应用场景，"
    "语言简洁有力，避免空话套话。"
)

DEFAULT_USER_PROMPT_TEMPLATE = """请分析以下 GitHub 开源项目，生成中文摘要。严格按 JSON 返回，不要加任何额外文字或 Markdown 代码围栏。

项目名称: {full_name}
项目描述: {description}
主要语言: {language}
Stars: {stars_total} (今日新增 {stars_today})

README 内容（可能被截断）:
---
{readme}
---

返回格式（严格 JSON，两个字段都必填）:
{{
  "one_liner": "15-30 字的一句话定位，突出项目独特价值，避免套话",
  "detail": "500 字以上的详细中文介绍，分成 4 个自然段，顺序为：\\n\\n第 1 段 项目背景：解决了什么真实的痛点或场景问题。\\n\\n第 2 段 核心功能：项目对外提供的能力清单、关键用法。\\n\\n第 3 段 技术亮点：用了什么技术栈、架构，有什么先进性或创新点（技术术语首次出现加括号注释）。\\n\\n第 4 段 适用场景：哪些团队 / 业务场景会用到，相比同类项目的优势。"
}}
"""


class AISummarizer:
    """豆包 API 封装，专注于 GitHub 仓库摘要任务"""

    def __init__(self, ai_config: Dict):
        self.base_url = ai_config.get("base_url", "").rstrip("/")
        self.api_key = ai_config.get("api_key", "")
        self.model = ai_config.get("model", "")
        self.system_prompt = ai_config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.user_prompt_template = ai_config.get("summary_prompt") or DEFAULT_USER_PROMPT_TEMPLATE
        self.session = requests.Session()

    def configured(self) -> bool:
        """配置是否齐全"""
        if not self.base_url or not self.model:
            return False
        if not self.api_key or self.api_key.startswith("your_") or "your_doubao" in self.api_key:
            return False
        return True

    def summarize_repo(
        self,
        full_name: str,
        description: str,
        language: str,
        stars_total: int,
        stars_today: int,
        readme: str,
        readme_max_chars: int = 8000,
        timeout: int = 180,
        retries: int = 1,
    ) -> Optional[Dict[str, str]]:
        """
        生成一个仓库的中文摘要。

        Returns:
            {"one_liner": "...", "detail": "..."} 或 None（失败）
        """
        if not self.configured():
            logger.warning("AI 摘要器未配置，跳过")
            return None

        readme_truncated = (readme or "")[:readme_max_chars]
        user_prompt = self.user_prompt_template.format(
            full_name=full_name,
            description=description or "（无描述）",
            language=language or "未知",
            stars_total=stars_total,
            stars_today=stars_today,
            readme=readme_truncated or "（README 获取失败）",
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.5,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
            except requests.Timeout as e:
                last_error = f"timeout {timeout}s"
                logger.warning(
                    f"[AI] 超时（第 {attempt + 1}/{retries + 1} 次）[{full_name}]: {e}"
                )
                continue
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"[AI] 请求异常 [{full_name}]: {e}")
                continue

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    f"[AI] 摘要失败 [{full_name}]: HTTP {resp.status_code} {resp.text[:300]}"
                )
                continue

            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                last_error = str(e)
                logger.warning(f"[AI] 响应结构异常 [{full_name}]: {e}")
                continue

            parsed = self._parse_json(content)
            if not parsed:
                last_error = "JSON parse failed"
                logger.warning(f"[AI] 返回内容不是合法 JSON [{full_name}]: {content[:200]}")
                continue

            one_liner = (parsed.get("one_liner") or "").strip()
            detail = (parsed.get("detail") or "").strip()
            if not one_liner or not detail:
                last_error = "missing fields"
                logger.warning(f"[AI] 字段缺失 [{full_name}]: {parsed}")
                continue

            logger.info(f"[AI] 摘要完成 [{full_name}]: detail 长度 {len(detail)} 字")
            return {"one_liner": one_liner, "detail": detail}

        logger.warning(f"[AI] 最终失败 [{full_name}]: {last_error}")
        return None

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict]:
        """从可能带围栏的文本中解析 JSON"""
        text = text.strip()
        # 去掉 ```json / ``` 围栏
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 兜底：找到第一个 { 和最后一个 } 之间的内容
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return None
