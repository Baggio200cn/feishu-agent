"""
飞书 Agent 定时任务调度器

基于 APScheduler，支持 cron 风格触发器。
任务列表与时间在 config/credentials.json 的 `schedule.jobs` 中定义。

每次任务运行后会更新 logs/scheduler_state.json，UI 通过读取该文件展示状态。
"""
import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from apscheduler.events import EVENT_SCHEDULER_STARTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

STATE_FILE = "logs/scheduler_state.json"
PID_FILE = "logs/scheduler.pid"


def default_schedule_config() -> List[Dict[str, Any]]:
    return [
        {"type": "github", "hour": 9, "minute": 0, "enabled": True},
        {"type": "reddit", "hour": 9, "minute": 5, "enabled": False},
        {"type": "laoba_feng", "hour": 9, "minute": 30, "enabled": True},
        {"type": "organize", "hour": 23, "minute": 0, "enabled": True},
    ]


class FeishuScheduler:
    def __init__(self, jobs_config: List[Dict[str, Any]]):
        self.jobs_config = jobs_config
        self.scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        self.state: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "pid": os.getpid(),
            "last_runs": {},
            "next_runs": {},
            "jobs": [],
        }

    def add_jobs(self) -> None:
        for job in self.jobs_config:
            job_type = job.get("type")
            if not job_type:
                continue

            if not job.get("enabled", True):
                logger.info(f"跳过未启用任务: {job_type}")
                self.state["jobs"].append({**job, "registered": False})
                continue

            handler = self._get_handler(job_type)
            if not handler:
                logger.warning(f"未知任务类型: {job_type}")
                self.state["jobs"].append({**job, "registered": False, "error": "未知类型"})
                continue

            hour = int(job.get("hour", 9))
            minute = int(job.get("minute", 0))
            trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Shanghai")
            self.scheduler.add_job(
                handler,
                trigger=trigger,
                id=job_type,
                name=f"{job_type} @ {hour:02d}:{minute:02d}",
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=300,
            )
            self.state["jobs"].append({**job, "registered": True})
            logger.info(f"已注册任务: {job_type} cron({hour:02d}:{minute:02d} +08:00)")

    def _get_handler(self, job_type: str) -> Optional[Callable]:
        return {
            "github": self._run_github,
            "reddit": self._run_reddit,
            "laoba_feng": self._run_laoba_feng,
            "organize": self._run_organize,
        }.get(job_type)

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        for job in self.scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            if next_run:
                self.state["next_runs"][job.id] = next_run.isoformat(timespec="seconds")
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"写入状态文件失败: {e}")

    def _record_run(self, job_type: str, status: str, **extra) -> None:
        self.state["last_runs"][job_type] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            **extra,
        }
        self._save_state()

    def _run_organize(self) -> None:
        logger.info("[scheduler] 触发任务: organize")
        try:
            import argparse
            from main import cmd_organize
            cmd_organize(argparse.Namespace(dry_run=False))
            self._record_run("organize", "success")
        except Exception as e:
            logger.exception("organize 任务失败")
            self._record_run("organize", "error", message=str(e))

    def _run_github(self) -> None:
        logger.info("[scheduler] 触发任务: github")
        try:
            import argparse
            from main import cmd_import_github
            cmd_import_github(argparse.Namespace())
            self._record_run("github", "success")
        except Exception as e:
            logger.exception("github 任务失败")
            self._record_run("github", "error", message=str(e))

    def _run_reddit(self) -> None:
        logger.info("[scheduler] 触发任务: reddit")
        try:
            import argparse
            from main import cmd_import_reddit
            cmd_import_reddit(argparse.Namespace(force=False))
            self._record_run("reddit", "success")
        except Exception as e:
            logger.exception("reddit 任务失败")
            self._record_run("reddit", "error", message=str(e))

    def _run_laoba_feng(self) -> None:
        logger.info("[scheduler] 触发任务: laoba_feng")
        try:
            import argparse
            from main import cmd_import_laoba_feng
            cmd_import_laoba_feng(argparse.Namespace(force=False))
            self._record_run("laoba_feng", "success")
        except Exception as e:
            logger.exception("laoba_feng 任务失败")
            self._record_run("laoba_feng", "error", message=str(e))

    def _write_pid(self) -> None:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

    def _remove_pid(self) -> None:
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass

    def _load_existing_state_if_any(self) -> None:
        """启动时把 logs/scheduler_state.json 里的 last_runs 读进来，
        这样 catch-up 才能判断"今天是否已跑过"。"""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                prev_runs = prev.get("last_runs") or {}
                if isinstance(prev_runs, dict) and prev_runs:
                    self.state["last_runs"] = prev_runs
                    logger.info(f"已加载历史 last_runs: {list(prev_runs.keys())}")
        except Exception as e:
            logger.warning(f"读取历史 state 失败: {e}")

    def _catch_up_missed_today(self) -> None:
        """
        启动时补跑：若今天某 job 的 cron 触发点已过 + 今天从未成功跑过，
        就立刻安排一次性 run_date=now+5s 的触发。
        """
        now = datetime.now()
        today_date = now.date().isoformat()
        existing_last = self.state.get("last_runs") or {}

        for job in self.jobs_config:
            job_type = job.get("type")
            if not job_type or not job.get("enabled", True):
                continue
            handler = self._get_handler(job_type)
            if not handler:
                continue

            hour = int(job.get("hour", 9))
            minute = int(job.get("minute", 0))
            scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # cron 点还没到，走常规调度即可
            if scheduled_today > now:
                continue

            # 今天已经跑过（看 last_runs 日期是不是今天）就跳过
            last = existing_last.get(job_type) or {}
            last_at = str(last.get("at", ""))
            if last_at.startswith(today_date):
                logger.info(f"[catch-up] {job_type} 今天已跑过 ({last_at})，不补跑")
                continue

            # 今天没跑过 + cron 点已过 → 安排一次性补跑
            run_at = now + timedelta(seconds=5)
            logger.info(
                f"[catch-up] {job_type} 今天 {hour:02d}:{minute:02d} 触发点已过，"
                f"当天未跑，安排 {run_at.strftime('%H:%M:%S')} 补跑一次"
            )
            self.scheduler.add_job(
                handler,
                trigger="date",
                run_date=run_at,
                id=f"{job_type}_catchup",
                name=f"{job_type} (补跑今日)",
                replace_existing=True,
                misfire_grace_time=60,
            )

    def start(self) -> None:
        self.add_jobs()
        self._load_existing_state_if_any()
        self._catch_up_missed_today()
        self.state["running"] = True
        self.state["started_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_pid()
        self._save_state()

        # 调度器启动后再写一次状态，把 next_run_time 持久化
        self.scheduler.add_listener(
            lambda _e: self._save_state(),
            EVENT_SCHEDULER_STARTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
        )

        def _shutdown(_signum, _frame):
            logger.info("收到停止信号，关闭调度器")
            self.state["running"] = False
            self._save_state()
            self._remove_pid()
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        logger.info("调度器启动（按 Ctrl+C 停止）")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.state["running"] = False
            self._save_state()
            self._remove_pid()


def read_scheduler_state() -> Dict[str, Any]:
    """读取调度器当前状态（供 UI 调用）"""
    if not os.path.exists(STATE_FILE):
        return {"running": False, "last_runs": {}, "next_runs": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"running": False, "last_runs": {}, "next_runs": {}}

    # 校验 PID 是否仍存活
    pid = state.get("pid")
    if state.get("running") and pid:
        if not _pid_alive(pid):
            state["running"] = False
            state["pid_alive"] = False
        else:
            state["pid_alive"] = True
    return state


def _pid_alive(pid: int) -> bool:
    """跨平台检查进程是否存在"""
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
