"""
飞书 Agent 图形化启动器 v2
- 控制台标签：一键执行所有功能
- 智能对话标签：自然语言与飞书 Agent 对话
"""
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

# ── 配色 ────────────────────────────────────────────────────────────────────
C_BG       = "#F0F4FF"
C_CARD     = "#FFFFFF"
C_BLUE     = "#3370FF"
C_TEXT     = "#1F2329"
C_GRAY     = "#8F959E"
C_BORDER   = "#DEE0E3"
C_CONSOLE  = "#1E1E2E"
C_FG       = "#CDD6F4"
C_USER_BG  = "#E8F0FE"
C_BOT_BG   = "#F0FFF4"
C_TOOL_BG  = "#FFF8E1"
C_USER_FG  = "#1A5FE0"
C_BOT_FG   = "#1A6B2A"
C_TOOL_FG  = "#8B6914"


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _load_api_key() -> str:
    cred = os.path.join(BASE_DIR, "config", "credentials.json")
    try:
        with open(cred, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("ai", {}).get("api_key", "")
    except Exception:
        return ""


def run_cmd(cmd_args, output_widget):
    """后台运行命令，实时输出到文本框"""
    def _run():
        output_widget.config(state="normal")
        output_widget.delete(1.0, tk.END)
        output_widget.insert(tk.END, f"▶ {' '.join(cmd_args)}\n{'─'*60}\n")
        output_widget.config(state="disabled")
        env = os.environ.copy()
        env["PYTHONHTTPSVERIFY"] = "0"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.Popen(
                cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=BASE_DIR, env=env, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                output_widget.config(state="normal")
                output_widget.insert(tk.END, line)
                output_widget.see(tk.END)
                output_widget.config(state="disabled")
            proc.wait()
            output_widget.config(state="normal")
            output_widget.insert(tk.END, f"\n{'─'*60}\n✅ 完成（退出码 {proc.returncode}）\n")
        except Exception as e:
            output_widget.config(state="normal")
            output_widget.insert(tk.END, f"\n❌ {e}\n")
        finally:
            output_widget.config(state="disabled")
            output_widget.see(tk.END)
    threading.Thread(target=_run, daemon=True).start()


# ── 主窗口 ───────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("飞书 Agent 控制台")
        self.geometry("900x620")
        self.configure(bg=C_BG)

        ico = os.path.join(BASE_DIR, "feishu_agent.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except Exception:
                pass

        self._agent = None   # 懒加载 FeishuChatAgent
        self._build()

    # ── UI 构建 ───────────────────────────────────────────────────────────────
    def _build(self):
        # 顶部标题栏
        hdr = tk.Frame(self, bg=C_BLUE, pady=11)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🤖  飞书 Agent 智能管理中心",
                 bg=C_BLUE, fg="white", font=("微软雅黑", 14, "bold")).pack(side="left", padx=20)
        tk.Label(hdr, text="by Claude AI",
                 bg=C_BLUE, fg="#A8C4FF", font=("微软雅黑", 9)).pack(side="right", padx=20)

        # Notebook（标签页）
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TNotebook", background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("微软雅黑", 10),
                         padding=[16, 6], background=C_BORDER, foreground=C_GRAY)
        style.map("TNotebook.Tab",
                  background=[("selected", C_BLUE)],
                  foreground=[("selected", "white")])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        # 标签1：控制台
        tab1 = tk.Frame(nb, bg=C_BG)
        nb.add(tab1, text="  🖥️ 控制台  ")
        self._build_console(tab1)

        # 标签2：智能对话
        tab2 = tk.Frame(nb, bg=C_BG)
        nb.add(tab2, text="  💬 智能对话  ")
        self._build_chat(tab2)

        # 底部状态栏
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x")
        foot = tk.Frame(self, bg=C_BG, pady=4)
        foot.pack(fill="x")
        tk.Label(foot, text=f"Python {sys.version.split()[0]}  |  {BASE_DIR}",
                 bg=C_BG, fg=C_GRAY, font=("微软雅黑", 8)).pack(side="left", padx=12)

    # ── 控制台标签 ────────────────────────────────────────────────────────────
    def _build_console(self, parent):
        body = tk.Frame(parent, bg=C_BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        left = tk.Frame(body, bg=C_BG, width=200)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        out = scrolledtext.ScrolledText(
            body, state="disabled", wrap="word",
            bg=C_CONSOLE, fg=C_FG, font=("Consolas", 9),
            relief="flat", bd=0,
        )
        out.pack(side="right", fill="both", expand=True)

        sections = [
            ("📚 Wiki 整理", [
                ("扫描 & 整理（预览）", [PYTHON, "main.py", "organize", "--dry-run"]),
                ("扫描 & 整理（执行）", [PYTHON, "main.py", "organize"]),
                ("列出 Wiki 空间",      [PYTHON, "main.py", "list-spaces"]),
            ]),
            ("🧹 Wiki 清理", [
                ("预览需清理的节点",    [PYTHON, "cleanup_wiki.py", "--list"]),
                ("移动文档到根目录",    [PYTHON, "cleanup_wiki.py", "--move"]),
                ("移动 + 删除空目录",  [PYTHON, "cleanup_wiki.py", "--move", "--delete"]),
            ]),
            ("📅 日历 & 消息", [
                ("查看日历事件",        [PYTHON, "main.py", "manage", "calendar"]),
                ("查看 IM 消息",        [PYTHON, "main.py", "manage", "messages"]),
                ("查看邮件",            [PYTHON, "main.py", "manage", "email"]),
            ]),
            ("🐙 GitHub 导入", [
                ("导入 GitHub 仓库",    [PYTHON, "main.py", "import-github"]),
            ]),
        ]

        for sec, btns in sections:
            tk.Label(left, text=sec, bg=C_BG, fg=C_GRAY,
                     font=("微软雅黑", 8, "bold"), anchor="w").pack(fill="x", padx=4, pady=(10, 2))
            for label, cmd in btns:
                tk.Button(
                    left, text=label, bg=C_CARD, fg=C_TEXT,
                    activebackground=C_BLUE, activeforeground="white",
                    relief="flat", font=("微软雅黑", 9),
                    anchor="w", padx=8, pady=5, cursor="hand2",
                    command=lambda c=cmd: run_cmd(c, out),
                ).pack(fill="x", pady=1, padx=2)

        tk.Button(parent, text="清空输出", bg=C_BG, fg=C_GRAY,
                  relief="flat", font=("微软雅黑", 8), cursor="hand2",
                  command=lambda: (
                      out.config(state="normal"),
                      out.delete(1.0, tk.END),
                      out.config(state="disabled"),
                  )).pack(side="right", padx=12, pady=4)

    # ── 智能对话标签 ──────────────────────────────────────────────────────────
    def _build_chat(self, parent):
        # 聊天历史区
        hist_frame = tk.Frame(parent, bg=C_BG)
        hist_frame.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        self._chat_hist = scrolledtext.ScrolledText(
            hist_frame, state="disabled", wrap="word",
            bg=C_CONSOLE, fg=C_FG, font=("微软雅黑", 10),
            relief="flat", bd=0, spacing3=4,
        )
        self._chat_hist.pack(fill="both", expand=True)

        # 文字颜色标签
        self._chat_hist.tag_config("user",  foreground="#89B4FA", font=("微软雅黑", 10, "bold"))
        self._chat_hist.tag_config("user_text", foreground="#CDD6F4")
        self._chat_hist.tag_config("bot",   foreground="#A6E3A1", font=("微软雅黑", 10, "bold"))
        self._chat_hist.tag_config("bot_text", foreground="#CDD6F4")
        self._chat_hist.tag_config("tool",  foreground="#F9E2AF", font=("Consolas", 9))
        self._chat_hist.tag_config("error", foreground="#F38BA8")
        self._chat_hist.tag_config("hint",  foreground="#6C7086", font=("微软雅黑", 9, "italic"))

        # 欢迎提示
        self._append_chat("hint", "💡 你可以用自然语言提问，例如：\n"
                                  "   • 帮我预览一下 Wiki 文档分类\n"
                                  "   • 清理一下 Wiki 结构\n"
                                  "   • 查看我今天的日历\n\n")

        # 输入区
        input_frame = tk.Frame(parent, bg=C_BG, pady=8)
        input_frame.pack(fill="x", padx=12, pady=(4, 8))

        self._input = tk.Text(
            input_frame, height=3, wrap="word",
            bg="#313244", fg=C_FG, insertbackground="white",
            font=("微软雅黑", 10), relief="flat", bd=6,
        )
        self._input.pack(side="left", fill="x", expand=True)
        self._input.bind("<Return>", self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)  # Shift+Enter 换行

        btn_frame = tk.Frame(input_frame, bg=C_BG)
        btn_frame.pack(side="right", padx=(8, 0))

        self._send_btn = tk.Button(
            btn_frame, text="发送\n↵", width=6,
            bg=C_BLUE, fg="white", activebackground="#1A5FE0",
            relief="flat", font=("微软雅黑", 9, "bold"), cursor="hand2",
            command=self._send,
        )
        self._send_btn.pack(pady=(0, 4))

        tk.Button(
            btn_frame, text="清空", width=6,
            bg="#45475A", fg=C_FG, activebackground="#585B70",
            relief="flat", font=("微软雅黑", 9), cursor="hand2",
            command=self._clear_chat,
        ).pack()

        # 快捷提示按钮
        hints_frame = tk.Frame(parent, bg=C_BG)
        hints_frame.pack(fill="x", padx=12, pady=(0, 8))
        hints = [
            "预览 Wiki 分类",
            "执行 Wiki 整理",
            "清理 Wiki 结构",
            "查看日历",
            "查看消息",
        ]
        for h in hints:
            tk.Button(
                hints_frame, text=h,
                bg="#313244", fg="#A6ADC8",
                activebackground=C_BLUE, activeforeground="white",
                relief="flat", font=("微软雅黑", 8), cursor="hand2",
                padx=8, pady=3,
                command=lambda t=h: self._quick_send(t),
            ).pack(side="left", padx=3)

    # ── 对话逻辑 ──────────────────────────────────────────────────────────────
    def _get_agent(self):
        if self._agent is None:
            api_key = _load_api_key()
            if not api_key:
                self._append_chat("error", "❌ 未找到 API Key，请检查 config/credentials.json\n\n")
                return None
            try:
                sys.path.insert(0, BASE_DIR)
                from src.chat.chat_agent import FeishuChatAgent
                self._agent = FeishuChatAgent(api_key)
            except Exception as e:
                self._append_chat("error", f"❌ 初始化 Agent 失败: {e}\n\n")
                return None
        return self._agent

    def _on_enter(self, event):
        if event.state & 0x1:   # Shift+Enter → 换行
            return
        self._send()
        return "break"

    def _quick_send(self, text):
        self._input.delete(1.0, tk.END)
        self._input.insert(tk.END, text)
        self._send()

    def _send(self):
        msg = self._input.get(1.0, tk.END).strip()
        if not msg:
            return
        self._input.delete(1.0, tk.END)
        self._send_btn.config(state="disabled", text="处理中\n...")

        self._append_chat("user", "你：\n")
        self._append_chat("user_text", f"{msg}\n\n")

        agent = self._get_agent()
        if not agent:
            self._send_btn.config(state="normal", text="发送\n↵")
            return

        def _run():
            try:
                def on_tool_start(name):
                    self.after(0, lambda: self._append_chat(
                        "tool", f"  🔧 正在执行: {name} ...\n"))

                def on_tool_done(name, result):
                    # 只显示最后几行结果，避免刷屏
                    lines = result.strip().split("\n")
                    preview = "\n".join(lines[-6:]) if len(lines) > 6 else result.strip()
                    self.after(0, lambda: self._append_chat(
                        "tool", f"  📋 {preview}\n\n"))

                reply = agent.chat(msg, on_tool_start=on_tool_start, on_tool_done=on_tool_done)
                self.after(0, lambda: self._append_chat("bot", "飞书 Agent：\n"))
                self.after(0, lambda: self._append_chat("bot_text", f"{reply}\n\n"))
            except Exception as e:
                self.after(0, lambda: self._append_chat("error", f"❌ 错误: {e}\n\n"))
            finally:
                self.after(0, lambda: self._send_btn.config(state="normal", text="发送\n↵"))

        threading.Thread(target=_run, daemon=True).start()

    def _append_chat(self, tag: str, text: str):
        self._chat_hist.config(state="normal")
        self._chat_hist.insert(tk.END, text, tag)
        self._chat_hist.see(tk.END)
        self._chat_hist.config(state="disabled")

    def _clear_chat(self):
        self._chat_hist.config(state="normal")
        self._chat_hist.delete(1.0, tk.END)
        self._chat_hist.config(state="disabled")
        if self._agent:
            self._agent.clear()
        self._append_chat("hint", "💡 对话已清空，上下文已重置。\n\n")


if __name__ == "__main__":
    App().mainloop()
