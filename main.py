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
  python main.py import-reddit     # Reddit AI 日报：订阅 subreddit → 豆包摘要 → 飞书
  python main.py chat "关键词"       # 对话助手：搜 Wiki + 豆包回答
  python main.py cleanup-wiki --prefix "xxx"           # 预览批量删（DRY RUN）
  python main.py cleanup-wiki --prefix "xxx" --confirm # 真删
  python main.py schedule          # 启动定时任务调度器（守护进程）
  python main.py schedule-status   # 查询调度器状态（输出 JSON）
"""
import argparse
import json
import logging
import os
import sys
import time
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
    cached = _load_trending_cache(cache_path)
    # 续跑策略：只保留成功的，失败的丢掉（这样 --force 或者自动重试都能重跑失败项）
    items_with_summary: List[Dict[str, Any]] = []
    if cached and not force:
        items_with_summary = [it for it in cached if it.get("summary_ok")]
        done_names = {it["repo"].get("full_name") for it in items_with_summary}
        retried = len(cached) - len(items_with_summary)
        if retried:
            logger.info(
                f"缓存命中 {len(done_names)} 个成功项；"
                f"{retried} 个失败项将在本次重试（丢弃旧 fallback）"
            )
        else:
            logger.info(f"从缓存恢复 {len(done_names)} 个已摘要仓库: {cache_path}")
    else:
        done_names = set()
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


def cmd_import_reddit(args):
    """
    Reddit AI 日报：订阅 subreddit top-of-day → 豆包 AI 中文摘要 →
    按日期汇总为文件夹写入飞书 Wiki 的 reddit专区。
    """
    from datetime import datetime

    from src.importers.reddit_importer import RedditImporter
    from src.importers.ai_summarizer import AISummarizer
    from src.importers.feishu_doc_writer import FeishuDocWriter

    run_stats: Dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "status": "error",
        "fetched": 0,
        "summarized": 0,
        "ai_failed": 0,
        "wiki_url": "",
        "message": "",
    }

    try:
        creds = config_loader.load_credentials()
        reddit_cfg = creds.get("reddit", {}) or {}
        ai_cfg = config_loader.get_ai_config()
        wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")

        # 前置校验
        if not reddit_cfg.get("enabled", True):
            msg = "Reddit 抓取未启用（credentials.json 里 reddit.enabled=false）"
            logger.info(msg)
            run_stats["status"] = "disabled"
            run_stats["message"] = msg
            _write_reddit_stats(run_stats)
            return

        subs = reddit_cfg.get("subreddits") or []
        if not subs:
            msg = "Reddit subreddit 列表为空"
            logger.error(msg)
            run_stats["message"] = msg
            _write_reddit_stats(run_stats)
            return

        if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
            msg = "飞书 Wiki space_id 未配置"
            logger.error(msg)
            run_stats["message"] = msg
            _write_reddit_stats(run_stats)
            return

        summarizer = AISummarizer(ai_cfg)
        if not summarizer.configured():
            msg = "豆包 AI 未配置，Reddit 摘要需要 AI"
            logger.error(msg)
            run_stats["message"] = msg
            _write_reddit_stats(run_stats)
            return

        factory = FeishuClientFactory(creds["accounts"])
        personal_client = factory.get_client("personal")
        writer = FeishuDocWriter(personal_client, wiki_space_id)

        date_str = datetime.now().strftime("%Y-%m-%d")
        parent_folder = reddit_cfg.get("parent_folder", "reddit专区")
        force = getattr(args, "force", False)

        # 1. 抓 Reddit
        importer = RedditImporter(
            subreddits=subs,
            user_agent=reddit_cfg.get("user_agent", "feishu-agent/0.1"),
        )
        posts = importer.fetch_daily(
            period=reddit_cfg.get("period", "day"),
            per_sub_fetch=int(reddit_cfg.get("per_sub_fetch", 10)),
            limit_total=int(reddit_cfg.get("limit_total", 10)),
            top_comments=int(reddit_cfg.get("top_comments", 3)),
        )
        run_stats["fetched"] = len(posts)
        if not posts:
            msg = "Reddit 返回为空（可能 VPN 断了 / 限频 / subreddit 名写错）"
            logger.warning(msg)
            run_stats["message"] = msg
            _write_reddit_stats(run_stats)
            return

        # 2. 对每个帖子做 AI 摘要，缓存增量保存
        # 续跑策略：只留成功的；失败项自动重试（丢弃旧 fallback）
        cache_path = f"logs/reddit_cache_{date_str}.json"
        cached = _load_trending_cache(cache_path)
        items_with_summary = []
        done_ids: set = set()
        if cached and not force:
            items_with_summary = [it for it in cached if it.get("summary_ok")]
            done_ids = {it["post"].get("id") for it in items_with_summary}
            retried = len(cached) - len(items_with_summary)
            if retried:
                logger.info(
                    f"缓存命中 {len(done_ids)} 条成功项；"
                    f"{retried} 条失败项将在本次重试"
                )
            else:
                logger.info(f"从缓存恢复 {len(done_ids)} 条已摘要 Reddit 帖子")

        for i, post in enumerate(posts, 1):
            pid = post.get("id", "")
            if pid in done_ids:
                logger.info(f"[{i}/{len(posts)}] 跳过（缓存已有）: r/{post['subreddit']}/{pid}")
                continue
            logger.info(f"[{i}/{len(posts)}] 摘要 r/{post['subreddit']}/{pid} {post['title'][:40]}")
            summary = summarizer.summarize_reddit_post(post)
            summary_ok = summary is not None
            if not summary:
                summary = {
                    "one_liner": post.get("title", "")[:30] or "（AI 摘要失败）",
                    "detail": "AI 摘要失败，请点击原帖查看。",
                }
            items_with_summary.append({
                "post": post,
                "summary": summary,
                "summary_ok": summary_ok,
            })
            _save_trending_cache(cache_path, items_with_summary)

        run_stats["summarized"] = sum(1 for it in items_with_summary if it.get("summary_ok"))
        run_stats["ai_failed"] = len(items_with_summary) - run_stats["summarized"]

        # 3. 写飞书日报（文件夹 + 子页）
        result = writer.write_daily_reddit_report(
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
            if run_stats["ai_failed"] == 0 and result["created_count"] + result["skipped_count"] == len(items_with_summary):
                run_stats["status"] = "success"
            else:
                run_stats["status"] = "partial"
            run_stats["message"] = (
                f"抓取 {run_stats['fetched']} · 摘要 {run_stats['summarized']}"
                f" · AI失败 {run_stats['ai_failed']}"
                f" · 新建页 {result['created_count']} · 跳过 {result['skipped_count']}"
            )
            print(f"\n✅ Reddit 日报文件夹: {result['folder_url']}")
            print(f"   {run_stats['message']}")
            print(f"\n子页面:")
            for p in result["repo_pages"]:
                marker = "🆕" if p["created"] else "  "
                print(f"   {marker} {p['title'][:70]}")
                if p["url"]:
                    print(f"        {p['url']}")
        else:
            run_stats["message"] = "Reddit 日报写入失败，可能飞书权限或节点问题"
            logger.error(run_stats["message"])

        _write_reddit_stats(run_stats)

    except Exception as e:
        logger.exception("import-reddit 异常")
        run_stats["message"] = f"异常终止: {e}"
        _write_reddit_stats(run_stats)
        raise


def _write_reddit_stats(stats: Dict[str, Any]) -> None:
    os.makedirs("logs", exist_ok=True)
    with open("logs/reddit_last_run.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


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


def cmd_chat(args):
    """
    对话助手 MVP：关键词搜飞书 Wiki → 豆包 AI 基于搜索结果给中文回答。

    用法:
      python main.py chat "关键词"
      python main.py chat "关键词" --limit 5 --json   # 输出 JSON 格式供 UI 调用
    """
    query = (getattr(args, "query", "") or "").strip()
    if not query:
        print("请传入查询关键词: python main.py chat \"你的问题\"")
        sys.exit(1)

    limit = int(getattr(args, "limit", 5) or 5)
    json_output = bool(getattr(args, "json", False))

    result: Dict[str, Any] = {
        "query": query,
        "answer": "",
        "sources": [],
        "status": "error",
        "message": "",
    }

    try:
        creds = config_loader.load_credentials()
        wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
        if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
            result["message"] = "飞书 Wiki space_id 未配置"
            _emit_chat_result(result, json_output)
            return

        factory = FeishuClientFactory(creds["accounts"])
        client = factory.get_client("personal")

        # 1. 搜 Wiki — 用 ListSpaceNode + 递归标题匹配（tenant_access_token 兼容）
        # Wiki v1 SearchNode 需要 user_access_token (99991668)，tenant 过不了
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

        query_lower = query.lower()
        matches: List[Dict[str, Any]] = []

        def _scan(parent_token: Optional[str], depth: int):
            if len(matches) >= limit * 3 or depth > 4:
                return
            page_token = None
            for _ in range(5):
                b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50)
                if parent_token:
                    b.parent_node_token(parent_token)
                if page_token:
                    b.page_token(page_token)
                r = client.wiki.v2.space_node.list(b.build())
                if not r.success():
                    return
                for it in (getattr(r.data, "items", None) or []):
                    title = getattr(it, "title", "") or ""
                    if query_lower in title.lower():
                        matches.append({
                            "title": title,
                            "node_token": it.node_token,
                            "has_child": bool(getattr(it, "has_child", False)),
                        })
                        if len(matches) >= limit * 3:
                            return
                    # 递归向下（限深度）
                    if getattr(it, "has_child", False) and depth < 4:
                        _scan(it.node_token, depth + 1)
                        if len(matches) >= limit * 3:
                            return
                if not getattr(r.data, "has_more", False):
                    break
                page_token = getattr(r.data, "page_token", None)
                if not page_token:
                    break

        _scan(None, 0)

        # 按匹配度简单排序：完全包含 > 前缀 > 任意位置
        def _score(title: str) -> int:
            t = title.lower()
            if t == query_lower: return 100
            if t.startswith(query_lower): return 50
            return 10
        matches.sort(key=lambda m: _score(m["title"]), reverse=True)

        for m in matches[:limit]:
            result["sources"].append({
                "title": m["title"],
                "url": f"https://open.feishu.cn/wiki/{m['node_token']}",
                "node_token": m["node_token"],
            })

        if not result["sources"]:
            result["status"] = "success"
            result["answer"] = f"在飞书 Wiki 里没找到与「{query}」相关的页面。"
            _emit_chat_result(result, json_output)
            return

        # 2. 豆包基于搜索结果写回答
        from src.importers.ai_summarizer import AISummarizer

        ai_cfg = config_loader.get_ai_config()
        summarizer = AISummarizer(ai_cfg)
        if not summarizer.configured():
            # 没配 AI 时降级为"只列搜索结果"
            result["status"] = "partial"
            result["answer"] = (
                f"找到 {len(result['sources'])} 个相关 Wiki 页面（未配置豆包 AI，"
                f"仅列出标题；配置 ai.api_key 后能自动总结）："
            )
            _emit_chat_result(result, json_output)
            return

        sources_lines = "\n".join(
            f"- {s['title']}  {s['url']}" for s in result["sources"]
        )
        prompt = (
            f"用户询问: {query}\n\n"
            f"根据以下飞书 Wiki 里检索到的相关页面标题和链接，用中文给出一个简短的"
            f"帮助回答（200 字以内），引用具体页面名，不要编造页面中没有的内容。"
            f"如果信息不足以回答，直接说\"建议直接点击下方链接查看\"。\n\n"
            f"检索结果:\n{sources_lines}"
        )
        ai_answer = summarizer._chat_json(
            tag=f"chat[{query[:20]}]",
            user_prompt=(
                prompt + "\n\n返回 JSON: "
                + '{"one_liner": "一句话答复（必填）", "detail": "展开说明（必填）"}'
            ),
            timeout=60,
            retries=1,
        )
        if ai_answer:
            result["answer"] = ai_answer.get("detail") or ai_answer.get("one_liner") or ""
            result["status"] = "success"
        else:
            result["answer"] = f"AI 回答生成失败，以下是找到的 {len(result['sources'])} 个相关页面："
            result["status"] = "partial"

        _emit_chat_result(result, json_output)

    except Exception as e:
        logger.exception("chat 异常")
        result["message"] = f"异常: {e}"
        _emit_chat_result(result, json_output)
        if not json_output:
            raise


def _emit_chat_result(result: Dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"\n问：{result['query']}")
    if result.get("answer"):
        print(f"\n{result['answer']}")
    if result.get("sources"):
        print("\n相关 Wiki 页面：")
        for s in result["sources"]:
            print(f"  • {s['title']}  {s['url']}")
    if result.get("message"):
        print(f"\n⚠️ {result['message']}")


def cmd_cleanup_wiki(args):
    """
    Wiki 清理：按标题前缀 或 按 node_token 列表批量删除空间下的节点。
    **危险操作**：默认 --dry-run，只有显式加 --confirm 才真删。
    删除路径：先试 wiki DELETE，forbidden（1061004）时兜底到 drive DELETE。

    用法:
      python main.py cleanup-wiki --prefix "[诊断]"
      python main.py cleanup-wiki --prefix "[诊断]" --confirm
      python main.py cleanup-wiki --node-tokens tok1,tok2,tok3 --confirm --json
      python main.py cleanup-wiki --json
    """
    from src.agent import wiki_agent

    prefix = (getattr(args, "prefix", "") or "").strip()
    node_tokens_raw = (getattr(args, "node_tokens", "") or "").strip()
    node_tokens = [t.strip() for t in node_tokens_raw.split(",") if t.strip()]
    confirm = bool(getattr(args, "confirm", False))
    json_output = bool(getattr(args, "json", False))

    result: Dict[str, Any] = {
        "prefix": prefix,
        "node_tokens": node_tokens,
        "dry_run": not confirm,
        "matched": [],
        "deleted": [],
        "failed": [],
        "status": "error",
        "message": "",
    }

    if not prefix and not node_tokens:
        result["message"] = "必须指定 --prefix 或 --node-tokens，避免误删整个空间"
        _emit_cleanup_result(result, json_output)
        return

    try:
        creds = config_loader.load_credentials()
        wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
        if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
            result["message"] = "飞书 Wiki space_id 未配置"
            _emit_cleanup_result(result, json_output)
            return

        factory = FeishuClientFactory(creds["accounts"])
        client = factory.get_client("personal")

        personal_cfg = creds["accounts"]["personal"]
        tenant_token = wiki_agent.get_tenant_token(
            personal_cfg.get("app_id", ""), personal_cfg.get("app_secret", "")
        )
        if not tenant_token:
            result["message"] = "获取 tenant_access_token 失败，检查 app_id / app_secret"
            _emit_cleanup_result(result, json_output)
            return

        # 匹配目标节点
        matched_nodes: List[Dict[str, Any]] = []
        if node_tokens:
            # 按 token 精确匹配，需要从树里拿到 title / obj_token / obj_type
            all_nodes = wiki_agent.scan_wiki_tree(client, wiki_space_id)
            by_tok = {n["node_token"]: n for n in all_nodes}
            for tok in node_tokens:
                n = by_tok.get(tok)
                if n:
                    matched_nodes.append({
                        "title": n["title"],
                        "node_token": n["node_token"],
                        "obj_token": n.get("obj_token", ""),
                        "obj_type": n.get("obj_type", ""),
                    })
                else:
                    matched_nodes.append({
                        "title": "(未找到)",
                        "node_token": tok,
                        "obj_token": "",
                        "obj_type": "",
                        "not_found": True,
                    })
        else:
            # 按前缀匹配，只扫一层顶层（保持原行为：前缀 delete 针对日报目录这类顶层）
            from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest
            page_token = None
            for _ in range(10):
                builder = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50)
                if page_token:
                    builder.page_token(page_token)
                resp = client.wiki.v2.space_node.list(builder.build())
                if not resp.success():
                    result["message"] = f"列节点失败: {resp.code} {resp.msg}"
                    _emit_cleanup_result(result, json_output)
                    return
                for it in (getattr(resp.data, "items", None) or []):
                    title = getattr(it, "title", "") or ""
                    if title.startswith(prefix):
                        matched_nodes.append({
                            "title": title,
                            "node_token": it.node_token,
                            "obj_token": getattr(it, "obj_token", "") or "",
                            "obj_type": getattr(it, "obj_type", "") or "",
                        })
                if not getattr(resp.data, "has_more", False):
                    break
                page_token = getattr(resp.data, "page_token", None)
                if not page_token:
                    break

        result["matched"] = matched_nodes

        if not matched_nodes:
            result["status"] = "success"
            result["message"] = (
                f"没有标题以 '{prefix}' 开头的节点" if prefix
                else "未找到任何指定 node_token"
            )
            _emit_cleanup_result(result, json_output)
            return

        if not confirm:
            result["status"] = "dry_run"
            result["message"] = (
                f"预览模式：匹配到 {len(matched_nodes)} 个节点。加 --confirm 才会真删。"
            )
            _emit_cleanup_result(result, json_output)
            return

        # 真删（含 drive 兜底）
        batch_result = wiki_agent.batch_delete_nodes(
            tenant_token=tenant_token,
            wiki_space_id=wiki_space_id,
            targets=matched_nodes,
        )
        result["deleted"] = batch_result["deleted"]
        result["failed"] = batch_result["failed"]
        result["status"] = "success" if not result["failed"] else "partial"
        result["message"] = (
            f"删除 {len(result['deleted'])} 个，失败 {len(result['failed'])} 个"
        )
        _emit_cleanup_result(result, json_output)

    except Exception as e:
        logger.exception("cleanup-wiki 异常")
        result["message"] = f"异常: {e}"
        _emit_cleanup_result(result, json_output)
        if not json_output:
            raise


def _emit_cleanup_result(result: Dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
        return
    prefix = result.get("prefix") or ""
    toks = result.get("node_tokens") or []
    if prefix:
        print(f"\n清理目标前缀：'{prefix}'")
    if toks:
        print(f"\n清理目标 node_tokens：{len(toks)} 个")
    print(f"匹配到 {len(result['matched'])} 个节点:")
    for n in result["matched"]:
        marker = "·"
        if any(d["node_token"] == n["node_token"] for d in result.get("deleted", [])):
            marker = "✅"
        elif any(f["node_token"] == n["node_token"] for f in result.get("failed", [])):
            marker = "❌"
        print(f"  {marker} {n['title']}  (token={n['node_token']})")
    if result["dry_run"]:
        print(f"\n[DRY-RUN] {result['message']}")
    else:
        print(f"\n{result['message']}")


# ============================================================================
# scan-empty-wiki：扫描 Wiki 树，找出正文为空的节点
# ============================================================================
def cmd_scan_empty_wiki(args):
    """
    扫全部 Wiki 节点 → 逐个读 docx 正文 → 判定空节点。
    非 docx 类型（doc / sheet / file 等）计入 skipped。

    用法:
      python main.py scan-empty-wiki --json
      python main.py scan-empty-wiki --threshold 5 --max 500
    """
    from src.agent import wiki_agent

    threshold = int(getattr(args, "threshold", wiki_agent.EMPTY_BODY_CHAR_THRESHOLD)
                    or wiki_agent.EMPTY_BODY_CHAR_THRESHOLD)
    max_nodes = int(getattr(args, "max", wiki_agent.MAX_NODES_TO_SCAN)
                    or wiki_agent.MAX_NODES_TO_SCAN)
    json_output = bool(getattr(args, "json", False))

    result: Dict[str, Any] = {
        "scanned": 0,
        "read_ok": 0,
        "skipped": [],
        "empty": [],
        "body_threshold": threshold,
        "status": "error",
        "message": "",
    }

    try:
        creds = config_loader.load_credentials()
        wiki_space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
        if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
            result["message"] = "飞书 Wiki space_id 未配置"
        else:
            factory = FeishuClientFactory(creds["accounts"])
            client = factory.get_client("personal")
            personal_cfg = creds["accounts"]["personal"]
            tenant_token = wiki_agent.get_tenant_token(
                personal_cfg.get("app_id", ""), personal_cfg.get("app_secret", "")
            )
            if not tenant_token:
                result["message"] = "获取 tenant_access_token 失败"
            else:
                scan = wiki_agent.detect_empty_nodes(
                    client=client,
                    tenant_token=tenant_token,
                    wiki_space_id=wiki_space_id,
                    body_threshold=threshold,
                    max_nodes=max_nodes,
                )
                result.update(scan)
                result["status"] = "success"
                result["message"] = (
                    f"扫 {scan['scanned']} 个 · 读到 {scan['read_ok']} 个 · "
                    f"跳过 {len(scan['skipped'])} 个 · 真正空 {len(scan['empty'])} 个"
                )
    except Exception as e:
        logger.exception("scan-empty-wiki 异常")
        result["message"] = f"异常: {e}"

    if json_output:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"\n{result['message']}")
    for n in result.get("empty", [])[:50]:
        print(f"  · {n['title']}  (正文 {n.get('body_chars',0)} 字, token={n['node_token']})")


# ============================================================================
# agent-chat：对话式 Wiki 管家（意图路由 + 预览 + 确认执行 + 多轮会话）
# ============================================================================
def cmd_agent_chat(args):
    """
    对话 Agent · Wiki 管家。

    能力: 搜 Wiki · 找空文档 · 删空文档 · 按前缀删除 · 整理建议 · 连续对话。
    会话状态持久化在 logs/agent_sessions/{session_id}.json。

    用法:
      python main.py agent-chat --query "找空节点" --json
      python main.py agent-chat --query "确认执行" --session sess-xxx --json
      python main.py agent-chat --reset --session sess-xxx --json
    """
    from src.agent import wiki_agent

    query = (getattr(args, "query", "") or "").strip()
    session_id = (getattr(args, "session", "") or "").strip()
    reset = bool(getattr(args, "reset", False))
    json_output = bool(getattr(args, "json", False))

    reply: Dict[str, Any] = {
        "session_id": session_id or wiki_agent.new_session_id(),
        "query": query,
        "intent": "",
        "reply_text": "",
        "preview": None,        # {title, items: [...], summary}
        "pending_action": None, # {id, type, params, confirm_hint}
        "sources": [],          # 搜索结果
        "status": "error",
        "message": "",
    }

    # 重置会话
    if reset:
        session = {"id": reply["session_id"], "history": [], "pending_action": None}
        wiki_agent.save_session(session)
        reply.update({
            "status": "success",
            "intent": "reset",
            "reply_text": "会话已重置。Agent 能做：搜 Wiki · 找空文档 · 删空文档 · 按前缀删除 · 整理建议。说句话开始吧。",
        })
        print(json.dumps(reply, ensure_ascii=False) if json_output else reply["reply_text"])
        return

    if not query:
        reply["message"] = "请传入 --query"
        print(json.dumps(reply, ensure_ascii=False) if json_output else reply["message"])
        return

    session = wiki_agent.load_session(session_id)
    reply["session_id"] = session["id"]

    try:
        creds = config_loader.load_credentials()
        personal_cfg = creds["accounts"]["personal"]
        wiki_space_id = personal_cfg.get("wiki_space_id", "")
        if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
            reply["message"] = "飞书 Wiki space_id 未配置"
            print(json.dumps(reply, ensure_ascii=False))
            return

        factory = FeishuClientFactory(creds["accounts"])
        client = factory.get_client("personal")
        tenant_token = wiki_agent.get_tenant_token(
            personal_cfg.get("app_id", ""), personal_cfg.get("app_secret", "")
        )
        if not tenant_token:
            reply["message"] = "获取 tenant_access_token 失败"
            print(json.dumps(reply, ensure_ascii=False))
            return

        intent = wiki_agent.classify_intent(query, session)
        reply["intent"] = intent

        # ---- 确认执行待定动作 ----
        if intent == "confirm":
            pending = session.get("pending_action") or {}
            action_type = pending.get("type")
            params = pending.get("params") or {}
            if not action_type:
                reply["reply_text"] = "没有待确认的操作。"
                reply["status"] = "success"
            elif action_type == "delete_nodes":
                targets = params.get("targets") or []
                if not targets:
                    reply["reply_text"] = "待删节点列表为空。"
                    reply["status"] = "success"
                else:
                    br = wiki_agent.batch_delete_nodes(tenant_token, wiki_space_id, targets)
                    ok = len(br["deleted"])
                    bad = len(br["failed"])
                    if bad == 0:
                        reply["reply_text"] = f"本次删除完成：成功 {ok} 个。"
                    else:
                        # 全部/大部分失败时，给出真相 + 替代方案
                        reply["reply_text"] = (
                            f"删除成功 {ok} 个，失败 {bad} 个。\n\n"
                            f"📌 原因：飞书 Wiki v2 API 没有删除节点方法，只能通过 drive 删底层 docx；"
                            f"而个人 Wiki 下由您本人创建的 docx，应用 tenant_token 无删除权限 (1061004)。"
                            f"这是飞书 API 硬限制，加权限也绕不过。\n\n"
                            f"✅ 两条替代路径：\n"
                            f"1) 说「标记空节点」让我把这些节点改名加 🗑[空] 前缀，"
                            f"你在 Wiki UI 里肉眼排一片 🗑 批量选删；\n"
                            f"2) 下方列出了全部直链，点进去手动删也行。"
                        )
                    # 失败节点附 wiki 直链
                    failed_urls = wiki_agent.build_wiki_urls(br["failed"])
                    reply["sources"] = failed_urls[:30]  # 超过 30 条就截断
                    reply["preview"] = {
                        "title": "处理结果",
                        "items": [
                            *[{"title": d["title"], "ok": True, "detail": "已通过 drive 删除"}
                              for d in br["deleted"]],
                            *[{"title": f["title"], "ok": False, "detail": (f.get("error", "")[:100])}
                              for f in br["failed"]],
                        ],
                        "summary": f"成功 {ok} · 失败 {bad}",
                    }
                    reply["status"] = "success" if bad == 0 else "partial"
            elif action_type == "mark_nodes":
                targets = params.get("targets") or []
                if not targets:
                    reply["reply_text"] = "待标记节点为空。"
                    reply["status"] = "success"
                else:
                    mr = wiki_agent.mark_node_titles(client, wiki_space_id, targets)
                    ok = len(mr["marked"])
                    bad = len(mr["failed"])
                    reply["reply_text"] = (
                        f"标记完成：{ok} 个节点已加 🗑[空] 前缀，{bad} 个失败。\n"
                        f"现在到飞书 Wiki UI 里，搜 🗑 就能一眼圈出全部空节点，批量选中后手动删即可。"
                    )
                    reply["preview"] = {
                        "title": "标记结果",
                        "items": [
                            *[{"title": f"{m.get('new_title','')}", "ok": True,
                               "detail": "已改名"} for m in mr["marked"]],
                            *[{"title": f["title"], "ok": False,
                               "detail": f.get("error", "")} for f in mr["failed"]],
                        ],
                        "summary": f"成功 {ok} · 失败 {bad}",
                    }
                    reply["status"] = "success" if bad == 0 else "partial"
            elif action_type == "cleanup_prefix":
                prefix = params.get("prefix", "")
                targets = params.get("targets") or []
                if not targets:
                    reply["reply_text"] = f"前缀 '{prefix}' 没有匹配节点。"
                    reply["status"] = "success"
                else:
                    br = wiki_agent.batch_delete_nodes(tenant_token, wiki_space_id, targets)
                    ok = len(br["deleted"])
                    bad = len(br["failed"])
                    reply["reply_text"] = f"按前缀 '{prefix}' 清理完成：成功 {ok}，失败 {bad}。"
                    reply["preview"] = {
                        "title": "删除结果",
                        "items": [
                            *[{"title": d["title"], "ok": True, "detail": f"path={d.get('path','?')}"}
                              for d in br["deleted"]],
                            *[{"title": f["title"], "ok": False, "detail": f.get("error", "")}
                              for f in br["failed"]],
                        ],
                        "summary": f"成功 {ok} · 失败 {bad}",
                    }
                    reply["status"] = "success" if bad == 0 else "partial"
            session["pending_action"] = None

        # ---- 取消 ----
        elif intent == "cancel":
            session["pending_action"] = None
            reply["reply_text"] = "好的，已取消。没有做任何修改。"
            reply["status"] = "success"

        # ---- 找空节点（只预览） ----
        elif intent == "scan_empty":
            scan = wiki_agent.detect_empty_nodes(client, tenant_token, wiki_space_id)
            # 缓存到 session 供后续 delete_empty / mark_empty 复用
            session["last_scan"] = {"empty": scan["empty"], "ts": int(time.time())}
            reply["reply_text"] = (
                f"扫 {scan['scanned']} 个 · 读到 {scan['read_ok']} 个 · "
                f"跳过 {len(scan['skipped'])} 个（权限/类型不支持） · 真正空 {len(scan['empty'])} 个。"
                + ("" if not scan['empty']
                   else "\n\n如需删除，回复「删空节点」；想改名加 🗑 前缀方便手动删，回复「标记空节点」。"
                        "（结果已缓存 5 分钟，不用等重扫）")
            )
            reply["preview"] = {
                "title": "扫描结果",
                "items": [
                    {"title": n["title"], "ok": True,
                     "detail": f"正文 {n.get('body_chars',0)} 字"}
                    for n in scan["empty"]
                ],
                "summary": f"空节点 {len(scan['empty'])} 个",
            }
            reply["status"] = "success"

        # ---- 删空节点（预览 + stage pending_action） ----
        elif intent == "delete_empty":
            # 优先复用最近 5 分钟内的扫描结果
            cached = session.get("last_scan") or {}
            if cached and (int(time.time()) - cached.get("ts", 0) < 300):
                empties = cached.get("empty", [])
                logger.info(f"复用 5 分钟内缓存的扫描结果: {len(empties)} 个空节点")
                scan_info = f"（复用 {len(empties)} 个已缓存空节点）"
            else:
                scan = wiki_agent.detect_empty_nodes(client, tenant_token, wiki_space_id)
                empties = scan["empty"]
                session["last_scan"] = {"empty": empties, "ts": int(time.time())}
                scan_info = f"（新扫 {scan['scanned']} 个节点，其中 {len(empties)} 个真正空）"
            if not empties:
                reply["reply_text"] = (
                    f"扫 {scan['scanned']} 个节点，没找到空节点，无需清理。"
                )
                reply["status"] = "success"
            else:
                targets = [
                    {"title": n["title"], "node_token": n["node_token"],
                     "obj_token": n.get("obj_token", ""), "obj_type": n.get("obj_type", "")}
                    for n in empties
                ]
                session["pending_action"] = {
                    "id": "act-" + time.strftime("%H%M%S"),
                    "type": "delete_nodes",
                    "params": {"targets": targets},
                    "confirm_hint": "回复「确认执行」真删，或「取消」放弃。",
                }
                reply["reply_text"] = (
                    f"找到 {len(empties)} 个空节点 {scan_info}。\n"
                    f"⚠️ 真删路径在个人 Wiki 下通常会因飞书 API 限制失败（drive 权限不够）。"
                    f"如果删除大面积失败，换句话说「标记空节点」——我会把标题加 🗑[空] 前缀，"
                    f"你在 Wiki UI 里肉眼批量删。\n\n"
                    f"确认真删请回「确认执行」，想改走标记路径回「标记空节点」，不想动回「取消」。"
                )
                reply["preview"] = {
                    "title": f"即将删除 {len(empties)} 个空节点",
                    "items": [
                        {"title": n["title"], "ok": True,
                         "detail": f"正文 {n.get('body_chars',0)} 字"}
                        for n in empties
                    ],
                    "summary": f"共 {len(empties)} 个，删除后不可恢复",
                }
                reply["pending_action"] = session["pending_action"]
                reply["status"] = "success"

        # ---- 标记空节点（改名加 🗑[空] 前缀，作为真删的替代方案） ----
        elif intent == "mark_empty":
            # 同样优先复用缓存
            cached = session.get("last_scan") or {}
            if cached and (int(time.time()) - cached.get("ts", 0) < 300):
                empties = cached.get("empty", [])
                logger.info(f"mark_empty 复用缓存: {len(empties)} 个空节点")
            else:
                scan = wiki_agent.detect_empty_nodes(client, tenant_token, wiki_space_id)
                empties = scan["empty"]
                session["last_scan"] = {"empty": empties, "ts": int(time.time())}
            if not empties:
                reply["reply_text"] = f"扫 {scan['scanned']} 个节点，没找到空节点，无需标记。"
                reply["status"] = "success"
            else:
                targets = [
                    {"title": n["title"], "node_token": n["node_token"],
                     "obj_token": n.get("obj_token", ""), "obj_type": n.get("obj_type", "")}
                    for n in empties
                ]
                session["pending_action"] = {
                    "id": "act-" + time.strftime("%H%M%S"),
                    "type": "mark_nodes",
                    "params": {"targets": targets},
                    "confirm_hint": "回复「确认执行」开始改名，或「取消」放弃。",
                }
                reply["reply_text"] = (
                    f"找到 {len(empties)} 个空节点。"
                    f"将给它们的标题加 🗑[空] 前缀（可恢复，不会删内容）。"
                    f"这样你在 Wiki UI 里搜 🗑 就能一键圈出。回复「确认执行」或「取消」。"
                )
                reply["preview"] = {
                    "title": f"即将标记 {len(empties)} 个空节点",
                    "items": [
                        {"title": n["title"], "ok": True,
                         "detail": f"→ 🗑[空] {n['title']}"[:60]}
                        for n in empties
                    ],
                    "summary": f"共 {len(empties)} 个（可恢复）",
                }
                reply["pending_action"] = session["pending_action"]
                reply["status"] = "success"

        # ---- 按前缀清理 ----
        elif intent == "cleanup_prefix":
            # 从 query 里抽取前缀（优先方括号/引号/「」）
            prefix = _extract_prefix_from_query(query)
            if not prefix:
                reply["reply_text"] = (
                    "请告诉我前缀。例如：「按前缀删 [诊断]」或「删除所有 GitHub Trending 日报 开头的节点」。"
                )
                reply["status"] = "success"
            else:
                # 扫顶层匹配
                from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest
                matched = []
                page_token = None
                for _ in range(10):
                    b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50)
                    if page_token:
                        b.page_token(page_token)
                    r = client.wiki.v2.space_node.list(b.build())
                    if not r.success():
                        break
                    for it in (getattr(r.data, "items", None) or []):
                        t = getattr(it, "title", "") or ""
                        if t.startswith(prefix):
                            matched.append({
                                "title": t,
                                "node_token": it.node_token,
                                "obj_token": getattr(it, "obj_token", "") or "",
                                "obj_type": getattr(it, "obj_type", "") or "",
                            })
                    if not getattr(r.data, "has_more", False):
                        break
                    page_token = getattr(r.data, "page_token", None)
                    if not page_token:
                        break
                if not matched:
                    reply["reply_text"] = f"没有标题以 '{prefix}' 开头的顶层节点。"
                    reply["status"] = "success"
                else:
                    session["pending_action"] = {
                        "id": "act-" + time.strftime("%H%M%S"),
                        "type": "cleanup_prefix",
                        "params": {"prefix": prefix, "targets": matched},
                        "confirm_hint": "回复「确认执行」真删，或「取消」放弃。",
                    }
                    reply["reply_text"] = (
                        f"匹配到 {len(matched)} 个以 '{prefix}' 开头的顶层节点。"
                        f"⚠️ 确认后将连同子页**不可恢复**地删除。回复「确认执行」或「取消」。"
                    )
                    reply["preview"] = {
                        "title": f"即将删除 {len(matched)} 个节点",
                        "items": [{"title": m["title"], "ok": True,
                                   "detail": f"token={m['node_token'][:16]}…"} for m in matched],
                        "summary": f"前缀 '{prefix}'",
                    }
                    reply["pending_action"] = session["pending_action"]
                    reply["status"] = "success"

        # ---- 整理建议 ----
        elif intent == "suggest":
            nodes = wiki_agent.scan_wiki_tree(client, wiki_space_id)
            # 缓存扫描结果，复用
            cached_scan = session.get("last_scan") or {}
            if cached_scan and (int(time.time()) - cached_scan.get("ts", 0) < 300):
                scan = {"empty": cached_scan.get("empty", []),
                        "read_ok": 0, "skipped": [], "body_threshold": 8}
            else:
                scan = wiki_agent.detect_empty_nodes(client, tenant_token, wiki_space_id)
                session["last_scan"] = {"empty": scan["empty"], "ts": int(time.time())}

            # 先尝试豆包 LLM 版建议（基于真实树概览）
            overview = wiki_agent.summarize_tree_for_llm(nodes)
            ai_text = None
            try:
                from src.importers.ai_summarizer import AISummarizer
                ai_cfg = config_loader.get_ai_config()
                summarizer = AISummarizer(ai_cfg)
                ai_text = wiki_agent.ai_suggestions(
                    summarizer, overview, len(scan["empty"]), user_question=query
                )
            except Exception as e:
                logger.warning(f"AI suggestions 调用失败: {e}")

            # 规则版 fallback
            tips = wiki_agent.generate_suggestions(nodes, scan)

            if ai_text:
                reply["reply_text"] = f"Wiki 结构分析（AI）：\n{ai_text}"
            else:
                reply["reply_text"] = "Wiki 结构分析：\n" + "\n".join(f"• {t}" for t in tips)

            # 预览卡片：展示顶层目录概览
            items = []
            for f in overview["top_folders"][:15]:
                samples = "、".join(f["sample_titles"][:3]) if f["sample_titles"] else ""
                detail = f"{f['node_count']} 个子节点" + (f" · 样例: {samples}" if samples else "")
                items.append({"title": f["title"], "ok": True, "detail": detail[:80]})
            # 追加规则结论
            for t in tips[:5]:
                items.append({"title": "💡 " + t, "ok": True, "detail": ""})
            reply["preview"] = {
                "title": "整理建议",
                "items": items,
                "summary": f"共 {overview['total_nodes']} 节点 · 顶层 {overview['top_level_count']} · 空 {len(scan['empty'])}",
            }
            reply["status"] = "success"

        # ---- 搜 Wiki（默认） ----
        else:
            sources = _search_wiki_titles(client, wiki_space_id, query, limit=5)
            # 如果是长句 + 整句 substring 匹配找不到，尝试把长句切成关键词再搜
            if not sources and len(query) > 6:
                import re as _re
                # 去掉常见停用词，抽 2-6 字的名词短语
                stop = set("你我他的了是在有没和与或吗呢啊请帮给让把对就也都还这那么什么怎么能可以应该如何".split())
                tokens = [t for t in _re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z]{2,20}", query)
                          if t not in stop and len(t) >= 2]
                seen = set()
                merged: List[Dict[str, Any]] = []
                for tok in tokens[:5]:
                    hits = _search_wiki_titles(client, wiki_space_id, tok, limit=3)
                    for h in hits:
                        if h["node_token"] in seen:
                            continue
                        seen.add(h["node_token"])
                        merged.append(h)
                sources = merged[:5]
                reply["sources"] = sources

            if not sources:
                # 长句且无命中 → 主动引导走 suggest
                if len(query) > 12:
                    reply["reply_text"] = (
                        f"没找到直接相关的 Wiki 页面。你这个问题更像是在让我分析知识库结构，"
                        f"我重新路由到「整理建议」了——再发一句"
                        f"「给点整理建议」或「你觉得我的 Wiki 怎么整理」，我就扫全树给你分析。"
                    )
                else:
                    reply["reply_text"] = (
                        f"Wiki 里没找到与「{query}」相关的页面。"
                        f"换个说法试试，或说「给点整理建议」让我扫全树分析。"
                    )
                reply["status"] = "success"
            else:
                from src.importers.ai_summarizer import AISummarizer
                ai_cfg = config_loader.get_ai_config()
                summarizer = AISummarizer(ai_cfg)
                if summarizer.configured():
                    lines = "\n".join(f"- {s['title']}  {s['url']}" for s in sources)
                    ai = summarizer._chat_json(
                        tag=f"agent[{query[:20]}]",
                        user_prompt=(
                            f"用户询问: {query}\n\n"
                            f"检索到以下相关 Wiki 页面：\n{lines}\n\n"
                            f"用中文给出 200 字以内的帮助回答，引用具体页名，"
                            f"不要编造不存在的内容。返回 JSON: "
                            '{"one_liner": "一句话答复", "detail": "展开"}'
                        ),
                        timeout=60,
                        retries=1,
                    )
                    if ai:
                        reply["reply_text"] = ai.get("detail") or ai.get("one_liner") or ""
                    else:
                        reply["reply_text"] = f"找到 {len(sources)} 个相关页面（AI 摘要失败，直接看下方链接）。"
                else:
                    reply["reply_text"] = f"找到 {len(sources)} 个相关页面（未配置豆包，仅列标题）。"
                reply["status"] = "success"

        # 追加到 history
        session["history"].append({"role": "user", "content": query, "ts": int(time.time())})
        session["history"].append({
            "role": "agent",
            "content": reply["reply_text"],
            "intent": intent,
            "ts": int(time.time()),
        })
        # 限制 history 长度
        if len(session["history"]) > 40:
            session["history"] = session["history"][-40:]
        wiki_agent.save_session(session)

        # 附带最近 history 给 UI 展示
        reply["history"] = session["history"][-10:]

    except Exception as e:
        logger.exception("agent-chat 异常")
        reply["message"] = f"异常: {e}"

    if json_output:
        print(json.dumps(reply, ensure_ascii=False))
    else:
        print(f"\n[Agent · intent={reply.get('intent')}]")
        print(reply.get("reply_text") or reply.get("message") or "")


def _extract_prefix_from_query(query: str) -> str:
    """从用户 query 中抽取前缀：支持 []、""、「」、''。找不到则返回空串。"""
    import re as _re
    for pattern in [r"\[([^\]]+)\]", r"「([^」]+)」", r"\"([^\"]+)\"", r"'([^']+)'"]:
        m = _re.search(pattern, query)
        if m:
            return m.group(1).strip()
    # 回退：去掉常见动词/助词后取剩余前面一段
    cleaned = _re.sub(r"(按前缀|前缀|删除|删|清理|批量|所有|开头的|节点|wiki|Wiki|把|请|帮我|为我|所有以|以)", "", query)
    cleaned = cleaned.strip()
    return cleaned if 1 <= len(cleaned) <= 40 else ""


def _search_wiki_titles(client, wiki_space_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """轻量递归搜索 Wiki 标题（ListSpaceNode，不走 v1 search）。"""
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest
    q = query.lower()
    hits: List[Dict[str, Any]] = []

    def _scan(parent: Optional[str], depth: int):
        if len(hits) >= limit * 3 or depth > 4:
            return
        page_token = None
        for _ in range(5):
            b = ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(50)
            if parent:
                b.parent_node_token(parent)
            if page_token:
                b.page_token(page_token)
            r = client.wiki.v2.space_node.list(b.build())
            if not r.success():
                return
            for it in (getattr(r.data, "items", None) or []):
                t = getattr(it, "title", "") or ""
                if q in t.lower():
                    hits.append({
                        "title": t,
                        "node_token": it.node_token,
                        "url": f"https://open.feishu.cn/wiki/{it.node_token}",
                    })
                    if len(hits) >= limit * 3:
                        return
                if getattr(it, "has_child", False) and depth < 4:
                    _scan(it.node_token, depth + 1)
                    if len(hits) >= limit * 3:
                        return
            if not getattr(r.data, "has_more", False):
                break
            page_token = getattr(r.data, "page_token", None)
            if not page_token:
                break

    _scan(None, 0)

    def _score(title: str) -> int:
        t = title.lower()
        if t == q: return 100
        if t.startswith(q): return 50
        return 10
    hits.sort(key=lambda h: _score(h["title"]), reverse=True)
    return hits[:limit]


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

    # import-reddit 子命令
    p_import_reddit = subparsers.add_parser("import-reddit", help="Reddit AI 日报 → 飞书")
    p_import_reddit.add_argument("--force", action="store_true", help="忽略缓存，重新调用豆包摘要")

    # manage 子命令
    p_manage = subparsers.add_parser("manage", help="管理飞书资源")
    p_manage.add_argument("resource", choices=["email", "messages", "calendar", "contacts"])
    p_manage.add_argument("--chat-id", dest="chat_id", help="IM 群聊 ID（管理消息时必填）")
    p_manage.add_argument("--query", help="搜索关键词（管理联系人时可用）")

    # chat 子命令：对话助手 MVP
    p_chat = subparsers.add_parser("chat", help="对话助手：关键词搜 Wiki → 豆包回答")
    p_chat.add_argument("query", nargs="?", default="", help="查询关键词")
    p_chat.add_argument("--limit", type=int, default=5, help="搜多少个相关页面")
    p_chat.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

    # cleanup-wiki 子命令：批量删除 Wiki 节点
    p_cleanup = subparsers.add_parser("cleanup-wiki", help="按前缀或 token 批量删 Wiki 节点（默认 dry-run）")
    p_cleanup.add_argument("--prefix", required=False, default="", help="要匹配的节点标题前缀")
    p_cleanup.add_argument("--node-tokens", dest="node_tokens", required=False, default="",
                           help="逗号分隔的 node_token 列表（优先于 --prefix）")
    p_cleanup.add_argument("--confirm", action="store_true", help="真删（不加则仅预览）")
    p_cleanup.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

    # scan-empty-wiki 子命令：找空节点
    p_scan = subparsers.add_parser("scan-empty-wiki", help="扫 Wiki 树找出正文为空的节点")
    p_scan.add_argument("--threshold", type=int, default=8, help="正文字数阈值（≤ 视为空）")
    p_scan.add_argument("--max", type=int, default=500, help="最多扫多少个节点")
    p_scan.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

    # agent-chat 子命令：Wiki 管家对话 Agent
    p_agent = subparsers.add_parser("agent-chat", help="对话 Agent · Wiki 管家")
    p_agent.add_argument("--query", default="", help="用户问题 / 指令")
    p_agent.add_argument("--session", default="", help="会话 id（不传则新建）")
    p_agent.add_argument("--reset", action="store_true", help="重置会话")
    p_agent.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

    # schedule 子命令（守护进程）
    subparsers.add_parser("schedule", help="启动定时任务调度器")
    subparsers.add_parser("schedule-status", help="查询调度器状态（输出 JSON）")

    args = parser.parse_args()

    if args.command == "organize":
        cmd_organize(args)
    elif args.command == "import-github":
        cmd_import_github(args)
    elif args.command == "import-reddit":
        cmd_import_reddit(args)
    elif args.command == "manage":
        cmd_manage(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "schedule-status":
        cmd_schedule_status(args)
    elif args.command == "chat":
        cmd_chat(args)
    elif args.command == "cleanup-wiki":
        cmd_cleanup_wiki(args)
    elif args.command == "scan-empty-wiki":
        cmd_scan_empty_wiki(args)
    elif args.command == "agent-chat":
        cmd_agent_chat(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
