"""
飞书智能管理 Agent — 主入口
用法:
  python main.py organize          # 扫描两个账号文档 → AI 分类 → 整理到个人 Wiki
  python main.py organize --dry-run  # 仅预览，不实际移动
  python main.py import-github     # 将 GitHub 仓库导入飞书
  python main.py manage email      # 邮箱管理
  python main.py manage messages   # IM 消息管理
  python main.py manage calendar   # 日历管理
  python main.py manage contacts   # 联系人管理
  python main.py schedule          # 启动定时任务调度器（守护进程）
  python main.py schedule-status   # 查询调度器状态（输出 JSON）
"""
import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/feishu_agent.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config_loader import config_loader
from src.utils.feishu_client import FeishuClientFactory


def cmd_organize(args):
    """扫描文档 → AI 分类 → 整理到个人 Wiki"""
    from src.organizer.doc_scanner import DocScanner
    from src.organizer.ai_categorizer import AICategorizer
    from src.organizer.doc_organizer import DocOrganizer

    creds = config_loader.load_credentials()
    categories_cfg = config_loader.load_categories()
    factory = FeishuClientFactory(creds["accounts"])

    personal_cfg = config_loader.get_account_config("personal")
    wiki_space_id = personal_cfg.get("wiki_space_id", "")
    if not wiki_space_id:
        logger.error("请在 credentials.json 中配置 accounts.personal.wiki_space_id")
        sys.exit(1)

    all_docs = []

    # 扫描个人账号
    logger.info("=== 扫描个人账号文档 ===")
    personal_client = factory.get_client("personal")
    personal_scanner = DocScanner(personal_client, "personal")
    personal_docs = personal_scanner.scan_wiki(wiki_space_id)
    all_docs.extend(personal_docs)
    logger.info(f"个人账号: {len(personal_docs)} 篇文档")

    # 扫描企业账号（如已配置）
    enterprise_cfg = creds["accounts"].get("enterprise", {})
    if enterprise_cfg.get("app_id") and not enterprise_cfg["app_id"].startswith("cli_enterprise"):
        logger.info("=== 扫描企业账号文档 ===")
        try:
            enterprise_client = factory.get_client("enterprise")
            enterprise_scanner = DocScanner(enterprise_client, "enterprise")
            enterprise_docs = enterprise_scanner.scan_wiki()
            all_docs.extend(enterprise_docs)
            logger.info(f"企业账号: {len(enterprise_docs)} 篇文档")
        except Exception as e:
            logger.warning(f"企业账号扫描失败（跳过）: {e}")
    else:
        logger.info("企业账号未配置，跳过")

    if not all_docs:
        logger.info("未扫描到任何文档，退出")
        return

    # AI 自动分类
    logger.info(f"=== AI 分类（共 {len(all_docs)} 篇）===")
    categorizer = AICategorizer(config_loader.get_ai_config(), categories_cfg)
    all_docs = categorizer.categorize_batch(all_docs)

    # 打印分类预览
    from collections import Counter
    cat_counts = Counter(d["category"] for d in all_docs)
    print("\n分类预览:")
    for cat, cnt in cat_counts.most_common():
        print(f"  {cat}: {cnt} 篇")

    if args.dry_run:
        print("\n[DRY-RUN 模式] 以上为预览，未实际移动任何文档。")
        print("去掉 --dry-run 参数后重新运行以执行实际整理。")
        return

    # 整理文档
    logger.info("=== 整理文档到个人 Wiki ===")
    organizer = DocOrganizer(personal_client, wiki_space_id, dry_run=False)
    report = organizer.organize(all_docs, categories_cfg)
    logger.info("文档整理完成！")


