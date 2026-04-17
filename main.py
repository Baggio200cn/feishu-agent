"""
飞书智能管理 Agent — 主入口
用法:
  python main.py organize          # 扫描两个账号文档 → AI 分类 → 整理到个人 Wiki
  python main.py organize --dry-run  # 仅预览，不实际移动
  python main.py import-github --repo owner/repo  # 将指定 GitHub 仓库导入飞书
  python main.py daily-reddit [--limit N]          # Reddit AI_Agents 日报
  python main.py daily-github [--since daily] [--top-n 15]  # GitHub Trending 日报
  python main.py search --keyword 关键词           # 搜索 Wiki 文档
  python main.py list-spaces                       # 列出所有 Wiki 知识空间
  python main.py daily-report                      # 每日摘要报告
  python main.py manage email      # 邮箱管理
  python main.py manage messages   # IM 消息管理
  python main.py manage calendar   # 日历管理
  python main.py manage contacts   # 联系人管理
"""
import argparse
import json
import logging
import os
import sys

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


def cmd_import_github(args):
    """将 GitHub 仓库内容导入飞书"""
    from src.importers.github_importer import GitHubImporter
    from src.importers.feishu_doc_writer import FeishuDocWriter

    creds = config_loader.load_credentials()
    github_cfg = config_loader.get_github_config()
    factory = FeishuClientFactory(creds["accounts"])
    personal_client = factory.get_client("personal")
    wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")

    importer = GitHubImporter(token=github_cfg.get("token", ""))
    writer = FeishuDocWriter(personal_client, wiki_space_id)

    all_repos = []

    # 单个仓库（来自 --repo 参数或 chat agent）
    if getattr(args, "repo", None):
        logger.info(f"导入指定仓库: {args.repo}")
        data = importer.fetch_repo(args.repo)
        if data:
            all_repos.append(data)
        else:
            print(f"❌ 无法获取仓库: {args.repo}")
            return
    else:
        # 导入配置文件中的仓库列表
        repo_list = github_cfg.get("repo_list", [])
        if repo_list:
            logger.info(f"导入指定仓库列表: {repo_list}")
            all_repos.extend(importer.import_repo_list(repo_list))

        # 按主题搜索
        topics = github_cfg.get("search_topics", [])
        if topics:
            logger.info(f"搜索主题仓库: {topics}")
            all_repos.extend(importer.search_by_topics(topics, per_topic=5))

    if not all_repos:
        logger.info("未获取到任何仓库，请检查 credentials.json 中的 github 配置")
        return

    logger.info(f"共获取 {len(all_repos)} 个仓库，开始写入飞书...")
    urls = writer.write_github_repos_batch(all_repos)

    print(f"\n✅ 已成功导入 {len(urls)} 个仓库到飞书 Wiki:")
    for url in urls:
        print(f"  {url}")


