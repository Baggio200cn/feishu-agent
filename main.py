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
from src.plugins.registry import PluginRegistry

# Shared registry — lives for the duration of the process.
_plugin_registry = PluginRegistry()


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

    # 导入指定仓库列表
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


def cmd_plugins(args):
    """列出 plugins/ 目录中的所有插件及其状态。"""
    registry = _plugin_registry
    plugins_dir = registry.plugins_dir

    # Discover files on disk.
    if not os.path.isdir(plugins_dir):
        print(f"插件目录不存在: {plugins_dir}")
        print("请创建该目录并放入插件文件（*.py）。")
        return

    files = sorted(
        f for f in os.listdir(plugins_dir)
        if f.endswith(".py") and not f.startswith("_")
    )

    if not files:
        print(f"插件目录为空: {plugins_dir}")
        return

    # Load all so we can report accurate metadata.
    if not registry._plugins:
        registry.load_all()

    loaded_by_file = {}
    for name, (instance, mod_name) in registry._plugins.items():
        stem = mod_name.removeprefix("_feishu_plugin_")
        loaded_by_file[stem] = instance

    print(f"插件目录: {plugins_dir}")
    print(f"共 {len(files)} 个插件文件:\n")
    for filename in files:
        stem = os.path.splitext(filename)[0]
        instance = loaded_by_file.get(stem)
        if instance:
            info = instance.get_info()
            status = "已加载"
            desc = info["description"] or "(无描述)"
            reg_name = info["name"]
            print(f"  [{status}]  {filename}")
            print(f"            名称: {reg_name}   描述: {desc}")
        else:
            print(f"  [未加载]  {filename}")
        print()


def cmd_reload_plugins(args):
    """重新加载 plugins/ 目录中的所有插件（无需重启进程）。"""
    registry = _plugin_registry

    if args.plugin:
        # If the registry is empty (fresh CLI invocation), populate it first so
        # reload() can find already-known plugins by name.
        if not registry._plugins:
            registry.load_all()
        result = registry.reload(args.plugin)
        _print_reload_result({args.plugin: result})
    else:
        # Reload everything (and discover new files).
        results = registry.reload_all()
        if not results:
            # Nothing was loaded before — do an initial load instead.
            loaded = registry.load_all()
            results = {name: "loaded" for name in loaded}

        if results:
            _print_reload_result(results)
        else:
            print("plugins/ 目录为空或不存在，无插件可加载。")
            print(f"  插件目录: {registry.plugins_dir}")

    # Also bust the config cache so updated credentials/categories take effect.
    if args.reload_config:
        config_loader.reload()
        print("配置缓存已清除，下次访问将重新读取配置文件。")

    # List currently active plugins.
    active = registry.list_plugins()
    if active:
        print(f"\n当前已加载插件 ({len(active)} 个):")
        for info in active:
            print(f"  • {info['name']}: {info['description']}  [{info['module']}]")


def _print_reload_result(results: dict) -> None:
    ok = {k: v for k, v in results.items() if not v.startswith("error")}
    err = {k: v for k, v in results.items() if v.startswith("error")}
    if ok:
        print(f"成功处理 {len(ok)} 个插件:")
        for name, status in ok.items():
            print(f"  ✓ {name}: {status}")
    if err:
        print(f"失败 {len(err)} 个插件:")
        for name, status in err.items():
            print(f"  ✗ {name}: {status}")


def main():
    os.makedirs("logs", exist_ok=True)
    parser = argparse.ArgumentParser(description="飞书智能管理 Agent")
    subparsers = parser.add_subparsers(dest="command")

    # organize 子命令
    p_organize = subparsers.add_parser("organize", help="扫描文档并整理到个人 Wiki")
    p_organize.add_argument("--dry-run", action="store_true", help="仅预览，不实际移动文档")

    # import-github 子命令
    subparsers.add_parser("import-github", help="将 GitHub 仓库导入飞书")

    # manage 子命令
    p_manage = subparsers.add_parser("manage", help="管理飞书资源")
    p_manage.add_argument("resource", choices=["email", "messages", "calendar", "contacts"])
    p_manage.add_argument("--chat-id", dest="chat_id", help="IM 群聊 ID（管理消息时必填）")
    p_manage.add_argument("--query", help="搜索关键词（管理联系人时可用）")

    # plugins 子命令
    subparsers.add_parser("plugins", help="列出 plugins/ 目录中的所有插件及其状态")

    # reload-plugins 子命令
    p_reload = subparsers.add_parser(
        "reload-plugins",
        help="重新加载 plugins/ 目录中的插件（无需重启）",
    )
    p_reload.add_argument(
        "plugin",
        nargs="?",
        default=None,
        help="仅重载指定插件名；省略则重载全部",
    )
    p_reload.add_argument(
        "--reload-config",
        action="store_true",
        help="同时清除配置文件缓存，强制重新读取 credentials.json / categories.json",
    )

    args = parser.parse_args()

    if args.command == "organize":
        cmd_organize(args)
    elif args.command == "import-github":
        cmd_import_github(args)
    elif args.command == "manage":
        cmd_manage(args)
    elif args.command == "plugins":
        cmd_plugins(args)
    elif args.command == "reload-plugins":
        cmd_reload_plugins(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
