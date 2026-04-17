"""
飞书智能 Agent — GUI 启动器
双击 launcher.pyw 或 启动器.bat 运行
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
import tkinter.messagebox as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON   = sys.executable
ASSETS   = os.path.join(BASE_DIR, "assets")


# ── 图标工具 ──────────────────────────────────────────────────────────────────

def _ensure_icons():
    """首次运行时自动生成图标"""
    if not os.path.exists(os.path.join(ASSETS, "icon.ico")):
        gen = os.path.join(BASE_DIR, "create_icons.py")
        if os.path.exists(gen):
            try:
                subprocess.run([PYTHON, gen], cwd=BASE_DIR,
                               capture_output=True, timeout=15)
            except Exception:
                pass


def _load_photo(filename: str, size: int = 32):
    """加载图片为 PhotoImage，失败返回 None"""
    path = os.path.join(ASSETS, filename)
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).resize((size, size), Image.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        return ph
    except Exception:
        try:
            return tk.PhotoImage(file=path)
        except Exception:
            return None


def _make_avatar(parent, text: str, color: str, size: int = 34) -> tk.Canvas:
    """用 Canvas 绘制彩色圆形头像（无需图片文件）"""
    c = tk.Canvas(parent, width=size, height=size,
                  highlightthickness=0, bg=parent.cget("bg"))
    r = size // 2
    c.create_oval(1, 1, size - 1, size - 1, fill=color, outline="")
    c.create_text(r, r, text=text,
                  font=("Segoe UI Emoji", int(size * 0.42)),
                  fill="white")
    return c


# ── 聊天窗口 ──────────────────────────────────────────────────────────────────

class ChatWindow(tk.Toplevel):
    BG      = "#F0F2F5"
    USER_BG = "#1B6FF5"
    BOT_BG  = "#FFFFFF"

    def __init__(self, parent):
        super().__init__(parent)
        self.title("飞书智能助理 — 对话")
        self.geometry("820x620")
        self.minsize(640, 440)
        self.configure(bg=self.BG)
        self._q: queue.Queue = queue.Queue()
        self._build()
        self.after(100, self._poll)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # ── 聊天记录区 ──
        frame = tk.Frame(self, bg=self.BG)
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self._log = scrolledtext.ScrolledText(
            frame, state="disabled", wrap=tk.WORD,
            font=("Microsoft YaHei", 10), bg=self.BG,
            relief=tk.FLAT, bd=0,
        )
        self._log.grid(row=0, column=0, sticky="nsew")

        # 标签配置
        self._log.tag_config("user_name",  foreground="#1B6FF5",
                             font=("Microsoft YaHei", 9, "bold"), spacing1=10)
        self._log.tag_config("user_text",  background="#DCF8C6",
                             lmargin1=50, rmargin=12, spacing3=6,
                             font=("Microsoft YaHei", 10))
        self._log.tag_config("bot_name",   foreground="#28A745",
                             font=("Microsoft YaHei", 9, "bold"), spacing1=10)
        self._log.tag_config("bot_text",   background=self.BOT_BG,
                             lmargin1=12, rmargin=50, spacing3=6,
                             font=("Microsoft YaHei", 10))
        self._log.tag_config("sys_text",   foreground="#888888",
                             font=("Microsoft YaHei", 9), spacing1=4, spacing3=4,
                             lmargin1=12)
        self._log.tag_config("tool_text",  foreground="#FF8C00",
                             font=("Consolas", 9), spacing1=2, lmargin1=20)

        # ── 输入区 ──
        bar = tk.Frame(self, bg="#E4E6EB", pady=8)
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        bar.columnconfigure(1, weight=1)

        # 用户头像
        user_av = _make_avatar(bar, "👤", "#1B6FF5", 34)
        user_av.grid(row=0, column=0, padx=(8, 6))

        self._var = tk.StringVar()
        self._entry = ttk.Entry(bar, textvariable=self._var,
                                font=("Microsoft YaHei", 11))
        self._entry.grid(row=0, column=1, sticky="ew", ipady=7)
        self._entry.bind("<Return>", self._send)

        self._btn = ttk.Button(bar, text="发送", command=self._send, width=7)
        self._btn.grid(row=0, column=2, padx=(6, 4))

        ttk.Button(bar, text="清空", command=self._clear, width=5).grid(
            row=0, column=3, padx=(0, 8))

        # 状态栏
        self._status = tk.StringVar(value="就绪，请输入指令")
        tk.Label(self, textvariable=self._status, anchor="w",
                 bg="#D0D3D8", font=("Microsoft YaHei", 9)).grid(
            row=2, column=0, sticky="ew")

        self._append_sys("助理已就绪 — 可输入：\"抓取 GitHub Trending 日报\"、\"整理 Wiki 文档\" 等")

    # ── 消息追加 ──────────────────────────────────────────────────────────────

    def _write(self, *pairs):
        self._log.config(state="normal")
        for text, tag in pairs:
            self._log.insert(tk.END, text, tag)
        self._log.config(state="disabled")
        self._log.see(tk.END)

    def _append_user(self, text: str):
        self._write(("  你\n", "user_name"), (f"  {text}\n", "user_text"))

    def _append_bot(self, text: str):
        self._write(("  助理\n", "bot_name"), (f"  {text}\n", "bot_text"))

    def _append_sys(self, text: str):
        self._write((f"  ℹ {text}\n", "sys_text"))

    def _append_tool(self, text: str):
        self._write((f"  ⚙ {text}\n", "tool_text"))

    def _clear(self):
        self._log.config(state="normal")
        self._log.delete("1.0", tk.END)
        self._log.config(state="disabled")
        self._append_sys("历史已清空")

    # ── 发送 / 执行 ───────────────────────────────────────────────────────────

    def _send(self, _=None):
        msg = self._var.get().strip()
        if not msg:
            return
        self._var.set("")
        self._append_user(msg)
        self._btn.config(state="disabled")
        self._status.set("助理思考中…")
        threading.Thread(target=self._run, args=(msg,), daemon=True).start()

    def _run(self, message: str):
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [PYTHON, os.path.join(BASE_DIR, "agent_cli.py"), message],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=BASE_DIR, env=env,
            )
            lines = []
            for line in proc.stdout:
                line = line.rstrip()
                lines.append(line)
                # 实时显示工具调用行
                if line.startswith("[调用工具]") or line.startswith("[工具完成]"):
                    self._q.put(("tool", line))
            proc.wait()
            reply = "\n".join(
                l for l in lines
                if not l.startswith("[调用工具]") and not l.startswith("[工具完成]")
            ).strip()
            self._q.put(("bot", reply or "(无回复)"))
        except Exception as e:
            self._q.put(("sys", f"执行出错: {e}"))

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "bot":
                    self._append_bot(data)
                    self._btn.config(state="normal")
                    self._status.set("就绪")
                elif kind == "tool":
                    self._append_tool(data)
                elif kind == "sys":
                    self._append_sys(data)
                    self._btn.config(state="normal")
                    self._status.set("就绪")
        except queue.Empty:
            pass
        self.after(80, self._poll)


# ── 主启动器 ──────────────────────────────────────────────────────────────────

class LauncherApp(tk.Tk):
    BLUE   = "#1B6FF5"
    GREEN  = "#28A745"
    ORANGE = "#FF6B35"
    GRAY   = "#6C757D"
    GOLD   = "#E6A817"
    TEAL   = "#17A2B8"

    def __init__(self):
        super().__init__()
        self.title("飞书智能 Agent")
        self.resizable(False, False)
        self.configure(bg="#F8F9FA")
        _ensure_icons()
        self._photos = []          # 防止被 GC
        self._set_icon()
        self._build()

    def _set_icon(self):
        for fname, method in [("icon.ico", "iconbitmap"), ("icon.png", "iconphoto")]:
            path = os.path.join(ASSETS, fname)
            if not os.path.exists(path):
                continue
            try:
                if method == "iconbitmap":
                    self.iconbitmap(path)
                    return
                else:
                    ph = _load_photo(fname, 32)
                    if ph:
                        self.iconphoto(True, ph)
                        self._photos.append(ph)
                        return
            except Exception:
                pass

    def _build(self):
        # ── 顶部 Header ──
        header = tk.Frame(self, bg=self.BLUE, padx=16, pady=14)
        header.pack(fill="x")

        # Logo 头像
        logo_av = _make_avatar(header, "飞", self.BLUE, 52)
        # 尝试加载真实 logo
        logo_ph = _load_photo("logo.png", 52)
        if logo_ph:
            lbl = tk.Label(header, image=logo_ph, bg=self.BLUE)
            self._photos.append(logo_ph)
        else:
            lbl = logo_av
        lbl.pack(side="left", padx=(0, 12))

        tk.Label(header,
                 text="飞书智能 Agent",
                 font=("Microsoft YaHei", 20, "bold"),
                 bg=self.BLUE, fg="white").pack(side="left")

        # ── 功能按钮 ──
        frame = tk.Frame(self, bg="#F8F9FA", padx=20, pady=14)
        frame.pack(fill="both", expand=True)

        btns = [
            ("🤖  打开对话助理",           self.BLUE,   self._open_chat),
            ("📈  GitHub Trending 日报",   self.GREEN,  self._run_github),
            ("🧵  Reddit AI 日报",         self.ORANGE, self._run_reddit),
            ("📚  整理 Wiki（预览）",       self.GRAY,   self._wiki_preview),
            ("✅  整理 Wiki（执行）",       self.GOLD,   self._wiki_execute),
            ("🕐  启动每日定时任务",        self.TEAL,   self._start_scheduler),
        ]
        for text, color, cmd in btns:
            self._make_btn(frame, text, color, cmd)

        # ── 状态栏 ──
        self._status = tk.StringVar(value="就绪")
        tk.Label(self, textvariable=self._status, anchor="w",
                 bg="#DEE2E6", font=("Microsoft YaHei", 9),
                 padx=8, pady=3).pack(fill="x", side="bottom")

    def _make_btn(self, parent, text, color, cmd):
        f = tk.Frame(parent, bg="#F8F9FA")
        f.pack(fill="x", pady=4)
        btn = tk.Button(
            f, text=text, command=cmd,
            font=("Microsoft YaHei", 11), bg=color, fg="white",
            activebackground=color, activeforeground="white",
            relief=tk.FLAT, padx=18, pady=10,
            cursor="hand2", width=30, anchor="w",
        )
        btn.pack(fill="x")
        # hover 效果
        darker = self._darken(color)
        btn.bind("<Enter>", lambda e, b=btn, c=darker: b.config(bg=c))
        btn.bind("<Leave>", lambda e, b=btn, c=color: b.config(bg=c))

    @staticmethod
    def _darken(hex_color: str, factor: float = 0.85) -> str:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r * factor), int(g * factor), int(b * factor))

    # ── 动作 ─────────────────────────────────────────────────────────────────

    def _open_chat(self):
        ChatWindow(self)

    def _start_scheduler(self):
        sch = os.path.join(BASE_DIR, "scheduler.py")
        if not os.path.exists(sch):
            mb.showwarning("提示", "scheduler.py 不存在，请先拉取最新代码")
            return
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.Popen(
            [PYTHON, sch],
            cwd=BASE_DIR, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._status.set("定时任务已在后台启动（每天 11:30 自动执行）")
        mb.showinfo("定时任务", "✅ 每日定时任务已启动！\n将在每天 11:30 自动执行 Reddit + GitHub 日报并通知您。\n关闭此窗口不影响后台任务。")

    def _run_sub(self, label: str, cmd_args: list):
        self._status.set(f"运行中: {label}…")
        self.update()
        def worker():
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            p = subprocess.Popen(
                [PYTHON] + cmd_args, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
                cwd=BASE_DIR, env=env,
            )
            out, _ = p.communicate(timeout=600)
            self.after(0, lambda: self._show(label, out))
        threading.Thread(target=worker, daemon=True).start()

    def _show(self, label: str, output: str):
        self._status.set(f"{label} 完成")
        win = tk.Toplevel(self)
        win.title(f"结果 — {label}")
        win.geometry("720x520")
        win.configure(bg="#F8F9FA")
        tk.Label(win, text=f"✅ {label} 执行完成",
                 font=("Microsoft YaHei", 12, "bold"),
                 bg="#F8F9FA", fg="#28A745").pack(pady=(10, 4))
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 10),
                                        bg="#1E1E1E", fg="#D4D4D4", relief=tk.FLAT)
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        txt.insert(tk.END, output or "(无输出)")
        txt.config(state="disabled")

    def _run_github(self):
        self._run_sub("GitHub Trending", ["main.py", "daily-github"])

    def _run_reddit(self):
        self._run_sub("Reddit 日报", ["main.py", "daily-reddit"])

    def _wiki_preview(self):
        self._run_sub("Wiki 预览", ["main.py", "organize", "--dry-run"])

    def _wiki_execute(self):
        if mb.askyesno("确认执行", "将实际移动 Wiki 文档，确认执行？"):
            self._run_sub("Wiki 整理", ["main.py", "organize"])


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
