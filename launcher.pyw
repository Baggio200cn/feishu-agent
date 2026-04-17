"""
飞书智能 Agent — GUI 启动器
双击运行，提供图形化对话界面
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CREDS_PATH = os.path.join(BASE_DIR, "credentials.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_icon(widget, filename: str, size: int = 32):
    """安全加载图片，失败时返回 None（不崩溃）"""
    path = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        # 必须保持引用，防止被 GC
        widget._photo_refs = getattr(widget, "_photo_refs", [])
        widget._photo_refs.append(photo)
        return photo
    except Exception:
        try:
            photo = tk.PhotoImage(file=path)
            return photo
        except Exception:
            return None


def _load_creds():
    if not os.path.exists(CREDS_PATH):
        return {}
    try:
        with open(CREDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── 聊天窗口 ──────────────────────────────────────────────────────────────────

class ChatWindow(tk.Toplevel):
    """对话式 AI 助理窗口"""

    USER_COLOR   = "#DCF8C6"
    AGENT_COLOR  = "#FFFFFF"
    SYSTEM_COLOR = "#F0F0F0"

    def __init__(self, parent):
        super().__init__(parent)
        self.title("飞书智能助理")
        self.geometry("780x580")
        self.minsize(600, 400)
        self._queue: queue.Queue = queue.Queue()
        self._proc = None
        self._build_ui()
        self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # 聊天记录区
        frame_chat = tk.Frame(self, bg="#F5F5F5")
        frame_chat.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        frame_chat.columnconfigure(0, weight=1)
        frame_chat.rowconfigure(0, weight=1)

        self._chat_text = scrolledtext.ScrolledText(
            frame_chat, state="disabled", wrap=tk.WORD,
            font=("Microsoft YaHei", 10), bg="#F5F5F5", relief=tk.FLAT,
        )
        self._chat_text.grid(row=0, column=0, sticky="nsew")
        self._chat_text.tag_config("user",   background=self.USER_COLOR,  lmargin1=80, rmargin=8, spacing3=6)
        self._chat_text.tag_config("agent",  background=self.AGENT_COLOR, lmargin1=8,  rmargin=80, spacing3=6)
        self._chat_text.tag_config("system", background=self.SYSTEM_COLOR, lmargin1=8,  rmargin=8,  spacing3=4,
                                   font=("Microsoft YaHei", 9), foreground="#666666")

        # 输入区
        frame_input = tk.Frame(self, bg="#ECECEC")
        frame_input.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        frame_input.columnconfigure(0, weight=1)

        # 角色图标
        self._agent_icon_lbl = tk.Label(frame_input, bg="#ECECEC")
        icon = _load_icon(self._agent_icon_lbl, "agent.png", 36)
        if icon:
            self._agent_icon_lbl.config(image=icon)
        else:
            self._agent_icon_lbl.config(text="🤖", font=("", 20))
        self._agent_icon_lbl.grid(row=0, column=0, padx=(0, 6))

        self._input_var = tk.StringVar()
        self._entry = ttk.Entry(frame_input, textvariable=self._input_var, font=("Microsoft YaHei", 11))
        self._entry.grid(row=0, column=1, sticky="ew", ipady=6)
        self._entry.bind("<Return>", self._on_send)

        self._send_btn = ttk.Button(frame_input, text="发送", command=self._on_send, width=8)
        self._send_btn.grid(row=0, column=2, padx=(6, 0))

        self._clear_btn = ttk.Button(frame_input, text="清空", command=self._clear_history, width=6)
        self._clear_btn.grid(row=0, column=3, padx=(4, 0))

        # 状态栏
        self._status_var = tk.StringVar(value="就绪")
        status_bar = tk.Label(self, textvariable=self._status_var, anchor="w",
                              bg="#DDDDDD", font=("Microsoft YaHei", 9))
        status_bar.grid(row=2, column=0, sticky="ew")

        self._append_system("助理已启动，请输入指令（例如：\"抓取 GitHub Trending 日报\"）")

    def _append(self, text: str, tag: str):
        self._chat_text.config(state="normal")
        self._chat_text.insert(tk.END, text + "\n", tag)
        self._chat_text.config(state="disabled")
        self._chat_text.see(tk.END)

    def _append_user(self, text: str):
        self._append(f"你: {text}", "user")

    def _append_agent(self, text: str):
        self._append(f"助理: {text}", "agent")

    def _append_system(self, text: str):
        self._append(f"[系统] {text}", "system")

    def _clear_history(self):
        self._chat_text.config(state="normal")
        self._chat_text.delete("1.0", tk.END)
        self._chat_text.config(state="disabled")
        self._append_system("历史已清空")

    def _on_send(self, _event=None):
        msg = self._input_var.get().strip()
        if not msg:
            return
        self._input_var.set("")
        self._append_user(msg)
        self._send_btn.config(state="disabled")
        self._status_var.set("助理思考中…")
        threading.Thread(target=self._run_agent, args=(msg,), daemon=True).start()

    def _run_agent(self, message: str):
        try:
            cmd = [PYTHON, os.path.join(BASE_DIR, "agent_cli.py"), message]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=BASE_DIR,
            )
            output_lines = []
            for line in proc.stdout:
                output_lines.append(line.rstrip())
                self._queue.put(("stream", line.rstrip()))
            proc.wait()
            full = "\n".join(output_lines).strip()
            self._queue.put(("done", full))
        except Exception as e:
            self._queue.put(("error", str(e)))

    def _poll_queue(self):
        try:
            while True:
                kind, data = self._queue.get_nowait()
                if kind == "done":
                    self._append_agent(data if data else "(无输出)")
                    self._send_btn.config(state="normal")
                    self._status_var.set("就绪")
                elif kind == "error":
                    self._append_system(f"执行失败: {data}")
                    self._send_btn.config(state="normal")
                    self._status_var.set("就绪")
                # "stream" lines shown via append_agent in real-time is complex; skip for simplicity
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_close(self):
        self.destroy()


# ── 主启动器窗口 ──────────────────────────────────────────────────────────────

class LauncherApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("飞书智能 Agent")
        self.resizable(False, False)
        self._build_ui()
        self._set_window_icon()

    def _set_window_icon(self):
        icon_path = os.path.join(ASSETS_DIR, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
                return
            except Exception:
                pass
        # 尝试 PNG 图标
        png_path = os.path.join(ASSETS_DIR, "icon.png")
        if os.path.exists(png_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(png_path).resize((32, 32), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, photo)
                self._icon_ref = photo
            except Exception:
                try:
                    photo = tk.PhotoImage(file=png_path)
                    self.iconphoto(True, photo)
                    self._icon_ref = photo
                except Exception:
                    pass

    def _build_ui(self):
        # 顶部 Logo 区
        header = tk.Frame(self, bg="#1B6FF5", pady=12)
        header.pack(fill="x")

        logo_lbl = tk.Label(header, bg="#1B6FF5")
        logo = _load_icon(logo_lbl, "logo.png", 48)
        if logo:
            logo_lbl.config(image=logo)
        else:
            logo_lbl.config(text="🚀", font=("", 28), fg="white")
        logo_lbl.pack(side="left", padx=16)

        tk.Label(
            header, text="飞书智能 Agent", font=("Microsoft YaHei", 18, "bold"),
            bg="#1B6FF5", fg="white",
        ).pack(side="left")

        # 功能按钮区
        frame = tk.Frame(self, padx=24, pady=16)
        frame.pack(fill="both", expand=True)

        buttons = [
            ("🤖  打开对话助理",         self._open_chat,         "#1B6FF5"),
            ("📈  GitHub Trending 日报", self._run_github_daily,  "#28A745"),
            ("🧵  Reddit AI 日报",       self._run_reddit_daily,  "#FF4500"),
            ("📚  整理 Wiki 文档（预览）", self._run_wiki_preview,  "#6C757D"),
            ("✅  整理 Wiki 文档（执行）", self._run_wiki_execute,  "#FFC107"),
        ]

        for text, cmd, color in buttons:
            btn = tk.Button(
                frame, text=text, command=cmd,
                font=("Microsoft YaHei", 11), bg=color, fg="white",
                activebackground=color, activeforeground="white",
                relief=tk.FLAT, padx=20, pady=10, cursor="hand2", width=28,
            )
            btn.pack(fill="x", pady=4)

        # 状态栏
        self._status = tk.StringVar(value="就绪")
        tk.Label(self, textvariable=self._status, anchor="w",
                 bg="#EEEEEE", font=("Microsoft YaHei", 9)).pack(fill="x", side="bottom")

    # ── 按钮动作 ──────────────────────────────────────────────────────────────

    def _open_chat(self):
        ChatWindow(self)

    def _run_subprocess(self, label: str, args: list):
        self._status.set(f"运行中: {label}…")
        self.update()
        def _worker():
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [PYTHON] + args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=BASE_DIR, env=env,
            )
            out, _ = proc.communicate(timeout=600)
            self.after(0, lambda: self._show_result(label, out))
        threading.Thread(target=_worker, daemon=True).start()

    def _show_result(self, label: str, output: str):
        self._status.set(f"{label} 完成")
        win = tk.Toplevel(self)
        win.title(f"结果: {label}")
        win.geometry("700x500")
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert(tk.END, output or "(无输出)")
        txt.config(state="disabled")

    def _run_github_daily(self):
        self._run_subprocess("GitHub Trending", ["main.py", "daily-github"])

    def _run_reddit_daily(self):
        self._run_subprocess("Reddit 日报", ["main.py", "daily-reddit"])

    def _run_wiki_preview(self):
        self._run_subprocess("Wiki 整理预览", ["main.py", "organize", "--dry-run"])

    def _run_wiki_execute(self):
        import tkinter.messagebox as mb
        if mb.askyesno("确认", "将实际移动 Wiki 文档，确认执行？"):
            self._run_subprocess("Wiki 整理执行", ["main.py", "organize"])


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
