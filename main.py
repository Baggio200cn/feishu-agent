"""
飞书智能管理 Agent — 主入口
用法:
  python main.py list-spaces          # 列出所有 Wiki 空间及其 space_id（首次使用先跑这个）
  python main.py organize             # 扫描文档 → AI 分类 → 整理到个人 Wiki
  python main.py organize --dry-run   # 仅预览，不实际移动
  python main.py import-github        # 将 GitHub 仓库导入飞书
  python main.py daily-reddit         # 抓取 r/AI_Agents 今日帖子 → 翻译 → 写入「agent专区」
  python main.py daily-github         # 抓取 GitHub Trending → 摘要 → 写入「github专区」
  python main.py manage email         # 邮箱管理
  python main.py manage messages      # IM 消息管理
  python main.py manage calendar      # 日历管理
  python main.py manage contacts      # 联系人管理
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

    all_docs = []

    # 扫描个人账号 — 若 wiki_space_id 不是纯数字则自动发现所有空间
    logger.info("=== 扫描个人账号文档 ===")
    personal_client = factory.get_client("personal")
    personal_scanner = DocScanner(personal_client, "personal")
    scan_space_id = wiki_space_id if wiki_space_id.isdigit() else None
    if not scan_space_id:
        logger.info("wiki_space_id 未配置或非数字，自动发现所有 Wiki 空间...")
    organizer = DocOrganizer(personal_client, wiki_space_id, dry_run=args.dry_run)
    personal_docs = personal_scanner.scan_wiki(
        scan_space_id,
        exclude_tokens=organizer.get_folder_tokens(),
    )
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

    # 整理文档（organizer 已在扫描阶段初始化）
    logger.info("=== 整理文档到个人 Wiki ===")
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

    # 命令行直接指定单个仓库（优先级最高）
    cli_repo = getattr(args, "repo", None)
    if cli_repo:
        logger.info(f"导入指定仓库: {cli_repo}")
        data = importer.fetch_repo(cli_repo)
        if data:
            all_repos.append(data)
        else:
            print(f"❌ 获取仓库失败: {cli_repo}（请检查仓库名格式 owner/repo 及网络）")
            return

    # 导入指定仓库列表
    repo_list = github_cfg.get("repo_list", [])
    if not cli_repo and repo_list:
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
    """抓取 r/AI_Agents 帖子 → Claude 翻译 → 写入飞书 Wiki「agent专区」"""
    from src.scrapers.reddit_scraper import RedditScraper
    from src.scrapers.daily_writer import DailyWriter

    creds = config_loader.load_credentials()
    ai_cfg = config_loader.get_ai_config()
    factory = FeishuClientFactory(creds["accounts"])
    personal_client = factory.get_client("personal")
    wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
    api_key = ai_cfg.get("api_key", "")

    limit = getattr(args, "limit", 20)
    logger.info(f"=== 抓取 r/AI_Agents（limit={limit}）===")
    scraper = RedditScraper(api_key=api_key)
    posts = scraper.fetch_posts(limit=limit)

    if not posts:
        print("未抓取到任何帖子，请检查网络")
        return

    print(f"抓取到 {len(posts)} 篇帖子，开始翻译...")
    posts = scraper.translate_posts(posts)

    writer = DailyWriter(personal_client, wiki_space_id)
    report = writer.write_reddit_posts(posts)

    print(f"\n✅ Reddit 日报完成！")
    print(f"  文件夹: {report['folder']}")
    print(f"  已写入: {report['written']} 篇")
    if report["failed"]:
        print(f"  失败:   {report['failed']} 篇")


def cmd_daily_github(args):
    """抓取 GitHub Trending → Claude 摘要 → 写入飞书 Wiki「github专区」"""
    from src.scrapers.github_trending_scraper import GitHubTrendingScraper
    from src.scrapers.daily_writer import DailyWriter

    creds = config_loader.load_credentials()
    ai_cfg = config_loader.get_ai_config()
    github_cfg = config_loader.get_github_config()
    factory = FeishuClientFactory(creds["accounts"])
    personal_client = factory.get_client("personal")
    wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
    api_key = ai_cfg.get("api_key", "")
    github_token = github_cfg.get("token", "")

    since = getattr(args, "since", "daily")
    top_n = getattr(args, "top_n", 15)

    logger.info(f"=== 抓取 GitHub Trending（since={since}, top_n={top_n}）===")
    scraper = GitHubTrendingScraper(
        api_key=api_key,
        github_token=github_token,
    )
    repos = scraper.fetch_trending(since=since, top_n=top_n)

    if not repos:
        print("未抓取到任何仓库，请检查网络或 beautifulsoup4 是否已安装")
        return

    print(f"抓取到 {len(repos)} 个仓库，开始生成中文摘要...")
    repos = scraper.generate_summaries(repos)

    writer = DailyWriter(personal_client, wiki_space_id)
    report = writer.write_github_trending(repos)

    print(f"\n✅ GitHub Trending 日报完成！")
    print(f"  文件夹: {report['folder']}")
    print(f"  已写入: {report['written']} 个仓库")
    if report["failed"]:
        print(f"  失败:   {report['failed']}")


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


def cmd_search(args):
    """按关键词搜索 Wiki 文档（从本地分类缓存）"""
    import sqlite3
    keyword = (args.keyword or "").strip()
    if not keyword:
        print("请提供搜索关键词，例如: python main.py search --keyword AI")
        return

    db_path = "data/agent.db"
    if not os.path.exists(db_path):
        print("本地索引为空，请先运行: python main.py organize --dry-run")
        return

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT title, category FROM doc_cache WHERE title LIKE ? ORDER BY category",
        (f"%{keyword}%",)
    ).fetchall()
    conn.close()

    if not rows:
        print(f"\n未找到包含「{keyword}」的文档")
        return

    from collections import defaultdict
    by_cat = defaultdict(list)
    for title, cat in rows:
        by_cat[cat].append(title)

    print(f"\n🔍 搜索「{keyword}」共找到 {len(rows)} 篇文档：\n")
    for cat, titles in by_cat.items():
        print(f"  📂 {cat}（{len(titles)} 篇）")
        for t in titles:
            print(f"     • {t}")
    print()


def cmd_daily_report(_args=None):
    """生成每日摘要报告：Wiki 统计 + 日历 + 消息"""
    import sqlite3
    from datetime import datetime
    from collections import Counter

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"📊 飞书 Agent 每日报告  {now}")
    print(f"{'='*60}\n")

    # ── Wiki 文档统计 ────────────────────────────────────────────
    db_path = "data/agent.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM doc_cache").fetchone()[0]
        cats = conn.execute(
            "SELECT category, COUNT(*) n FROM doc_cache GROUP BY category ORDER BY n DESC"
        ).fetchall()
        conn.close()
        print(f"📚 Wiki 文档总计：{total} 篇")
        for cat, n in cats:
            bar = "█" * min(n, 20)
            print(f"   {cat:<10} {bar} {n}")
    else:
        print("📚 Wiki 索引未建立，请先运行 organize --dry-run")

    print()

    # ── 日历（有权限时显示）──────────────────────────────────────
    print("📅 未来 7 天日历：")
    try:
        creds = config_loader.load_credentials()
        factory = FeishuClientFactory(creds["accounts"])
        client = factory.get_client("personal")
        from src.managers.calendar_manager import CalendarManager
        events = CalendarManager(client).list_events(days_ahead=7)
        if events:
            for e in events:
                print(f"   [{e.get('start_time','')[:10]}] {e.get('summary','无标题')}")
        else:
            print("   暂无日程（或需要开启 calendar 权限）")
    except Exception as e:
        print(f"   ⚠️ 获取失败（需要 calendar 权限）")

    print()

    # ── 最近邮件 ─────────────────────────────────────────────────
    print("📧 最近邮件：")
    try:
        from src.managers.email_manager import EmailManager
        mails = EmailManager(client).list_mails(limit=5)
        if mails:
            for m in mails:
                flag = "●" if not m.get("is_read") else " "
                print(f"   {flag} {m.get('subject','(无主题)')[:40]} — {m.get('from','')}")
        else:
            print("   暂无邮件（或需要开启 mail 权限）")
    except Exception:
        print("   ⚠️ 获取失败（需要 mail 权限）")

    print(f"\n{'='*60}\n")


def cmd_list_spaces(_args=None):
    """列出账号下所有 Wiki 空间及其 space_id，并尝试从 wiki_space_id 配置的 token 解析 space_id"""
    creds = config_loader.load_credentials()
    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")
    wiki_space_id = creds["accounts"].get("personal", {}).get("wiki_space_id", "")

    # 方法一：通过 node token 反查 space_id（无需机器人加入空间）
    if wiki_space_id and not wiki_space_id.isdigit():
        print(f"\n检测到 wiki_space_id='{wiki_space_id}' 是 token 而非数字，尝试通过节点 API 解析真实 space_id...")
        try:
            import lark_oapi as lark
            import json as _json
            # 直接调用 HTTP 接口查询节点信息
            token_resp = client.request(
                lark.RawRequest.builder()
                .http_method("GET")
                .uri(f"/open-apis/wiki/v2/nodes?token={wiki_space_id}&obj_type=wiki")
                .token_type(lark.AccessTokenType.TENANT)
                .build()
            )
            body = _json.loads(token_resp.raw.content)
            if body.get("code") == 0 and body.get("data", {}).get("node"):
                node = body["data"]["node"]
                space_id = node.get("space_id", "")
                title = node.get("title", "")
                print(f"\n✅ 成功解析！")
                print(f"  节点标题: {title}")
                print(f"  space_id: {space_id}")
                print(f"\n请将 credentials.json 中的 wiki_space_id 改为: {space_id}")
            else:
                code = body.get("code")
                msg = body.get("msg", "")
                print(f"节点解析失败 (code={code}): {msg}")
        except Exception as e:
            print(f"节点解析异常: {e}")

    # 方法二：通过 list-spaces API 列出应用机器人已加入的空间
    print("\n通过空间列表 API 查询（需机器人已加入空间）...")
    try:
        from lark_oapi.api.wiki.v2 import ListSpaceRequest
        req = ListSpaceRequest.builder().build()
        resp = client.wiki.v2.space.list(req)
        if not resp.success():
            print(f"获取空间列表失败: {resp.msg}")
        else:
            spaces = resp.data.items or []
            if not spaces:
                print("未找到任何 Wiki 空间（机器人尚未加入任何空间）")
                print("\n提示：在飞书开放平台确认「功能与能力」→「机器人」已开启，")
                print("然后在 Wiki 空间「成员管理」→「添加成员」→「机器人」tab 中添加。")
            else:
                print(f"\n找到 {len(spaces)} 个 Wiki 空间:\n")
                print(f"{'space_id':<25} {'名称'}")
                print("-" * 50)
                for s in spaces:
                    print(f"{s.space_id:<25} {s.name}")
                print("\n请将正确的 space_id（纯数字）填入 credentials.json 的 wiki_space_id 字段。")
    except Exception as e:
        print(f"异常: {e}")


def main():
    os.makedirs("logs", exist_ok=True)
    parser = argparse.ArgumentParser(description="飞书智能管理 Agent")
    subparsers = parser.add_subparsers(dest="command")

    # list-spaces 子命令
    subparsers.add_parser("list-spaces", help="列出所有 Wiki 空间及 space_id（首次使用）")

    # organize 子命令
    p_organize = subparsers.add_parser("organize", help="扫描文档并整理到个人 Wiki")
    p_organize.add_argument("--dry-run", action="store_true", help="仅预览，不实际移动文档")

    # import-github 子命令
    p_github = subparsers.add_parser("import-github", help="将 GitHub 仓库导入飞书")
    p_github.add_argument("--repo", help="指定仓库 owner/repo，如: --repo microsoft/vscode")

    # search 子命令
    p_search = subparsers.add_parser("search", help="按关键词搜索 Wiki 文档")
    p_search.add_argument("--keyword", required=True, help="搜索关键词")

    # daily-report 子命令
    subparsers.add_parser("daily-report", help="生成每日摘要报告")

    # daily-reddit 子命令
    p_reddit = subparsers.add_parser("daily-reddit", help="抓取 r/AI_Agents 帖子写入飞书「agent专区」")
    p_reddit.add_argument("--limit", type=int, default=20, help="每种排序抓取数量（默认 20）")

    # daily-github 子命令
    p_github_trend = subparsers.add_parser("daily-github", help="抓取 GitHub Trending 写入飞书「github专区」")
    p_github_trend.add_argument("--since", default="daily", choices=["daily", "weekly", "monthly"], help="时间范围")
    p_github_trend.add_argument("--top-n", dest="top_n", type=int, default=15, help="最多写入几个仓库（默认 15）")

    # manage 子命令
    p_manage = subparsers.add_parser("manage", help="管理飞书资源")
    p_manage.add_argument("resource", choices=["email", "messages", "calendar", "contacts"])
    p_manage.add_argument("--chat-id", dest="chat_id", help="IM 群聊 ID（管理消息时必填）")
    p_manage.add_argument("--query", help="搜索关键词（管理联系人时可用）")

    args = parser.parse_args()

    if args.command == "list-spaces":
        cmd_list_spaces(args)
    elif args.command == "organize":
        cmd_organize(args)
    elif args.command == "import-github":
        cmd_import_github(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "daily-report":
        cmd_daily_report(args)
    elif args.command == "daily-reddit":
        cmd_daily_reddit(args)
    elif args.command == "daily-github":
        cmd_daily_github(args)
    elif args.command == "manage":
        cmd_manage(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
