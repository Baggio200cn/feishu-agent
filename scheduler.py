"""
飞书智能 Agent — 每日定时调度器
用法:
  python scheduler.py               # 后台运行，默认 11:30 触发每日任务
  python scheduler.py --time 09:00  # 自定义触发时间
  python scheduler.py --now         # 立即执行（测试用）

功能:
  - 每天在指定时间运行 daily-reddit 和 daily-github 子命令
  - 任务完成后发 Windows toast 通知（PowerShell，无额外依赖）
  - 如果 credentials.json 中配置了 feishu_notify.chat_id，还会发飞书消息
  - 显示 Tkinter 状态窗口（下次运行时间 + 最近运行结果）
  - 记录日志到 logs/scheduler.log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import scrolledtext, ttk
from typing import Optional

# ── 路径常量 ─────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CREDS_PATH = os.path.join(BASE_DIR, "config", "credentials.json")
LOG_PATH = os.path.join(BASE_DIR, "logs", "scheduler.log")

# ── 日志初始化 ────────────────────────────────────────────────────────────────

os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

logger = logging.getLogger("scheduler")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

_fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
_fh.setFormatter(_fmt)
_fh.setLevel(logging.DEBUG)
logger.addHandler(_fh)

_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_sh.setLevel(logging.INFO)
logger.addHandler(_sh)

# ── 凭证加载 ─────────────────────────────────────────────────────────────────

def _load_credentials() -> dict:
    """读取 config/credentials.json；文件缺失时返回空字典（不崩溃）。"""
    if not os.path.exists(CREDS_PATH):
        logger.warning("credentials.json 未找到：%s", CREDS_PATH)
        return {}
    try:
        with open(CREDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("读取 credentials.json 失败: %s", exc)
        return {}

# ── Feishu 通知 ───────────────────────────────────────────────────────────────

def _feishu_get_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取飞书 tenant_access_token。"""
    try:
        import requests  # noqa: PLC0415
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        data = resp.json()
        token = data.get("tenant_access_token")
        if not token:
            logger.warning("获取飞书 token 失败: %s", data)
        return token
    except Exception as exc:
        logger.warning("获取飞书 token 异常: %s", exc)
        return None


def _feishu_send_message(chat_id: str, text: str, app_id: str, app_secret: str) -> bool:
    """向指定 chat_id 发送飞书文本消息。返回是否成功。"""
    token = _feishu_get_token(app_id, app_secret)
    if not token:
        return False
    try:
        import requests  # noqa: PLC0415
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        result = resp.json()
        if result.get("code") == 0:
            logger.info("飞书通知发送成功 → chat_id=%s", chat_id)
            return True
        else:
            logger.warning("飞书通知发送失败: %s", result)
            return False
    except Exception as exc:
        logger.warning("飞书通知发送异常: %s", exc)
        return False

# ── Windows Toast 通知 ────────────────────────────────────────────────────────

def _windows_toast(title: str, message: str) -> None:
    """通过 PowerShell 发送 Windows 桌面 toast 通知（无额外依赖）。"""
    if sys.platform != "win32":
        logger.debug("非 Windows 系统，跳过 toast 通知")
        return
    # 转义 PowerShell 特殊字符
    def _ps_escape(s: str) -> str:
        return s.replace("'", "''").replace('"', '`"')

    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.MessageBox] | Out-Null;"
        "$notify = New-Object System.Windows.Forms.NotifyIcon;"
        "$notify.Icon = [System.Drawing.SystemIcons]::Information;"
        "$notify.Visible = $true;"
        f"$notify.ShowBalloonTip(8000, '{_ps_escape(title)}', '{_ps_escape(message)}', "
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Milliseconds 8500;"
        "$notify.Dispose();"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        logger.debug("Windows toast 通知已触发")
    except Exception as exc:
        logger.warning("Windows toast 通知失败: %s", exc)

# ── 任务执行 ─────────────────────────────────────────────────────────────────

