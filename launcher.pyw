"""
飞书智能管理 Agent — 桌面 GUI 启动器
双击运行（pythonw launcher.pyw）或命令行：python launcher.pyw
"""
import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class _StreamRedirect:
    """把 write() 调用转发到 queue，用于捕获 stdout/stderr"""

    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text: str):
        if text:
            self._q.put(text)

    def flush(self):
        pass

    def isatty(self):
        return False


class FeishuAgentGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("飞书智能管理 Agent")
        self.root.geometry("900x620")
        self.root.minsize(700, 480)

        self._client = None
        self._log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._redirect_streams()
        self._poll_log_queue()
        threading.Thread(target=self._init_client, daemon=True).start()

    # ── UI 构建 ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # 控制台 Tab
        console_frame = ttk.Frame(notebook)
        notebook.add(console_frame, text=" 控制台 ")
        self._console = scrolledtext.ScrolledText(
            console_frame, state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4",
            font=("Courier New", 10), wrap=tk.WORD,
        )
        self._console.pack(fill=tk.BOTH, expand=True)

        # 聊天 Tab
        chat_frame = ttk.Frame(notebook)
        notebook.add(chat_frame, text=" 聊天 ")

        self._chat = scrolledtext.ScrolledText(
            chat_frame, state=tk.DISABLED,
            bg="#ffffff", font=("Microsoft YaHei", 10), wrap=tk.WORD,
        )
        self._chat.pack(fill=tk.BOTH, expand=True)
        self._chat.tag_config("user", foreground="#1a73e8")
        self._chat.tag_config("agent", foreground="#34a853")
        self._chat.tag_config("error", foreground="#ea4335")

        input_bar = ttk.Frame(chat_frame)
        input_bar.pack(fill=tk.X, padx=6, pady=(4, 6))

        self._entry = ttk.Entry(input_bar, font=("Microsoft YaHei", 10))
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entry.bind("<Return>", self._on_send)
        self._entry.focus_set()

        ttk.Button(input_bar, text="发送", command=self._on_send).pack(
            side=tk.RIGHT, padx=(6, 0)
        )

    # ── 流重定向 ───────────────────────────────────────────────────────────────

    def _redirect_streams(self):
        redir = _StreamRedirect(self._log_queue)
        sys.stdout = redir
        sys.stderr = redir

    def _poll_log_queue(self):
        try:
            while True:
                text = self._log_queue.get_nowait()
                self._append_console(text)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    # ── 飞书 Client 初始化 ─────────────────────────────────────────────────────

    def _init_client(self):
        try:
            from src.utils.config_loader import config_loader
            from src.utils.feishu_client import FeishuClientFactory

            creds = config_loader.load_credentials()
            factory = FeishuClientFactory(creds["accounts"])
            self._client = factory.get_client("personal")
            print("✅ 飞书 Client 初始化完成")
        except FileNotFoundError:
            print("⚠️  config/credentials.json 不存在，请复制 .example 文件并填写配置")
        except ValueError as exc:
            print(f"⚠️  配置不完整: {exc}")
        except Exception as exc:
            print(f"❌ 初始化失败: {exc}")

    # ── 控制台输出 ─────────────────────────────────────────────────────────────

    def _append_console(self, text: str):
        self._console.configure(state=tk.NORMAL)
        self._console.insert(tk.END, text)
        self._console.see(tk.END)
        self._console.configure(state=tk.DISABLED)

    # ── 聊天逻辑 ───────────────────────────────────────────────────────────────

    def _on_send(self, _event=None):
        msg = self._entry.get().strip()
        if not msg:
            return
        self._entry.delete(0, tk.END)
        self._append_chat(f"你: {msg}\n", "user")
        threading.Thread(target=self._process, args=(msg,), daemon=True).start()

    def _process(self, msg: str):
        try:
            reply = self._dispatch(msg)
        except Exception as exc:
            reply = f"错误: {exc}"
            tag = "error"
        else:
            tag = "agent"
        self.root.after(0, lambda: self._append_chat(f"Agent: {reply}\n\n", tag))

    def _dispatch(self, msg: str) -> str:
        low = msg.lower()
        if any(k in low for k in ("wiki", "wiki空间", "知识库", "空间")):
            return self._list_wiki_spaces()
        if any(k in low for k in ("日历", "日程", "会议", "事件")):
            return self._list_calendar()
        if any(k in low for k in ("邮件", "收件箱", "mail")):
            return self._list_emails()
        if any(k in low for k in ("日报", "daily", "today", "今天")):
            return self._daily_report()
        return (
            f"未识别的命令: 「{msg}」\n"
            "可用指令示例:\n"
            "  列出 Wiki 空间\n"
            "  查看日历\n"
            "  查看邮件\n"
            "  今日日报"
        )

    def _require_client(self) -> bool:
        return self._client is not None

    def _list_wiki_spaces(self) -> str:
        if not self._require_client():
            return "飞书 Client 未就绪，请稍后重试"
        try:
            from lark_oapi.api.wiki.v2 import ListSpaceRequest

            req = ListSpaceRequest.builder().page_size(50).build()
            resp = self._client.wiki.v2.space.list(req)
            if not resp.success():
                return f"获取 Wiki 空间失败: {resp.msg} (code={resp.code})"
            items = resp.data.items or []
            if not items:
                return "未找到任何 Wiki 空间"
            lines = [f"共 {len(items)} 个 Wiki 空间:"]
            for s in items:
                lines.append(f"  • {s.name}  (space_id={s.space_id})")
            return "\n".join(lines)
        except Exception as exc:
            return f"错误: {exc}"

    def _list_calendar(self) -> str:
        if not self._require_client():
            return "飞书 Client 未就绪"
        try:
            from src.managers.calendar_manager import CalendarManager

            mgr = CalendarManager(self._client)
            events = mgr.list_events(days_ahead=7)
            if not events:
                return "未来 7 天无日历事件（或无权限）"
            lines = [f"未来 7 天日历事件（共 {len(events)} 个）:"]
            for e in events:
                lines.append(f"  • [{e.get('start_time', '')}] {e.get('summary', '')} @ {e.get('location', '')}")
            return "\n".join(lines)
        except Exception as exc:
            return f"错误: {exc}"

    def _list_emails(self) -> str:
        if not self._require_client():
            return "飞书 Client 未就绪"
        try:
            from src.managers.email_manager import EmailManager

            mgr = EmailManager(self._client)
            mails = mgr.list_mails(limit=10)
            if not mails:
                return "收件箱为空（或无权限）"
            lines = [f"最近 {len(mails)} 封邮件:"]
            for m in mails:
                flag = "【未读】" if not m.get("is_read") else "      "
                lines.append(f"  • {flag}{m.get('subject', '(无主题)')}  — {m.get('from', '')}")
            return "\n".join(lines)
        except Exception as exc:
            return f"错误: {exc}"

    def _daily_report(self) -> str:
        if not self._require_client():
            return "飞书 Client 未就绪"
        parts = ["=== 今日日报 ===", ""]

        # 日历
        try:
            from src.managers.calendar_manager import CalendarManager

            events = CalendarManager(self._client).list_events(days_ahead=1)
            parts.append(f"【日历】今日事件 {len(events)} 个")
            for e in events:
                parts.append(f"  {e.get('start_time', '')}  {e.get('summary', '')}")
        except Exception as exc:
            parts.append(f"【日历】获取失败: {exc}")

        parts.append("")

        # 邮件
        try:
            from src.managers.email_manager import EmailManager

            mails = EmailManager(self._client).list_mails(limit=5)
            unread = [m for m in mails if not m.get("is_read")]
            parts.append(f"【邮件】未读 {len(unread)} 封")
            for m in unread:
                parts.append(f"  {m.get('subject', '(无主题)')}")
        except Exception as exc:
            parts.append(f"【邮件】获取失败: {exc}")

        return "\n".join(parts)

    def _append_chat(self, text: str, tag: str = "agent"):
        self._chat.configure(state=tk.NORMAL)
        self._chat.insert(tk.END, text, tag)
        self._chat.see(tk.END)
        self._chat.configure(state=tk.DISABLED)


def main():
    os.makedirs("logs", exist_ok=True)
    root = tk.Tk()
    FeishuAgentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
