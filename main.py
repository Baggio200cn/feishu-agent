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

    # --limit: dry-run 默认抽样 50 个（快预览），真执行默认 0（无限制）
    limit = getattr(args, "limit", None)
    if limit is None:
        limit = 50 if args.dry_run else 0

    all_docs = []

    # 扫描个人账号（Scanner 自带时间预算 + 节点上限，不会跑飞）
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

    # AI 自动分类（limit>0 时只分类抽样的前 N 个）
    total_found = len(all_docs)
    sampled = all_docs if (limit <= 0 or total_found <= limit) else all_docs[:limit]
    if sampled is not all_docs:
        logger.info(
            f"=== AI 分类（共扫到 {total_found} 篇，本次抽样 {len(sampled)} 篇）==="
        )
    else:
        logger.info(f"=== AI 分类（共 {total_found} 篇）===")

    categorizer = AICategorizer(config_loader.get_ai_config(), categories_cfg)
    sampled = categorizer.categorize_batch(sampled)

    # 未分类的其余文档标为"其他"，保证后续 organize 阶段不会 KeyError
    sampled_tokens = {id(d) for d in sampled}
    for d in all_docs:
        if id(d) not in sampled_tokens:
            d["category"] = d.get("category") or "其他（未分类）"

    # 打印分类预览
    from collections import Counter
    cat_counts = Counter(d["category"] for d in all_docs)
    print("\n分类预览:")
    for cat, cnt in cat_counts.most_common():
        print(f"  {cat}: {cnt} 篇")

    if args.dry_run:
        # dry-run 也写一份报告文件，方便审计 + 给 diag 读取
        from datetime import datetime as _dt
        os.makedirs("logs", exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        preview_report = {
            "run_at": _dt.now().isoformat(),
            "dry_run": True,
            "total": total_found,
            "sampled": len(sampled),
            "by_category": {
                cat: [d.get("title", "") for d in all_docs if d.get("category") == cat]
                for cat in cat_counts
            },
        }
        with open(f"logs/organize_report_{ts}.json", "w", encoding="utf-8") as f:
            json.dump(preview_report, f, ensure_ascii=False, indent=2)
        if sampled is not all_docs:
            print(f"\n[DRY-RUN] 抽样 {len(sampled)}/{total_found} 个分类；剩余被标为 '其他（未分类）'")
            print("  想全量分类（慢）：python main.py organize --dry-run --limit 0")
        else:
            print("\n[DRY-RUN] 以上为全量预览，未实际移动任何文档")
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
    对话助手升级版 = Agent 模式。

    两阶段:
      stage=plan     输入自然语言，返回 {intent, params, preview, action_spec}
      stage=execute  输入 action_spec（从 plan 阶段拿），真执行

    用法:
      python main.py chat "删除空文件"                     # 等价 --stage plan
      python main.py chat "..." --stage plan --json
      python main.py chat --stage execute --action '{...}' --json
    """
    from src.agent.agent_brain import AgentBrain
    from src.agent import wiki_tools

    stage = getattr(args, "stage", None) or "plan"
    json_output = bool(getattr(args, "json", False))

    creds = config_loader.load_credentials()
    personal = creds["accounts"]["personal"]
    wiki_space_id = personal.get("wiki_space_id", "")
    app_id = personal.get("app_id", "")
    app_secret = personal.get("app_secret", "")

    result: Dict[str, Any] = {"stage": stage, "status": "error", "message": ""}

    if not wiki_space_id or wiki_space_id == "your_personal_wiki_space_id":
        result["message"] = "飞书 Wiki space_id 未配置"
        _emit_chat_result(result, json_output)
        return

    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")
    ai_cfg = config_loader.get_ai_config()
    brain = AgentBrain(ai_cfg)

    if stage == "plan":
        _agent_stage_plan(args, client, wiki_space_id, brain, result, json_output)
    elif stage == "execute":
        _agent_stage_execute(args, client, wiki_space_id, app_id, app_secret, brain, result, json_output)
    else:
        result["message"] = f"未知 stage={stage}（plan|execute）"
        _emit_chat_result(result, json_output)


def _agent_stage_plan(args, client, wiki_space_id, brain, result, json_output):
    """Plan 阶段：识别意图 + dry-run 拿到预览列表，返回计划给 UI"""
    from src.agent import wiki_tools

    query = (getattr(args, "query", "") or "").strip()
    if not query:
        result["message"] = "请传入自然语言指令"
        _emit_chat_result(result, json_output)
        return

    # 1. 拿顶层节点标题作上下文（让 AI 判断更准）
    top_titles: List[str] = []
    try:
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest
        r = client.wiki.v2.space_node.list(
            ListSpaceNodeRequest.builder().space_id(wiki_space_id).page_size(20).build()
        )
        if r.success():
            top_titles = [getattr(it, "title", "") or "" for it in (getattr(r.data, "items", None) or [])]
    except Exception:
        pass

    # 2. 豆包识别意图
    plan = brain.parse_intent(query, context_titles=top_titles)
    result["query"] = query
    result["intent"] = plan["intent"]
    result["reasoning"] = plan["reasoning"]
    result["dangerous"] = plan["dangerous"]
    result["user_facing_plan"] = plan["user_facing_plan"]
    params = plan["params"] or {}

    # 3. 根据 intent 做 dry-run 预览
    intent = plan["intent"]
    SKIP = ("github专区", "reddit专区", "GitHub Trending 日报", "Reddit AI 日报", "[诊断]")

    if intent == "search":
        q = (params.get("query") or query).strip()
        matches = wiki_tools.search_wiki_by_title(client, wiki_space_id, q, limit=5)
        result["preview"] = {
            "type": "search",
            "count": len(matches),
            "items": [{"title": m["title"], "url": f"https://open.feishu.cn/wiki/{m['node_token']}"} for m in matches],
        }
        # search 无需确认
        result["action_spec"] = None
        result["status"] = "success"
        result["message"] = f"搜到 {len(matches)} 个相关页面"

    elif intent in ("find_empty", "delete_empty"):
        res = wiki_tools.find_empty_nodes(client, wiki_space_id, skip_prefixes=SKIP, max_scan=200)
        empty = res["empty"]
        result["preview"] = {
            "type": "empty_nodes",
            "scanned": res["scanned"],
            "count": len(empty),
            "items": [{"title": e["title"], "node_token": e["node_token"], "block_count": e["block_count"]} for e in empty],
        }
        if intent == "find_empty" or not empty:
            result["action_spec"] = None
            result["status"] = "success"
            result["message"] = f"扫 {res['scanned']} 个节点，{len(empty)} 个空"
        else:
            result["action_spec"] = {
                "action": "delete_nodes",
                "tokens": [e["node_token"] for e in empty],
                "descriptions": [e["title"] for e in empty],
            }
            result["status"] = "success"
            result["message"] = f"找到 {len(empty)} 个空节点待删除，请确认"

    elif intent == "delete_by_prefix":
        prefix = (params.get("prefix") or "").strip()
        if not prefix:
            result["status"] = "error"
            result["message"] = "AI 没提取出前缀，请改说得更具体"
            _emit_chat_result(result, json_output)
            return
        # 复用 cleanup-wiki 的 dry-run 预览路径
        matched = wiki_tools.list_all_nodes(client, wiki_space_id, max_nodes=500)
        matched = [n for n in matched if n["title"].startswith(prefix)]
        result["preview"] = {
            "type": "prefix_match",
            "prefix": prefix,
            "count": len(matched),
            "items": [{"title": n["title"], "node_token": n["node_token"]} for n in matched],
        }
        if not matched:
            result["action_spec"] = None
            result["status"] = "success"
            result["message"] = f"没有标题以 '{prefix}' 开头的节点"
        else:
            result["action_spec"] = {
                "action": "delete_nodes",
                "tokens": [n["node_token"] for n in matched],
                "descriptions": [n["title"] for n in matched],
            }
            result["status"] = "success"
            result["message"] = f"匹配到 {len(matched)} 个节点，确认后删除"

    elif intent == "organize_preview":
        result["preview"] = {
            "type": "hint",
            "text": "建议直接在主界面点「Wiki 整理工作流 → 预览分类结果」，会生成分类报告到 logs/organize_report_*.json。Agent 暂不直接触发 organize（扫描 + AI 分类较慢，放在独立按钮更合适）。",
        }
        result["action_spec"] = None
        result["status"] = "success"
        result["message"] = "已给出操作建议"

    else:
        # unknown → 降级搜索
        matches = wiki_tools.search_wiki_by_title(client, wiki_space_id, query, limit=5)
        result["preview"] = {
            "type": "search",
            "count": len(matches),
            "items": [{"title": m["title"], "url": f"https://open.feishu.cn/wiki/{m['node_token']}"} for m in matches],
        }
        result["action_spec"] = None
        result["status"] = "partial"
        result["message"] = "未精确识别意图，按关键词搜索处理"

    _emit_chat_result(result, json_output)


def _agent_stage_execute(args, client, wiki_space_id, app_id, app_secret, brain, result, json_output):
    """Execute 阶段：拿到 action_spec 真执行"""
    from src.agent import wiki_tools

    spec_raw = getattr(args, "action", "") or ""
    if not spec_raw:
        result["message"] = "execute 阶段需 --action '{JSON}'"
        _emit_chat_result(result, json_output)
        return

    try:
        spec = json.loads(spec_raw)
    except json.JSONDecodeError as e:
        result["message"] = f"--action 不是合法 JSON: {e}"
        _emit_chat_result(result, json_output)
        return

    action = spec.get("action")
    result["executed_action"] = action
    deleted: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []

    if action == "delete_nodes":
        tokens = spec.get("tokens") or []
        descs = spec.get("descriptions") or [""] * len(tokens)
        if not tokens:
            result["message"] = "tokens 为空"
            _emit_chat_result(result, json_output)
            return
        for i, tk in enumerate(tokens):
            title = descs[i] if i < len(descs) else ""
            r = wiki_tools.delete_wiki_node(app_id, app_secret, wiki_space_id, tk)
            if r["ok"]:
                deleted.append({"title": title, "node_token": tk})
                logger.info(f"✅ Agent 删除: {title}")
            else:
                failed.append({"title": title, "node_token": tk, "error": r["error"]})
                logger.warning(f"❌ Agent 删除失败: {title} — {r['error']}")

        result["deleted"] = deleted
        result["failed"] = failed
        result["status"] = "success" if not failed else "partial"
        raw_msg = f"成功删除 {len(deleted)} 个，失败 {len(failed)} 个"
        # 让豆包写个友好版
        result["message"] = brain.summarize_result("delete_nodes", {
            "deleted": len(deleted),
            "failed": len(failed),
            "titles_sample": [d["title"] for d in deleted[:5]],
        }) or raw_msg
    else:
        result["message"] = f"暂不支持的 action: {action}"

    _emit_chat_result(result, json_output)


def _emit_chat_result(result: Dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"\n[Agent {result.get('stage','?')}] status={result.get('status')}")
    if result.get("intent"):
        print(f"  意图: {result['intent']}  ({result.get('reasoning','')})")
    if result.get("user_facing_plan"):
        print(f"  计划: {result['user_facing_plan']}")
    if result.get("preview"):
        p = result["preview"]
        if p.get("type") == "search":
            print(f"\n  搜到 {p.get('count', 0)} 个相关页面：")
            for it in p.get("items", []):
                print(f"    • {it['title']}  {it.get('url','')}")
        elif p.get("type") in ("empty_nodes", "prefix_match"):
            n = p.get("count", 0)
            label = "空节点" if p["type"] == "empty_nodes" else f"前缀 '{p.get('prefix')}' 匹配"
            print(f"\n  {label}：{n} 个")
            for it in p.get("items", [])[:20]:
                extra = f" (blocks={it.get('block_count')})" if "block_count" in it else ""
                print(f"    - {it['title']}{extra}")
        elif p.get("type") == "hint":
            print(f"\n  {p.get('text', '')}")
    if result.get("action_spec"):
        print(f"\n  待确认执行的动作: {result['action_spec'].get('action')}")
        print(f"  执行命令: python main.py chat --stage execute --action '<json>' --json")
    if result.get("deleted") is not None:
        print(f"\n  已删除 {len(result['deleted'])} 个，失败 {len(result['failed'])} 个")
        for d in result["deleted"][:10]:
            print(f"    ✅ {d['title']}")
        for f in result["failed"][:10]:
            print(f"    ❌ {f['title']} — {f['error']}")
    if result.get("message"):
        print(f"\n  {result['message']}")


def cmd_cleanup_wiki(args):
    """
    Wiki 清理：按标题前缀匹配批量删除空间下的节点。
    **危险操作**：默认 --dry-run，只有显式加 --confirm 才真删。

    用法:
      python main.py cleanup-wiki --prefix "[诊断]"                    # 预览（安全）
      python main.py cleanup-wiki --prefix "[诊断]" --confirm          # 真删
      python main.py cleanup-wiki --prefix "GitHub Trending 日报 2025" --confirm
      python main.py cleanup-wiki --json                                # UI 调用
    """
    import argparse as _argparse

    prefix = (getattr(args, "prefix", "") or "").strip()
    confirm = bool(getattr(args, "confirm", False))
    json_output = bool(getattr(args, "json", False))

    result: Dict[str, Any] = {
        "prefix": prefix,
        "dry_run": not confirm,
        "matched": [],
        "deleted": [],
        "failed": [],
        "status": "error",
        "message": "",
    }

    if not prefix:
        result["message"] = "必须指定 --prefix，否则拒绝（避免误删整个空间）"
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

        # 1. 扫顶层节点，按 title 前缀匹配
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

        matched_nodes: List[Dict[str, str]] = []
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
                    })
            if not getattr(resp.data, "has_more", False):
                break
            page_token = getattr(resp.data, "page_token", None)
            if not page_token:
                break

        result["matched"] = matched_nodes

        if not matched_nodes:
            result["status"] = "success"
            result["message"] = f"没有标题以 '{prefix}' 开头的节点"
            _emit_cleanup_result(result, json_output)
            return

        if not confirm:
            result["status"] = "dry_run"
            result["message"] = (
                f"预览模式：匹配到 {len(matched_nodes)} 个节点。"
                f"加 --confirm 才会真删。"
            )
            _emit_cleanup_result(result, json_output)
            return

        # 2. 真删 —— lark-oapi 1.5.3 没包 DeleteSpaceNode，用 raw REST
        import requests as _requests

        personal_cfg = creds["accounts"]["personal"]
        auth_resp = _requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": personal_cfg.get("app_id", ""),
                "app_secret": personal_cfg.get("app_secret", ""),
            },
            timeout=10,
        )
        auth_json = auth_resp.json() if auth_resp.status_code == 200 else {}
        tenant_token = auth_json.get("tenant_access_token", "")
        if not tenant_token:
            result["message"] = f"获取 tenant_access_token 失败: {auth_json}"
            _emit_cleanup_result(result, json_output)
            return

        headers = {"Authorization": f"Bearer {tenant_token}"}
        for node in matched_nodes:
            url = (
                f"https://open.feishu.cn/open-apis/wiki/v2/spaces/"
                f"{wiki_space_id}/nodes/{node['node_token']}"
            )
            try:
                r = _requests.delete(url, headers=headers, timeout=15)
                if r.status_code == 200 and r.json().get("code", -1) == 0:
                    result["deleted"].append(node)
                    logger.info(f"✅ 已删除: {node['title']}")
                else:
                    err = f"HTTP {r.status_code} {r.text[:200]}"
                    result["failed"].append({**node, "error": err})
                    logger.warning(f"删除失败 [{node['title']}]: {err}")
            except Exception as e:
                result["failed"].append({**node, "error": str(e)})
                logger.exception(f"删除异常 [{node['title']}]")

        result["status"] = "success" if not result["failed"] else "partial"
        result["message"] = f"删除 {len(result['deleted'])} 个，失败 {len(result['failed'])} 个"
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
    prefix = result["prefix"]
    print(f"\n清理目标前缀：'{prefix}'")
    print(f"匹配到 {len(result['matched'])} 个节点:")
    for n in result["matched"]:
        marker = "✅" if n in result.get("deleted", []) else (
            "❌" if any(f["node_token"] == n["node_token"] for f in result.get("failed", [])) else "·"
        )
        print(f"  {marker} {n['title']}  (token={n['node_token']})")
    if result["dry_run"]:
        print(f"\n[DRY-RUN] {result['message']}")
    else:
        print(f"\n{result['message']}")


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
    p_organize.add_argument(
        "--limit", type=int, default=None,
        help="AI 分类的文档数上限（dry-run 默认 50，真执行默认 0=全量）"
    )

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

    # chat 子命令：Agent 两阶段（plan → execute）
    p_chat = subparsers.add_parser("chat", help="对话 Agent：识别意图 → 预览计划 → 确认后执行")
    p_chat.add_argument("query", nargs="?", default="", help="自然语言指令（plan 阶段需要）")
    p_chat.add_argument("--stage", choices=["plan", "execute"], default="plan",
                        help="plan=出计划（默认），execute=拿计划真执行")
    p_chat.add_argument("--action", default="", help="execute 阶段的 action_spec JSON 字符串")
    p_chat.add_argument("--limit", type=int, default=5, help="搜索/预览条目上限")
    p_chat.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

    # cleanup-wiki 子命令：批量删除 Wiki 节点
    p_cleanup = subparsers.add_parser("cleanup-wiki", help="按前缀批量删 Wiki 节点（默认 dry-run）")
    p_cleanup.add_argument("--prefix", required=False, default="", help="要匹配的节点标题前缀")
    p_cleanup.add_argument("--confirm", action="store_true", help="真删（不加则仅预览）")
    p_cleanup.add_argument("--json", action="store_true", help="输出 JSON 供 UI 调用")

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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