def _run_command(cmd_args: list[str]) -> tuple[bool, str]:
    """
    以子进程方式运行指定命令，返回 (成功与否, 合并输出文本)。
    工作目录固定为 BASE_DIR。
    """
    full_cmd = [PYTHON] + cmd_args
    logger.info("执行命令: %s", " ".join(full_cmd))
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            full_cmd,
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,  # 最多等 30 分钟
        )
        combined = (result.stdout or "") + (result.stderr or "")
        success = result.returncode == 0
        status_word = "成功" if success else f"失败(rc={result.returncode})"
        logger.info("命令 %s %s", cmd_args[-1], status_word)
        logger.debug("输出:\n%s", combined[:2000])
        return success, combined
    except subprocess.TimeoutExpired:
        logger.error("命令 %s 超时（>1800s）", cmd_args)
        return False, "命令执行超时"
    except Exception as exc:
        logger.error("命令 %s 执行异常: %s", cmd_args, exc)
        return False, str(exc)


def run_daily_jobs() -> tuple[bool, str]:
    """
    依次执行 daily-reddit 和 daily-github。
    返回 (整体是否全部成功, 汇总文本)。
    """
    results: list[str] = []
    all_ok = True

    for subcmd in ["daily-reddit", "daily-github"]:
        ok, out = _run_command(["main.py", subcmd])
        tag = "OK" if ok else "FAIL"
        snippet = out.strip().splitlines()
        # 取最后几行有意义输出作摘要
        summary_lines = [ln for ln in snippet if ln.strip()][-3:]
        summary = " | ".join(summary_lines) if summary_lines else "(无输出)"
        results.append(f"[{tag}] {subcmd}: {summary}")
        if not ok:
            all_ok = False

    return all_ok, "\n".join(results)


def send_notifications(success: bool, summary: str) -> None:
    """任务完成后发送所有通知（toast + 飞书）。"""
    status_label = "完成" if success else "部分失败"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    toast_title = f"飞书 Agent 每日任务{status_label}"
    toast_body = f"{now_str}\n{summary}"

    # Windows toast
    _windows_toast(toast_title, toast_body)

    # 飞书消息
    creds = _load_credentials()
    notify_cfg = creds.get("feishu_notify", {})
    chat_id = notify_cfg.get("chat_id", "").strip()
    if not chat_id:
        logger.debug("feishu_notify.chat_id 未配置，跳过飞书通知")
        return

    # 优先使用 personal 账号凭证获取 token
    accounts = creds.get("accounts", {})
    personal = accounts.get("personal", {})
    app_id = personal.get("app_id", "")
    app_secret = personal.get("app_secret", "")
    if not app_id or not app_secret or app_id.startswith("cli_personal"):
        logger.warning("personal 账号凭证未配置或为示例值，跳过飞书通知")
        return

    feishu_text = f"[飞书 Agent 每日任务{status_label}] {now_str}\n{summary}"
    _feishu_send_message(chat_id, feishu_text, app_id, app_secret)

# ── 时间工具 ─────────────────────────────────────────────────────────────────

def _parse_hhmm(s: str) -> tuple[int, int]:
    """将 'HH:MM' 解析为 (hour, minute)；格式有误时抛出 ValueError。"""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM，实际: {s!r}")
    return int(parts[0]), int(parts[1])


def _next_run_dt(hour: int, minute: int) -> datetime:
    """计算下一次应触发的 datetime（今天或明天）。"""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate

# ── 调度核心线程 ──────────────────────────────────────────────────────────────

