"""
agent_cli.py — 对话式 Agent 命令行入口
用法: python agent_cli.py "用户消息"
供 launcher.pyw 的 ChatWindow 调用，也可直接使用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config_loader import config_loader
from src.chat.chat_agent import FeishuChatAgent


def main():
    if len(sys.argv) < 2:
        print("用法: python agent_cli.py <消息>")
        sys.exit(1)

    message = " ".join(sys.argv[1:])
    ai_cfg = config_loader.get_ai_config()

    agent = FeishuChatAgent(
        api_key=ai_cfg.get("api_key", ""),
        model=ai_cfg.get("model", "doubao-seed-2-0-code-preview-260215"),
        base_url=ai_cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"),
    )

    reply = agent.chat(
        message,
        on_tool_start=lambda name: print(f"[调用工具] {name}...", flush=True),
        on_tool_done=lambda name, res: print(f"[工具完成] {name}:\n{res[:500]}", flush=True),
    )
    print(reply, flush=True)


if __name__ == "__main__":
    main()