def cmd_daily_reddit(args):
    """抓取 Reddit r/AI_Agents 日报并写入飞书"""
    creds = config_loader.load_credentials()
    factory = FeishuClientFactory(creds["accounts"])
    personal_client = factory.get_client("personal")
    wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")

    from src.scrapers.reddit_scraper import RedditScraper
    from src.scrapers.daily_writer import DailyWriter

    ai_cfg = config_loader.get_ai_config()
    scraper = RedditScraper(
        api_key=ai_cfg.get("api_key", ""),
        model=ai_cfg.get("model", "doubao-seed-2-0-code-preview-260215"),
        base_url=ai_cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    limit = getattr(args, "limit", 20) or 20
    logger.info(f"抓取 Reddit r/AI_Agents，每种排序 {limit} 篇...")
    posts = scraper.fetch_posts(limit=limit)
    if not posts:
        print("❌ 未抓取到任何帖子（RSS 可能暂时不可用）")
        return

    logger.info(f"抓取到 {len(posts)} 篇帖子，开始翻译...")
    posts = scraper.translate_posts(posts)

    writer = DailyWriter(personal_client, wiki_space_id)
    report = writer.write_reddit_posts(posts)
    print(
        f"\n✅ Reddit 日报写入完成: 成功 {report['written']} 篇，"
        f"失败 {report['failed']} 篇 → [{report['folder']}]"
    )


def cmd_daily_github(args):
    """抓取 GitHub Trending 日报并写入飞书"""
    creds = config_loader.load_credentials()
    factory = FeishuClientFactory(creds["accounts"])
    personal_client = factory.get_client("personal")
    wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")

    from src.scrapers.github_trending_scraper import GitHubTrendingScraper
    from src.scrapers.daily_writer import DailyWriter

    ai_cfg = config_loader.get_ai_config()
    github_cfg = config_loader.get_github_config()
    scraper = GitHubTrendingScraper(
        api_key=ai_cfg.get("api_key", ""),
        model=ai_cfg.get("model", "doubao-seed-2-0-code-preview-260215"),
        base_url=ai_cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"),
        github_token=github_cfg.get("token", ""),
    )
    since = getattr(args, "since", "daily") or "daily"
    top_n = int(getattr(args, "top_n", 15) or 15)

    logger.info(f"抓取 GitHub Trending ({since}, top {top_n})...")
    repos = scraper.fetch_trending(since=since, top_n=top_n)
    if not repos:
        print("❌ 未抓取到任何仓库")
        return

    logger.info("生成中文摘要...")
    repos = scraper.generate_summaries(repos)

    writer = DailyWriter(personal_client, wiki_space_id)
    report = writer.write_github_trending(repos)
    print(
        f"\n✅ GitHub Trending 日报写入完成: {report['written']} 个仓库 → [{report['folder']}]"
    )


def cmd_search(args):
    """按关键词搜索 Wiki 文档（从本地 SQLite 索引）"""
    import sqlite3
    keyword = (getattr(args, "keyword", "") or "").strip()
    if not keyword:
        print("请提供搜索关键词: --keyword <词>")
        return

    db_path = "data/agent.db"
    if not os.path.exists(db_path):
        print("本地索引不存在，请先运行 organize 命令建立索引。")
        return

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT title, category, node_token FROM wiki_docs WHERE title LIKE ? LIMIT 50",
        (f"%{keyword}%",),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"未找到包含「{keyword}」的文档。")
        return

    from collections import defaultdict
    by_cat = defaultdict(list)
    for title, cat, token in rows:
        by_cat[cat or "未分类"].append(title)

    print(f"\n搜索「{keyword}」找到 {len(rows)} 篇文档:\n")
    for cat, titles in sorted(by_cat.items()):
        print(f"  [{cat}] ({len(titles)} 篇)")
        for t in titles[:10]:
            print(f"    · {t}")
        if len(titles) > 10:
            print(f"    ... 共 {len(titles)} 篇")


def cmd_list_spaces(args):
    """列出飞书所有 Wiki 知识空间"""
    creds = config_loader.load_credentials()
    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")

    try:
        from lark_oapi.api.wiki.v2 import ListSpaceRequest
        req = ListSpaceRequest.builder().page_size(50).build()
        resp = client.wiki.v2.space.list(req)
        if not resp.success():
            print(f"获取失败: {resp.msg}")
            return
        spaces = resp.data.items or []
        print(f"\n共 {len(spaces)} 个 Wiki 知识空间:")
        for sp in spaces:
            print(f"  space_id={sp.space_id}  名称={sp.name}")
    except Exception as e:
        print(f"❌ 列出知识空间失败: {e}")