class SchedulerThread(threading.Thread):
    """
    后台线程：每 30 秒检查一次当前时间，到达目标时间后触发每日任务。
    每天只触发一次（状态在午夜重置）。
    """

    SLEEP_INTERVAL = 30  # 秒

    def __init__(self, run_hour: int, run_minute: int, event_queue: queue.Queue):
        super().__init__(daemon=True, name="SchedulerThread")
        self.run_hour = run_hour
        self.run_minute = run_minute
        self._event_queue = event_queue  # 向 UI 发送事件
        self._stop_event = threading.Event()
        self._ran_today: Optional[str] = None  # 记录已执行的日期字符串

    def stop(self):
        self._stop_event.set()

    def _today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _should_run(self) -> bool:
        now = datetime.now()
        return (
            now.hour == self.run_hour
            and now.minute == self.run_minute
            and self._ran_today != self._today_str()
        )

    def _reset_if_new_day(self):
        today = self._today_str()
        if self._ran_today and self._ran_today != today:
            logger.info("新的一天 (%s)，重置已执行标记", today)
            self._ran_today = None

    def run(self):
        logger.info(
            "调度器启动，每日触发时间: %02d:%02d", self.run_hour, self.run_minute
        )
        self._emit_next_run()

        while not self._stop_event.is_set():
            self._reset_if_new_day()

            if self._should_run():
                self._ran_today = self._today_str()
                logger.info("=== 开始执行每日任务 ===")
                self._event_queue.put(("running", "正在执行每日任务…"))

                success, summary = run_daily_jobs()

                ts = datetime.now().strftime("%H:%M:%S")
                result_msg = f"[{ts}] {'全部成功' if success else '部分失败'}\n{summary}"
                logger.info("每日任务完成:\n%s", summary)
                self._event_queue.put(("done", result_msg))

                send_notifications(success, summary)
                self._emit_next_run()

            self._stop_event.wait(self.SLEEP_INTERVAL)

        logger.info("调度器已停止")

    def _emit_next_run(self):
        nxt = _next_run_dt(self.run_hour, self.run_minute)
        self._event_queue.put(("next_run", nxt.strftime("%Y-%m-%d %H:%M")))

# ── Tkinter 状态窗口 ──────────────────────────────────────────────────────────

