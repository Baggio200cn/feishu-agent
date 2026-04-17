"""
飞书智能对话 Agent
使用 Claude tool_use，理解自然语言指令并调用飞书各项功能
"""
import json
import logging
import os
import subprocess
import sys
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable

# ── 工具定义（Claude tool_use 格式）────────────────────────────────────────
TOOLS = [
    {
        "name": "wiki_organize_preview",
        "description": (
            "扫描飞书 Wiki 所有文档，用 AI 自动分类并预览整理结果，"
            "不实际移动任何文档。用于查看分类效果。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wiki_organize_execute",
        "description": (
            "扫描飞书 Wiki 所有文档，用 AI 自动分类，"
            "实际创建分类目录并将文档移入对应目录。"
            "执行前应先用 wiki_organize_preview 确认分类结果。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wiki_cleanup_list",
        "description": "列出 Wiki 当前节点结构，查看哪些文档位置不对或有空目录需要清理。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wiki_cleanup_execute",
        "description": "将 Wiki 中位置错误的文档移回根目录，并删除空的分类目录节点。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wiki_list_spaces",
        "description": "列出飞书账号下所有 Wiki 知识空间及其 space_id。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "calendar_get_events",
        "description": "获取飞书日历近期的日程安排和会议事项。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "message_get_recent",
        "description": "获取飞书 IM 最近收到的消息。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "email_get_recent",
        "description": "获取飞书邮箱最近收到的邮件列表。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wiki_search_docs",
        "description": (
            "按关键词搜索飞书 Wiki 文档，从本地索引中查找标题包含该关键词的文档，"
            "并按分类汇总展示结果。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词，如 'AI'、'项目'、'2025' 等",
                }
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "wiki_daily_report",
        "description": (
            "生成每日摘要报告，汇总 Wiki 文档分类统计、近 7 天日历事件和最新邮件。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "reddit_daily",
        "description": (
            "抓取 Reddit r/AI_Agents 今日最新和热门帖子，"
            "用 AI 翻译成中文后写入飞书 Wiki「agent专区」文件夹。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "每种排序（new/hot）各抓取多少篇帖子，默认 20",
                }
            },
            "required": [],
        },
    },
    {
        "name": "github_trending",
        "description": (
            "抓取 GitHub Trending 今日最热门仓库，"
            "用 AI 生成中文摘要后写入飞书 Wiki「github专区」文件夹。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "时间范围，默认 daily（今日）",
                },
                "top_n": {
                    "type": "integer",
                    "description": "最多写入几个仓库，默认 15",
                },
            },
            "required": [],
        },
    },
    {
        "name": "github_import_repo",
        "description": (
            "将指定的 GitHub 仓库导入到飞书 Wiki，"
            "自动抓取 README 和核心文件，生成飞书文档。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub 仓库名，格式为 owner/repo，如 'microsoft/vscode'",
                }
            },
            "required": ["repo"],
        },
    },
]

SYSTEM_PROMPT = """你是飞书智能助理，帮助用户通过自然语言管理飞书 Wiki、日历、消息和邮件。

可用能力：
- Wiki 文档整理：扫描文档 → AI 自动分类 → 整理到对应目录
- Wiki 结构清理：修复文档位置错误、删除空目录
- Reddit 日报：抓取 r/AI_Agents 今日帖子 → 中文翻译 → 写入「agent专区」
- GitHub 日报：抓取 GitHub Trending 热门仓库 → 中文摘要 → 写入「github专区」
- 查看日历事件、IM 消息、邮件

工具使用规则：
- 有风险的操作（如实际移动文档）先用预览工具确认，再询问用户是否执行
- 工具执行失败后报告失败原因，不要假装成功
- 实际移动文档前必须先调用 wiki_organize_preview

回答原则：
1. 操作前先说明你将要做什么
2. 工具执行完后，用简洁的中文总结结果，不要原样复述日志
3. 如果工具执行报错，报告错误，询问是否重试
4. 保持友好、简洁的对话风格"""


def _run(cmd: List[str], timeout: int = 120) -> str:
    """执行子进程命令，返回输出文本（最多3000字符）"""
    env = os.environ.copy()
    env["PYTHONHTTPSVERIFY"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=BASE_DIR, env=env, timeout=timeout,
            errors="replace",
        )
        out = result.stdout + result.stderr
        if len(out) > 3000:
            out = "...(省略前半部分)...\n" + out[-2500:]
        return out or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"执行超时（>{timeout}s）"
    except Exception as e:
        return f"执行失败: {e}"


