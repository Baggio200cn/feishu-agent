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
    if not github_cfg.get("repo_list") and not github_cfg.get("search_topics"):
        return "GitHub 任务列表为空：repo_list 和 search_topics 都没填"
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


def cmd_import_github(args):
    """将 GitHub 仓库内容导入飞书"""
    from datetime import datetime

    from src.importers.github_importer import GitHubImporter
    from src.importers.feishu_doc_writer import FeishuDocWriter

    run_stats: Dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "status": "error",
        "imported": 0,
        "skipped_dup": 0,
        "fetched": 0,
        "failed": 0,
        "urls": [],
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

        index = _load_import_index()
        force_reimport = getattr(args, "force", False)

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
            msg = "未获取到任何仓库（可能被限频或配置有误）"
            logger.warning(msg)
            run_stats["message"] = msg
            _write_run_stats(run_stats)
            return

        # 去重
        to_import = []
        for repo in all_repos:
            full_name = repo.get("full_name")
            if not full_name:
                continue
            if not force_reimport and full_name in index:
                logger.info(f"跳过已导入: {full_name} → {index[full_name].get('wiki_url')}")
                run_stats["skipped_dup"] += 1
                continue
            to_import.append(repo)

        logger.info(
            f"共获取 {len(all_repos)} 个仓库，跳过已导入 {run_stats['skipped_dup']} 个，"
            f"即将写入 {len(to_import)} 个"
        )

        from datetime import datetime as _dt
        for repo in to_import:
            url = writer.write_github_repo(repo)
            if url:
                run_stats["imported"] += 1
                run_stats["urls"].append(url)
                index[repo["full_name"]] = {
                    "wiki_url": url,
                    "imported_at": _dt.now().isoformat(timespec="seconds"),
                }
                _save_import_index(index)  # 增量保存，防止中途崩溃丢失
            else:
                run_stats["failed"] += 1

        run_stats["status"] = "success" if run_stats["failed"] == 0 else "partial"
        run_stats["message"] = (
            f"新增 {run_stats['imported']} · 跳过重复 {run_stats['skipped_dup']}"
            f" · 失败 {run_stats['failed']}"
        )
        if importer.rate_limit_remaining is not None:
            run_stats["rate_limit_remaining"] = importer.rate_limit_remaining

        _write_run_stats(run_stats)

        print(f"\n✅ 本次导入结果：{run_stats['message']}")
        for url in run_stats["urls"]:
            print(f"  {url}")

    except Exception as e:
        logger.exception("import-github 异常")
        run_stats["message"] = f"异常终止: {e}"
        _write_run_stats(run_stats)
        raise


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
