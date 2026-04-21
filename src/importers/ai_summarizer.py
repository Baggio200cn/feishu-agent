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


DEFAULT_REDDIT_PROMPT_TEMPLATE = """请分析以下 Reddit 讨论帖，生成中文摘要。严格按 JSON 返回，不要加任何额外文字或 Markdown 代码围栏。

子版块: r/{subreddit}
帖子标题: {title}
作者: u/{author}   ·   得分: {score}   ·   评论数: {num_comments}
{flair_line}
{external_url_line}

帖子正文（self post 则为讨论内容，link post 则为空）:
---
{selftext}
---

置顶 / 作者补充评论（可能为空；[置顶] 或 [OP] 标记，常为上下文、FAQ、或作者补说明）:
---
{stickied_comments}
---

高赞评论（已按点赞数排序，可能含反对/质疑声音）:
---
{top_comments_text}
---

返回格式（严格 JSON，两个字段都必填）:
{{
  "one_liner": "15-30 字的一句话中文定位，概括帖子讨论的核心话题和独特价值",
  "detail": "400-600 字的详细中文介绍，分成 5 个自然段，顺序为：\\n\\n第 1 段 话题背景：讨论的起因、上下文，为什么值得关注。\\n\\n第 2 段 核心观点：原帖作者的主要论点、发现或分享内容。技术术语首次出现加括号注释。\\n\\n第 3 段 技术要点：涉及的具体技术、模型、工具、方法，归纳关键细节。\\n\\n第 4 段 置顶/作者补充：引用置顶评论或作者 OP 在评论区的补说明（如果有）；没有就写'无额外补充'。\\n\\n第 5 段 质疑与讨论：**重点**列出评论区里的反对意见、质疑点、不同立场的依据；没有明显质疑时写积极补充/经验分享，引用具体评论者观点。"
}}
"""


class AISummarizer:
    """豆包 API 封装，面向 GitHub 仓库 / Reddit 帖子两类摘要任务"""

    def __init__(self, ai_config: Dict):
        self.base_url = ai_config.get("base_url", "").rstrip("/")
        self.api_key = ai_config.get("api_key", "")
        self.model = ai_config.get("model", "")
        self.system_prompt = ai_config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.user_prompt_template = ai_config.get("summary_prompt") or DEFAULT_USER_PROMPT_TEMPLATE
        self.reddit_prompt_template = (
            ai_config.get("reddit_prompt") or DEFAULT_REDDIT_PROMPT_TEMPLATE
        )
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
        readme_max_chars: int = 4000,
        timeout: int = 180,
        retries: int = 2,
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

    def summarize_reddit_post(
        self,
        post: Dict,
        selftext_max_chars: int = 3000,
        comments_max_chars: int = 1500,
        timeout: int = 180,
        retries: int = 2,
    ) -> Optional[Dict[str, str]]:
        """
        Reddit 帖子 → {one_liner, detail} 中文摘要。
        """
        if not self.configured():
            logger.warning("AI 摘要器未配置，跳过")
            return None

        tag = f"r/{post.get('subreddit','?')}/{post.get('id','?')}"
        comments = post.get("top_comments") or []

        # 按类型分组：置顶 / OP / 其他高赞
        stickied_entries = [c for c in comments if c.get("is_stickied")]
        op_entries = [c for c in comments if c.get("is_op") and not c.get("is_stickied")]
        regular_entries = [c for c in comments if not c.get("is_stickied") and not c.get("is_op")]

        def _render(entries: List[Dict], budget: int, tag_fn=None) -> str:
            if not entries:
                return ""
            out = ""
            remaining = budget
            for c in entries:
                body = (c.get("body") or "").strip()
                if not body:
                    continue
                snippet = body[:max(remaining, 200)]
                prefix = tag_fn(c) if tag_fn else ""
                line = f"{prefix}[+{c.get('score', 0)}] u/{c.get('author','?')}: {snippet}"
                out += (line + "\n\n")
                remaining -= len(snippet)
                if remaining <= 0:
                    break
            return out.strip()

        stickied_budget = min(comments_max_chars // 2, 1000) if (stickied_entries or op_entries) else 0
        stickied_comments = _render(
            stickied_entries + op_entries,
            budget=stickied_budget or comments_max_chars,
            tag_fn=lambda c: "[置顶] " if c.get("is_stickied") else "[OP] ",
        ) or "（无置顶 / 作者补充评论）"

        top_comments_text = _render(
            regular_entries,
            budget=comments_max_chars - min(len(stickied_comments), comments_max_chars // 2),
        ) or "（无高赞评论）"

        flair_line = (
            f"帖子 Flair: {post['link_flair_text']}"
            if post.get("link_flair_text")
            else ""
        )
        external_url = post.get("url") or ""
        is_self = post.get("is_self")
        external_url_line = (
            "" if is_self or not external_url
            else f"外链: {external_url}"
        )

        try:
            user_prompt = self.reddit_prompt_template.format(
                subreddit=post.get("subreddit", ""),
                title=post.get("title", ""),
                author=post.get("author", ""),
                score=post.get("score", 0),
                num_comments=post.get("num_comments", 0),
                flair_line=flair_line,
                external_url_line=external_url_line,
                selftext=(post.get("selftext") or "（link post，无正文）")[:selftext_max_chars],
                stickied_comments=stickied_comments,
                top_comments_text=top_comments_text,
            )
        except KeyError:
            # 兼容旧模板（只有 {comments} 占位符）
            merged = (
                (f"[置顶/作者]\n{stickied_comments}\n\n" if stickied_entries or op_entries else "")
                + f"[高赞]\n{top_comments_text}"
            )
            user_prompt = self.reddit_prompt_template.format(
                subreddit=post.get("subreddit", ""),
                title=post.get("title", ""),
                author=post.get("author", ""),
                score=post.get("score", 0),
                num_comments=post.get("num_comments", 0),
                flair_line=flair_line,
                external_url_line=external_url_line,
                selftext=(post.get("selftext") or "（link post，无正文）")[:selftext_max_chars],
                comments=merged,
            )

        return self._chat_json(tag, user_prompt, timeout=timeout, retries=retries)

    def _chat_json(
        self,
        tag: str,
        user_prompt: str,
        timeout: int = 180,
        retries: int = 1,
    ) -> Optional[Dict[str, str]]:
        """通用的 chat completion → 解析 JSON → 返回 {one_liner, detail}"""
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
                    f"[AI] 超时（第 {attempt + 1}/{retries + 1} 次）[{tag}]: {e}"
                )
                continue
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"[AI] 请求异常 [{tag}]: {e}")
                continue

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    f"[AI] 摘要失败 [{tag}]: HTTP {resp.status_code} {resp.text[:300]}"
                )
                continue

            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                last_error = str(e)
                logger.warning(f"[AI] 响应结构异常 [{tag}]: {e}")
                continue

            parsed = self._parse_json(content)
            if not parsed:
                last_error = "JSON parse failed"
                logger.warning(f"[AI] 返回内容不是合法 JSON [{tag}]: {content[:200]}")
                continue

            one_liner = (parsed.get("one_liner") or "").strip()
            detail = (parsed.get("detail") or "").strip()
            if not one_liner or not detail:
                last_error = "missing fields"
                logger.warning(f"[AI] 字段缺失 [{tag}]: {parsed}")
                continue

            logger.info(f"[AI] 摘要完成 [{tag}]: detail 长度 {len(detail)} 字")
            return {"one_liner": one_liner, "detail": detail}

        logger.warning(f"[AI] 最终失败 [{tag}]: {last_error}")
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
