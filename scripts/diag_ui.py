"""
UI 后端联通性诊断 — 不开 Electron 窗口，就能逐个调 Python 后端命令，
确认每个按钮点下去会拿到合法结果。

用法:
  python scripts/diag_ui.py                  # 跑全部（含慢任务）
  python scripts/diag_ui.py --fast           # 跳过 import-github / import-reddit / organize
  python scripts/diag_ui.py --only chat      # 只跑对话助手

对应 UI 按钮：
  chat            → 对话助手 打开
  github          → GitHub 立即执行
  github-wiki     → GitHub Wiki（打开上次日报 URL）
  github-log      → GitHub 日志（开 logs/feishu_agent.log）
  reddit          → Reddit 立即执行
  reddit-wiki     → Reddit Wiki
  wiki-preview    → Wiki 整理 预览
  wiki-execute    → Wiki 整理 执行（危险：真移动）
  cleanup-preview → Wiki 清理 预览匹配项
  schedule-status → 调度器状态
"""
import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

PY_CMD = "python" if sys.platform == "win32" else "python3"


def run_cli(args: List[str], timeout: int = 600) -> Dict[str, Any]:
    """调 python main.py ... 返回 {ok, code, stdout, stderr, duration}"""
    started = time.time()
    try:
        proc = subprocess.run(
            [PY_CMD, "main.py", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration": round(time.time() - started, 1),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"timeout {timeout}s", "duration": timeout}


def extract_json(stdout: str) -> Optional[Dict]:
    """从 stdout 里拎最后一个合法 JSON 对象"""
    if not stdout:
        return None
    for line in reversed(stdout.splitlines()):
        t = line.strip()
        if t.startswith("{") and t.endswith("}"):
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                continue
    return None


def file_exists(rel: str) -> bool:
    return os.path.exists(os.path.join(REPO_ROOT, rel))


def read_json(rel: str) -> Dict:
    p = os.path.join(REPO_ROOT, rel)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# =========================================================
# 逐个 test case
# =========================================================

def test_chat(query: str = "GitHub Trending") -> Tuple[bool, str]:
    res = run_cli(["chat", query, "--limit", "3", "--json"], timeout=90)
    if not res["ok"]:
        return False, f"CLI 失败 code={res['code']} {res['stderr'][:120]}"
    data = extract_json(res["stdout"])
    if not data:
        return False, f"没解析到 JSON 输出; stdout 末尾: {res['stdout'][-200:]}"
    if data.get("status") not in ("success", "partial"):
        return False, f"status={data.get('status')}, msg={data.get('message','')[:100]}"
    sources_n = len(data.get("sources", []))
    ans_preview = (data.get("answer") or "")[:60].replace("\n", " ")
    return True, f"status={data['status']} · {sources_n} 个 source · answer={ans_preview}…"


def test_github_wiki() -> Tuple[bool, str]:
    data = read_json("logs/github_last_run.json")
    if not data:
        return False, "logs/github_last_run.json 不存在，先跑 import-github"
    url = data.get("wiki_url") or data.get("urls", [None])[0]
    if not url:
        return False, "last_run 里没有 wiki_url 字段"
    return True, f"wiki_url={url}"


def test_reddit_wiki() -> Tuple[bool, str]:
    data = read_json("logs/reddit_last_run.json")
    if not data:
        return False, "logs/reddit_last_run.json 不存在，先跑 import-reddit"
    url = data.get("wiki_url")
    if not url:
        return False, f"last_run 里没有 wiki_url，status={data.get('status')}, msg={data.get('message','')[:80]}"
    return True, f"wiki_url={url}"


def test_github_log() -> Tuple[bool, str]:
    if not file_exists("logs/feishu_agent.log"):
        return False, "logs/feishu_agent.log 不存在，先跑任何一个命令让日志产生"
    size_kb = round(os.path.getsize(os.path.join(REPO_ROOT, "logs/feishu_agent.log")) / 1024, 1)
    return True, f"日志存在 ({size_kb} KB)"


def test_github() -> Tuple[bool, str]:
    res = run_cli(["import-github"], timeout=900)
    if not res["ok"]:
        return False, f"code={res['code']} stderr={res['stderr'][:150]}"
    data = read_json("logs/github_last_run.json")
    status = data.get("status", "?")
    msg = data.get("message", "")[:80]
    return status in ("success", "partial", "skipped"), f"{res['duration']}s · status={status} · {msg}"


def test_reddit() -> Tuple[bool, str]:
    # 需 HTTP_PROXY，环境里没设的话会失败
    if not (os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")):
        return False, "环境变量未设 HTTP_PROXY，Reddit 在中国大陆需走 VPN"
    res = run_cli(["import-reddit"], timeout=900)
    if not res["ok"]:
        return False, f"code={res['code']} stderr={res['stderr'][:150]}"
    data = read_json("logs/reddit_last_run.json")
    status = data.get("status", "?")
    msg = data.get("message", "")[:80]
    return status in ("success", "partial", "skipped"), f"{res['duration']}s · status={status} · {msg}"


def test_wiki_preview() -> Tuple[bool, str]:
    res = run_cli(["organize", "--dry-run"], timeout=600)
    if not res["ok"]:
        return False, f"code={res['code']} stderr={res['stderr'][:150]}"
    # 报告文件名: logs/organize_report_*.json
    reports = [f for f in os.listdir(os.path.join(REPO_ROOT, "logs")) if f.startswith("organize_report_")]
    if not reports:
        return False, "没生成 organize_report_*.json"
    latest = max(reports)
    report = read_json(f"logs/{latest}")
    return True, f"{res['duration']}s · 扫描 {report.get('total', 0)} 篇 · dry_run={report.get('dry_run', '?')}"


def test_wiki_execute() -> Tuple[bool, str]:
    return False, "跳过（真移动，不在自动测试里做）"


def test_cleanup_preview() -> Tuple[bool, str]:
    # 用一个几乎不会匹到东西的前缀试预览路径
    res = run_cli(["cleanup-wiki", "--prefix", "[自动诊断永不匹配]", "--json"], timeout=60)
    if not res["ok"]:
        return False, f"code={res['code']} stderr={res['stderr'][:150]}"
    data = extract_json(res["stdout"])
    if not data:
        return False, f"没 JSON 输出; stdout={res['stdout'][-200:]}"
    # 预期 matched=[] 且 dry_run=True
    if data.get("dry_run") is True and len(data.get("matched", [])) == 0:
        return True, "dry-run 路径通畅（0 个匹配，符合预期）"
    return False, f"dry_run={data.get('dry_run')} matched={len(data.get('matched',[]))}"


def test_schedule_status() -> Tuple[bool, str]:
    res = run_cli(["schedule-status"], timeout=30)
    if not res["ok"]:
        return False, f"code={res['code']} stderr={res['stderr'][:150]}"
    data = extract_json(res["stdout"])
    if not data:
        return False, "没 JSON 输出"
    running = data.get("running", False)
    jobs_n = len(data.get("jobs", []))
    return True, f"running={running} · jobs_known={jobs_n}"


# =========================================================
# 主入口
# =========================================================

CASES = [
    # name, description, func, is_slow
    ("schedule-status",  "调度器状态",              test_schedule_status,  False),
    ("chat",             "对话助手（豆包调用）",   test_chat,             False),
    ("github-wiki",      "GitHub Wiki URL",         test_github_wiki,      False),
    ("reddit-wiki",      "Reddit Wiki URL",         test_reddit_wiki,      False),
    ("github-log",       "日志文件存在性",          test_github_log,       False),
    ("cleanup-preview",  "Wiki 清理预览 (空匹配)",  test_cleanup_preview,  False),
    ("github",           "GitHub 抓取真跑",         test_github,           True),
    ("reddit",           "Reddit 抓取真跑",         test_reddit,           True),
    ("wiki-preview",     "Wiki 整理预览 (dry-run)", test_wiki_preview,     True),
    ("wiki-execute",     "Wiki 整理执行 (跳过)",    test_wiki_execute,     True),
]


def main():
    parser = argparse.ArgumentParser(description="UI 后端联通性诊断")
    parser.add_argument("--fast", action="store_true", help="跳过慢任务")
    parser.add_argument("--only", help="逗号分隔，只跑这些：name1,name2")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None

    print("=" * 70)
    print("UI 后端联通性诊断")
    print("=" * 70)
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or "（未设）"
    print(f"PY_CMD:     {PY_CMD}")
    print(f"REPO_ROOT:  {REPO_ROOT}")
    print(f"HTTP_PROXY: {proxy}")
    print()

    results = []
    for name, desc, func, is_slow in CASES:
        if only and name not in only:
            continue
        if args.fast and is_slow:
            print(f"  ⏭  {name:20} {desc:26} [跳过 --fast]")
            continue

        print(f"  ⏳ {name:20} {desc:26} ...", end="", flush=True)
        try:
            ok, msg = func()
        except Exception as e:
            ok, msg = False, f"异常: {e}"
        mark = "✓" if ok else "✗"
        print(f"\r  {mark} {name:20} {desc:26} {msg}")
        results.append((name, ok, msg))

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("=" * 70)
    print(f"总计 {passed}/{total} 通过")
    if passed < total:
        print("失败项：")
        for name, ok, msg in results:
            if not ok:
                print(f"  ✗ {name}: {msg}")
    print("=" * 70)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