def _validate_github_config(github_cfg: dict, wiki_space_id: str) -> Optional[str]:
    """返回 None 表示配置有效，否则返回错误信息"""
    token = github_cfg.get("token", "")
    if not token or token.startswith("ghp_your_"):
        return "GitHub token 未配置（占位值 ghp_your_...）。请在 config/credentials.json 填入真实 token"
    if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
        return "飞书 Wiki space_id 未配置。请在 config/credentials.json 的 accounts.personal.wiki_space_id 填入"
    trending_enabled = github_cfg.get("trending", {}).get("enabled", False)
    has_static = github_cfg.get("repo_list") or github_cfg.get("search_topics")
    if not trending_enabled and not has_static:
        return "GitHub 任务配置为空：trending.enabled / repo_list / search_topics 至少启用一个"
    return None


def _load_import_index() -> Dict[str, Dict[str, Any]]:
    """读取已导入索引（去重用）。格式: {repo_full_name: {"wiki_url": ..., "imported_at": ...}}"""
    path = "logs/github_imported.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_import_index(index: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs("logs", exist_ok=True)
    with open("logs/github_imported.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _write_run_stats(stats: Dict[str, Any]) -> None:
    """写入最近一次运行统计，供 UI 展示"""
    os.makedirs("logs", exist_ok=True)
    with open("logs/github_last_run.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def _load_trending_cache(path: str) -> List[Dict[str, Any]]:
    """读取当日 trending 缓存（已摘要成功的仓库 + 失败占位），供续跑"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_trending_cache(path: str, items: List[Dict[str, Any]]) -> None:
    """增量保存 trending 缓存"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"保存 trending 缓存失败: {e}")


def cmd_import_github(args):
    """
    GitHub Trending 日报：从 /trending 抓热门仓库 → 豆包 AI 中文摘要 →
    按日期汇总为一页写入飞书 Wiki 的 github专区 子节点。

    老的 repo_list / search_topics 流程保留为兼容模式（若 trending.enabled=false 走老路）。
    """
    from datetime import datetime

    from src.importers.github_importer import GitHubImporter
    from src.importers.feishu_doc_writer import FeishuDocWriter

    run_stats: Dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "status": "error",
        "mode": "",
        "fetched": 0,
        "summarized": 0,
        "ai_failed": 0,
        "wiki_url": "",
        "message": "",
    }

    try:
        creds = config_loader.load_credentials()
        github_cfg = config_loader.get_github_config()
        wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")

        err = _validate_github_config(github_cfg, wiki_space_id)
        if err:
            logger.error(err)
            run_stats["message"] = err
            _write_run_stats(run_stats)
            return

        factory = FeishuClientFactory(creds["accounts"])
        personal_client = factory.get_client("personal")
        importer = GitHubImporter(token=github_cfg.get("token", ""))
        writer = FeishuDocWriter(personal_client, wiki_space_id)

        trending_cfg = github_cfg.get("trending", {})
        force = getattr(args, "force", False)

        if trending_cfg.get("enabled", False):
            run_stats["mode"] = "trending"
            _run_trending_pipeline(
                github_cfg=github_cfg,
                trending_cfg=trending_cfg,
                importer=importer,
                writer=writer,
                run_stats=run_stats,
                force=force,
            )
        else:
            run_stats["mode"] = "legacy"
            _run_legacy_pipeline(
                github_cfg=github_cfg,
                importer=importer,
                writer=writer,
                run_stats=run_stats,
                force=force,
            )

        _write_run_stats(run_stats)

    except Exception as e:
        logger.exception("import-github 异常")
        run_stats["message"] = f"异常终止: {e}"
        _write_run_stats(run_stats)
        raise


def _run_trending_pipeline(
    github_cfg: Dict, trending_cfg: Dict, importer, writer, run_stats: Dict, force: bool
) -> None:
    """新流程：Trending → 抓 README → 豆包摘要 → 写入飞书日报页"""
    from datetime import datetime as _dt

    from src.importers.github_trending import GitHubTrending
    from src.importers.ai_summarizer import AISummarizer

    ai_cfg = config_loader.get_ai_config()
    summarizer = AISummarizer(ai_cfg)
    if not summarizer.configured():
        msg = "豆包 AI 未配置（api_key / model / base_url 任一缺失），trending 模式需要 AI"
        logger.error(msg)
        run_stats["message"] = msg
        return

    date_str = _dt.now().strftime("%Y-%m-%d")
    parent_folder = github_cfg.get("parent_folder", "github专区")
    # 不再做文件夹级早退：write_daily_trending_report 会做 per-page 去重，
    # 已存在的子页面自动跳过，失败的子页面下次会补建

    # 1. 抓 Trending
    trending = GitHubTrending()
    repos_meta = trending.fetch(
        period=trending_cfg.get("period", "daily"),
        language=trending_cfg.get("language", ""),
        limit=int(trending_cfg.get("limit", 10)),
    )
    run_stats["fetched"] = len(repos_meta)

    if not repos_meta:
        msg = "Trending 列表为空（可能网络受限或 GitHub 限频）"
        logger.warning(msg)
        run_stats["message"] = msg
        return

    # 2. 对每个仓库：抓 README + 豆包摘要，每次增量持久化到缓存文件
    cache_path = f"logs/trending_cache_{date_str}.json"
    items_with_summary: List[Dict[str, Any]] = _load_trending_cache(cache_path)
    if items_with_summary and not force:
        # 续跑：从缓存里恢复已摘要成功的仓库
        done_names = {
            it["repo"].get("full_name") for it in items_with_summary if it.get("summary_ok")
        }
        logger.info(f"从缓存恢复 {len(done_names)} 个已摘要仓库: {cache_path}")
    else:
        items_with_summary = []
        done_names = set()

    for i, meta in enumerate(repos_meta, 1):
        full_name = meta.get("full_name", "")
        if full_name in done_names:
            logger.info(f"[{i}/{len(repos_meta)}] 跳过（缓存已有）: {full_name}")
            continue
        logger.info(f"[{i}/{len(repos_meta)}] 处理 {full_name}")

        # 2a. 抓 README（复用 GitHubImporter）
        detail = importer.fetch_repo(full_name)
        readme = detail.get("readme", "") if detail else ""

        # 2b. 豆包摘要
        summary = summarizer.summarize_repo(
            full_name=full_name,
            description=meta.get("description", ""),
            language=meta.get("language", ""),
            stars_total=meta.get("stars_total", 0),
            stars_today=meta.get("stars_today", 0),
            readme=readme,
        )
        summary_ok = summary is not None
        if not summary:
            summary = {
                "one_liner": meta.get("description", "") or "（AI 摘要失败）",
                "detail": "AI 摘要失败，请查看原仓库 README。",
            }

        items_with_summary.append({
            "repo": meta,
            "summary": summary,
            "summary_ok": summary_ok,
        })
        # 增量保存
        _save_trending_cache(cache_path, items_with_summary)

    run_stats["summarized"] = sum(1 for it in items_with_summary if it.get("summary_ok"))
    run_stats["ai_failed"] = len(items_with_summary) - run_stats["summarized"]

    # 3. 写飞书日报：文件夹 + N 个独立子页
    result = writer.write_daily_trending_report(
        items_with_summary=items_with_summary,
        parent_folder_title=parent_folder,
        date_str=date_str,
    )

    if result:
        run_stats["wiki_url"] = result["folder_url"]
        run_stats["wiki_folder_token"] = result["folder_token"]
        run_stats["pages_created"] = result["created_count"]
        run_stats["pages_skipped"] = result["skipped_count"]
        run_stats["repo_pages"] = result["repo_pages"]

        status_parts = []
        if run_stats["ai_failed"] == 0 and result["created_count"] + result["skipped_count"] == len(items_with_summary):
            run_stats["status"] = "success"
        else:
            run_stats["status"] = "partial"

        run_stats["message"] = (
            f"抓取 {run_stats['fetched']} · 摘要 {run_stats['summarized']}"
            f" · AI失败 {run_stats['ai_failed']}"
            f" · 新建页 {result['created_count']}"
            f" · 跳过 {result['skipped_count']}"
        )
        print(f"\n✅ 日报文件夹: {result['folder_url']}")
        print(f"   {run_stats['message']}")
        print(f"\n子页面:")
        for p in result["repo_pages"]:
            marker = "🆕" if p["created"] else "  "
            print(f"   {marker} {p['title']}")
            if p["url"]:
                print(f"        {p['url']}")
    else:
        run_stats["message"] = "日报写入失败，可能父节点不存在或 Wiki 权限问题"
        logger.error(run_stats["message"])


def _run_legacy_pipeline(
    github_cfg: Dict, importer, writer, run_stats: Dict, force: bool
) -> None:
    """老流程：repo_list + search_topics → 每个仓库一个 Wiki 页"""
    from datetime import datetime as _dt

    index = _load_import_index()
    all_repos: List[Dict[str, Any]] = []

    repo_list = github_cfg.get("repo_list", [])
    if repo_list:
        logger.info(f"导入指定仓库列表: {repo_list}")
        all_repos.extend(importer.import_repo_list(repo_list))

    topics = github_cfg.get("search_topics", [])
    if topics:
        logger.info(f"搜索主题仓库: {topics}")
        all_repos.extend(importer.search_by_topics(topics, per_topic=5))

    run_stats["fetched"] = len(all_repos)

    if not all_repos:
        run_stats["message"] = "未获取到任何仓库"
        logger.warning(run_stats["message"])
        return

    to_import = []
    run_stats["skipped_dup"] = 0
    for repo in all_repos:
        full_name = repo.get("full_name")
        if not full_name:
            continue
        if not force and full_name in index:
            logger.info(f"跳过已导入: {full_name}")
            run_stats["skipped_dup"] += 1
            continue
        to_import.append(repo)

    run_stats["imported"] = 0
    run_stats["failed"] = 0
    run_stats["urls"] = []
    for repo in to_import:
        url = writer.write_github_repo(repo)
        if url:
            run_stats["imported"] += 1
            run_stats["urls"].append(url)
            index[repo["full_name"]] = {
                "wiki_url": url,
                "imported_at": _dt.now().isoformat(timespec="seconds"),
            }
            _save_import_index(index)
        else:
            run_stats["failed"] += 1

    run_stats["status"] = "success" if run_stats["failed"] == 0 else "partial"
    run_stats["message"] = (
        f"新增 {run_stats['imported']} · 跳过 {run_stats['skipped_dup']}"
        f" · 失败 {run_stats['failed']}"
    )
    print(f"\n✅ (legacy 模式) {run_stats['message']}")
    for url in run_stats["urls"]:
        print(f"  {url}")


def cmd_manage(args):
    """管理飞书各类功能（邮件/消息/日历/联系人）"""
    creds = config_loader.load_credentials()
    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")

    if args.resource == "email":
        _manage_email(client)
    elif args.resource == "messages":
        _manage_messages(client, args)
    elif args.resource == "calendar":
        _manage_calendar(client)
    elif args.resource == "contacts":
        _manage_contacts(client, args)
    else:
        print(f"未知资源类型: {args.resource}")
        print("可用: email | messages | calendar | contacts")


def _manage_email(client):
    from src.managers.email_manager import EmailManager
    mgr = EmailManager(client)
    mails = mgr.list_mails(limit=10)
    if not mails:
        print("收件箱为空（或无权限）")
        return
    print(f"\n最近 {len(mails)} 封邮件:")
    for i, m in enumerate(mails, 1):
        status = "" if m.get("is_read") else "【未读】"
        print(f"  {i}. {status}{m.get('subject', '(无主题)')} — {m.get('from', '')} @ {m.get('date', '')}")
    print("\n提示: 使用 message_id 调用 mgr.reply_mail() / mgr.delete_mail() 操作邮件")


def _manage_messages(client, args):
    from src.managers.message_manager import MessageManager
    mgr = MessageManager(client)
    chat_id = getattr(args, "chat_id", None)
    if not chat_id:
        print("请通过 --chat-id <chat_id> 指定群聊 ID")
        return
    msgs = mgr.list_messages(chat_id, limit=10)
    print(f"\n群聊 {chat_id} 最近 {len(msgs)} 条消息:")
    for m in msgs:
        print(f"  [{m.get('create_time', '')}] {m.get('content', '')[:80]}")


def _manage_calendar(client):
    from src.managers.calendar_manager import CalendarManager
    mgr = CalendarManager(client)
    events = mgr.list_events(days_ahead=7)
    if not events:
        print("未来 7 天无日历事件（或无权限）")
        return
    print(f"\n未来 7 天日历事件 ({len(events)} 个):")
    for e in events:
        print(f"  [{e.get('start_time', '')}] {e.get('summary', '')} @ {e.get('location', '')}")


def _manage_contacts(client, args):
    from src.managers.contact_manager import ContactManager
    mgr = ContactManager(client)
    query = getattr(args, "query", None)
    if query:
        results = mgr.search_contact(query)
        print(f"\n搜索 '{query}' 结果 ({len(results)} 人):")
    else:
        results = mgr.list_contacts(limit=20)
        print(f"\n联系人列表 ({len(results)} 人):")
    for c in results:
        print(f"  {c.get('name', '')} — {c.get('email', '')} {c.get('job_title', '')}")


def cmd_schedule(args):
    """启动定时任务调度器（前台守护进程）"""
    from src.scheduler import FeishuScheduler, default_schedule_config

    try:
        creds = config_loader.load_credentials()
    except FileNotFoundError:
        creds = {}

    schedule_cfg = creds.get("schedule", {})
    jobs_config = schedule_cfg.get("jobs", default_schedule_config())
    if not jobs_config:
        logger.warning("调度器任务列表为空，使用内置默认配置")
        jobs_config = default_schedule_config()

    scheduler = FeishuScheduler(jobs_config)
    scheduler.start()


def cmd_schedule_status(args):
    """输出调度器当前状态 JSON（供 UI 调用）"""
    from src.scheduler import read_scheduler_state
    state = read_scheduler_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))