class SchedulerWindow(tk.Tk):
    """
    简单的状态窗口：显示下次运行时间和最近一次运行结果。
    关闭窗口时停止调度器线程并退出程序。
    """

    def __init__(self, run_hour: int, run_minute: int):
        super().__init__()
        self.title("飞书 Agent 每日调度器")
        self.resizable(False, False)
        self._eq: queue.Queue = queue.Queue()
        self._scheduler = SchedulerThread(run_hour, run_minute, self._eq)
        self._build_ui(run_hour, run_minute)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self, run_hour: int, run_minute: int):
        PAD = {"padx": 12, "pady": 6}

        # 标题行
        header = tk.Frame(self, bg="#1B6FF5", pady=10)
        header.pack(fill="x")
        tk.Label(
            header, text="飞书 Agent 每日调度器",
            font=("Microsoft YaHei", 14, "bold"),
            bg="#1B6FF5", fg="white",
        ).pack()

        # 配置信息
        info_frame = tk.Frame(self, padx=16, pady=8)
        info_frame.pack(fill="x")
        tk.Label(
            info_frame,
            text=f"每日触发时间: {run_hour:02d}:{run_minute:02d}",
            font=("Microsoft YaHei", 10),
            fg="#555555",
        ).pack(anchor="w")

        # 下次运行时间
        next_frame = tk.LabelFrame(self, text="下次运行", padx=12, pady=6,
                                   font=("Microsoft YaHei", 9))
        next_frame.pack(fill="x", padx=16, pady=(4, 0))
        self._next_run_var = tk.StringVar(value="计算中…")
        tk.Label(
            next_frame, textvariable=self._next_run_var,
            font=("Microsoft YaHei", 12, "bold"), fg="#1B6FF5",
        ).pack(anchor="w")

        # 状态指示
        status_frame = tk.Frame(self, padx=16, pady=4)
        status_frame.pack(fill="x")
        self._status_var = tk.StringVar(value="等待中")
        self._status_label = tk.Label(
            status_frame, textvariable=self._status_var,
            font=("Microsoft YaHei", 10), fg="#28A745",
        )
        self._status_label.pack(anchor="w")

        # 最近运行结果
        result_frame = tk.LabelFrame(self, text="最近运行结果", padx=12, pady=6,
                                     font=("Microsoft YaHei", 9))
        result_frame.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self._result_text = scrolledtext.ScrolledText(
            result_frame, height=8, width=56, wrap=tk.WORD,
            font=("Consolas", 9), state="disabled", relief=tk.FLAT,
            bg="#F8F8F8",
        )
        self._result_text.pack(fill="both", expand=True)

        # 手动触发按钮
        btn_frame = tk.Frame(self, padx=16, pady=10)
        btn_frame.pack(fill="x")
        ttk.Button(
            btn_frame, text="立即执行任务", command=self._manual_run,
        ).pack(side="left")
        ttk.Button(
            btn_frame, text="查看日志", command=self._open_log,
        ).pack(side="left", padx=(8, 0))

        # 底部提示
        tk.Label(
            self,
            text="关闭此窗口将停止调度器",
            font=("Microsoft YaHei", 8), fg="#999999",
        ).pack(pady=(0, 6))

        self.geometry("440x380")

    def _set_result(self, text: str):
        self._result_text.config(state="normal")
        self._result_text.delete("1.0", tk.END)
        self._result_text.insert(tk.END, text)
        self._result_text.config(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, data = self._eq.get_nowait()
                if kind == "next_run":
                    self._next_run_var.set(data)
                elif kind == "running":
                    self._status_var.set("执行中…")
                    self._status_label.config(fg="#FF8C00")
                    self._set_result("正在执行每日任务，请稍候…")
                elif kind == "done":
                    self._status_var.set("上次执行完毕")
                    self._status_label.config(fg="#28A745")
                    self._set_result(data)
        except queue.Empty:
            pass
        self.after(500, self._poll_queue)

    def _manual_run(self):
        """在独立线程中立即触发一次任务（不影响定时逻辑）。"""
        self._status_var.set("手动触发中…")
        self._status_label.config(fg="#FF8C00")
        self._set_result("正在执行每日任务，请稍候…")

        def _worker():
            success, summary = run_daily_jobs()
            ts = datetime.now().strftime("%H:%M:%S")
            result_msg = f"[{ts}] 手动触发 — {'全部成功' if success else '部分失败'}\n{summary}"
            self._eq.put(("done", result_msg))
            send_notifications(success, summary)

        threading.Thread(target=_worker, daemon=True).start()

    def _open_log(self):
        """在弹窗中显示最近 200 行日志。"""
        win = tk.Toplevel(self)
        win.title("调度器日志")
        win.geometry("700x500")
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            content = "".join(lines[-200:])
        except Exception as exc:
            content = f"读取日志失败: {exc}"
        txt.insert(tk.END, content)
        txt.see(tk.END)
        txt.config(state="disabled")

    def _on_close(self):
        logger.info("窗口关闭，停止调度器")
        self._scheduler.stop()
        self.destroy()

    def run(self):
        self._scheduler.start()
        self.after(500, self._poll_queue)
        self.mainloop()

# ── --now 模式（无 UI，直接运行后退出）────────────────────────────────────────

def run_now_mode():
    """立即执行每日任务，发送通知，然后退出。用于测试或一次性触发。"""
    logger.info("=== --now 模式：立即执行每日任务 ===")
    success, summary = run_daily_jobs()
    logger.info("任务完成:\n%s", summary)
    print("\n" + ("=" * 60))
    print("每日任务执行完毕：", "全部成功" if success else "部分失败")
    print(summary)
    print("=" * 60)
    send_notifications(success, summary)

# ── 入口 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="飞书 Agent 每日调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scheduler.py               # 默认 11:30 触发\n"
            "  python scheduler.py --time 09:00  # 自定义时间\n"
            "  python scheduler.py --now         # 立即执行（测试）\n"
        ),
    )
    parser.add_argument(
        "--time",
        default="11:30",
        metavar="HH:MM",
        help="每日触发时间，格式 HH:MM（默认: 11:30）",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="立即执行任务后退出（不启动调度循环，用于测试）",
    )
    args = parser.parse_args()

    try:
        run_hour, run_minute = _parse_hhmm(args.time)
    except ValueError as exc:
        parser.error(str(exc))
        return  # unreachable, silences type checker

    if args.now:
        run_now_mode()
        return

    # 常规模式：启动带 UI 的调度器
    logger.info("启动 Tkinter 调度器窗口，触发时间 %02d:%02d", run_hour, run_minute)
    app = SchedulerWindow(run_hour, run_minute)
    app.run()


if __name__ == "__main__":
    main()