def cmd_daily_report(args):
    """生成每日摘要报告"""
    import sqlite3
    from collections import Counter

    report_lines = [f"📊 每日摘要报告\n"]

    # Wiki 文档统计
    db_path = "data/agent.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT category FROM wiki_docs").fetchall()
        conn.close()
        if rows:
            cat_counts = Counter(r[0] or "未分类" for r in rows)
            report_lines.append(f"📚 Wiki 文档总计: {len(rows)} 篇")
            for cat, cnt in cat_counts.most_common(10):
                report_lines.append(f"  · {cat}: {cnt} 篇")
        else:
            report_lines.append("📚 Wiki 索引暂无数据（请先运行 organize）")
    else:
        report_lines.append("📚 Wiki 索引不存在（请先运行 organize）")

    # 近期日历事件
    try:
        creds = config_loader.load_credentials()
        factory = FeishuClientFactory(creds["accounts"])
        client = factory.get_client("personal")
        from src.managers.calendar_manager import CalendarManager
        events = CalendarManager(client).list_events(days_ahead=7)
        report_lines.append(f"\n📅 未来 7 天日历事件: {len(events)} 个")
        for e in events[:5]:
            report_lines.append(f"  · [{e.get('start_time', '')}] {e.get('summary', '')}")
    except Exception as e:
        report_lines.append(f"\n📅 日历获取失败: {e}")

    # 最新邮件
    try:
        from src.managers.email_manager import EmailManager
        mails = EmailManager(client).list_mails(limit=5)
        report_lines.append(f"\n📧 最新邮件: {len(mails)} 封")
        for m in mails:
            status = "" if m.get("is_read") else "【未读】"
            report_lines.append(f"  · {status}{m.get('subject', '(无主题)')} — {m.get('from', '')}")
    except Exception as e:
        report_lines.append(f"\n📧 邮件获取失败: {e}")

    print("\n".join(report_lines))


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


def main():
    os.makedirs("logs", exist_ok=True)
    parser = argparse.ArgumentParser(description="飞书智能管理 Agent")
    subparsers = parser.add_subparsers(dest="command")

    # organize 子命令
    p_organize = subparsers.add_parser("organize", help="扫描文档并整理到个人 Wiki")
    p_organize.add_argument("--dry-run", action="store_true", help="仅预览，不实际移动文档")

    # import-github 子命令
    p_import = subparsers.add_parser("import-github", help="将 GitHub 仓库导入飞书")
    p_import.add_argument("--repo", help="仓库名，格式 owner/repo")

    # daily-reddit 子命令
    p_reddit = subparsers.add_parser("daily-reddit", help="Reddit AI_Agents 日报写入飞书")
    p_reddit.add_argument("--limit", type=int, default=20, help="每种排序各抓取多少篇")

    # daily-github 子命令
    p_github = subparsers.add_parser("daily-github", help="GitHub Trending 日报写入飞书")
    p_github.add_argument("--since", default="daily", choices=["daily", "weekly", "monthly"])
    p_github.add_argument("--top-n", type=int, default=15, dest="top_n")

    # search 子命令
    p_search = subparsers.add_parser("search", help="按关键词搜索 Wiki 文档")
    p_search.add_argument("--keyword", required=True, help="搜索关键词")

    # list-spaces 子命令
    subparsers.add_parser("list-spaces", help="列出所有 Wiki 知识空间")

    # daily-report 子命令
    subparsers.add_parser("daily-report", help="生成每日摘要报告")

    # manage 子命令
    p_manage = subparsers.add_parser("manage", help="管理飞书资源")
    p_manage.add_argument("resource", choices=["email", "messages", "calendar", "contacts"])
    p_manage.add_argument("--chat-id", dest="chat_id", help="IM 群聊 ID（管理消息时必填）")
    p_manage.add_argument("--query", help="搜索关键词（管理联系人时可用）")

    args = parser.parse_args()

    if args.command == "organize":
        cmd_organize(args)
    elif args.command == "import-github":
        cmd_import_github(args)
    elif args.command == "daily-reddit":
        cmd_daily_reddit(args)
    elif args.command == "daily-github":
        cmd_daily_github(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "list-spaces":
        cmd_list_spaces(args)
    elif args.command == "daily-report":
        cmd_daily_report(args)
    elif args.command == "manage":
        cmd_manage(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