def main():
    os.makedirs("logs", exist_ok=True)
    parser = argparse.ArgumentParser(description="飞书智能管理 Agent")
    subparsers = parser.add_subparsers(dest="command")

    # organize 子命令
    p_organize = subparsers.add_parser("organize", help="扫描文档并整理到个人 Wiki")
    p_organize.add_argument("--dry-run", action="store_true", help="仅预览，不实际移动文档")

    # import-github 子命令
    p_import_github = subparsers.add_parser("import-github", help="将 GitHub 仓库导入飞书")
    p_import_github.add_argument("--force", action="store_true", help="忽略去重索引，强制重新导入所有仓库")

    # manage 子命令
    p_manage = subparsers.add_parser("manage", help="管理飞书资源")
    p_manage.add_argument("resource", choices=["email", "messages", "calendar", "contacts"])
    p_manage.add_argument("--chat-id", dest="chat_id", help="IM 群聊 ID（管理消息时必填）")
    p_manage.add_argument("--query", help="搜索关键词（管理联系人时可用）")

    # schedule 子命令（守护进程）
    subparsers.add_parser("schedule", help="启动定时任务调度器")
    subparsers.add_parser("schedule-status", help="查询调度器状态（输出 JSON）")

    args = parser.parse_args()

    if args.command == "organize":
        cmd_organize(args)
    elif args.command == "import-github":
        cmd_import_github(args)
    elif args.command == "manage":
        cmd_manage(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "schedule-status":
        cmd_schedule_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