def _dispatch(tool_name: str, tool_input: dict = None) -> str:
    """根据工具名和参数执行对应命令"""
    inp = tool_input or {}

    # 带参数的工具单独处理
    if tool_name == "wiki_search_docs":
        keyword = inp.get("keyword", "").strip()
        if not keyword:
            return "请提供搜索关键词"
        return _run([PYTHON, "main.py", "search", "--keyword", keyword])

    if tool_name == "github_import_repo":
        repo = inp.get("repo", "").strip()
        if not repo:
            return "请提供仓库名称，格式: owner/repo"
        return _run([PYTHON, "main.py", "import-github", "--repo", repo], timeout=180)

    if tool_name == "reddit_daily":
        limit = int(inp.get("limit", 20))
        return _run([PYTHON, "main.py", "daily-reddit", "--limit", str(limit)], timeout=300)

    if tool_name == "github_trending":
        since = inp.get("since", "daily")
        top_n = int(inp.get("top_n", 15))
        return _run(
            [PYTHON, "main.py", "daily-github", "--since", since, "--top-n", str(top_n)],
            timeout=300,
        )

    # 无参数工具
    dispatch = {
        "wiki_organize_preview": [PYTHON, "main.py", "organize", "--dry-run"],
        "wiki_organize_execute": [PYTHON, "main.py", "organize"],
        "wiki_cleanup_list":     [PYTHON, "cleanup_wiki.py", "--list"],
        "wiki_cleanup_execute":  [PYTHON, "cleanup_wiki.py", "--move", "--delete"],
        "wiki_list_spaces":      [PYTHON, "main.py", "list-spaces"],
        "wiki_daily_report":     [PYTHON, "main.py", "daily-report"],
        "calendar_get_events":   [PYTHON, "main.py", "manage", "calendar"],
        "message_get_recent":    [PYTHON, "main.py", "manage", "messages"],
        "email_get_recent":      [PYTHON, "main.py", "manage", "email"],
    }
    cmd = dispatch.get(tool_name)
    if not cmd:
        return f"未知工具: {tool_name}"
    return _run(cmd)


# ── 主 Agent 类 ─────────────────────────────────────────────────────────────
class FeishuChatAgent:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._history: List[Dict] = []

    def chat(
        self,
        user_message: str,
        on_tool_start: Optional[Callable[[str], None]] = None,
        on_tool_done: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """
        发送用户消息，返回 AI 回复文本。
        on_tool_start(tool_name): 工具调用开始时回调
        on_tool_done(tool_name, result): 工具执行完成时回调
        """
        # 检测历史损坏：末尾是 tool_result 说明上次调用中途失败，重置历史
        if (self._history
                and self._history[-1]["role"] == "user"
                and isinstance(self._history[-1]["content"], list)
                and self._history[-1]["content"]
                and isinstance(self._history[-1]["content"][0], dict)
                and self._history[-1]["content"][0].get("type") == "tool_result"):
            logger.warning("检测到历史记录损坏，已自动重置")
            self._history.clear()

        self._history.append({"role": "user", "content": user_message})
        # 记录此次调用前的历史长度，异常时用于回滚
        checkpoint = len(self._history)

        try:
            # ── 循环处理 tool_use ──────────────────────────────────────────
            while True:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=self._history,
                )

                if resp.stop_reason != "tool_use":
                    break  # 普通回复，退出循环

                # 处理所有 tool_use 块
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        if on_tool_start:
                            on_tool_start(block.name)
                        result = _dispatch(block.name, block.input)
                        if on_tool_done:
                            on_tool_done(block.name, result)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # 把 assistant 的 tool_use 内容 + 工具结果追加到历史
                self._history.append({"role": "assistant", "content": resp.content})
                self._history.append({"role": "user", "content": tool_results})

            # ── 提取纯文本回复 ───────────────────────────────────────────
            text = "".join(
                block.text for block in resp.content if hasattr(block, "text")
            )
            self._history.append({"role": "assistant", "content": resp.content})

            # 保持历史在合理长度（最近30轮），按完整对话段修剪
            if len(self._history) > 60:
                self._history = self._history[-60:]
                # 确保不从 tool_result 消息开头（否则下次调用会 400）
                while (self._history
                       and self._history[0]["role"] == "user"
                       and isinstance(self._history[0]["content"], list)
                       and self._history[0]["content"]
                       and isinstance(self._history[0]["content"][0], dict)
                       and self._history[0]["content"][0].get("type") == "tool_result"):
                    self._history.pop(0)

            return text

        except Exception:
            # API 调用失败时回滚历史，避免下次调用时历史损坏
            del self._history[checkpoint - 1:]
            raise

    def clear(self):
        self._history.clear()
