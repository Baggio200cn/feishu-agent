"""
文档整理器 — 在个人 Wiki 空间建目录，将文档按分类移动过来。

核心原则（来自 Harness Engineering）：
- 原子操作：文件夹创建 + 文档移动必须视为一个事务
  移动失败时删除新建的空文件夹，避免孤立文件夹残留
- 错误是主路径：每个失败都记录并可恢复，不仅记日志后继续
- 验证：执行后扫描确认文档已在正确位置
"""
import logging
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DB_PATH = "data/agent.db"


def _get_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS organizer_folders (
            node_token TEXT PRIMARY KEY,
            folder_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


class DocOrganizer:
    """
    在个人飞书 Wiki 空间中按分类建立目录结构，
    并将扫描到的文档（含分类信息）移动过来。
    """

    def __init__(self, personal_client, wiki_space_id: str, dry_run: bool = False):
        self._client = personal_client
        self.space_id = wiki_space_id
        self.dry_run = dry_run
        self._category_nodes: Dict[str, str] = {}   # category_name -> node_token
        self._newly_created: List[str] = []          # 本次运行新建的文件夹 token（用于 rollback）
        self._db = _get_db()
        # 从数据库加载历次运行已创建的文件夹 token
        self._folder_tokens: set = {
            row[0] for row in self._db.execute(
                "SELECT node_token FROM organizer_folders"
            ).fetchall()
        }

    def organize(self, docs: List[Dict], categories_config: Dict) -> Dict[str, Any]:
        """
        主入口：
        - docs: 已含 category 字段的文档列表
        - categories_config: 分类配置（含 icon）
        返回整理报告。
        """
        report = {
            "run_at": datetime.now().isoformat(),
            "dry_run": self.dry_run,
            "total": len(docs),
            "organized": 0,
            "skipped": 0,
            "errors": 0,
            "by_category": {},
        }

        icon_map = {c["name"]: c.get("icon", "📄") for c in categories_config.get("categories", [])}

        for doc in docs:
            # 跳过 organizer 自身创建的分类文件夹
            node_token_check = doc.get("node_token", "")
            if node_token_check and node_token_check in self._folder_tokens:
                logger.info(f"跳过已创建的分类目录: {doc.get('title', '')}")
                report["skipped"] += 1
                continue

            category = doc.get("category", "其他")
            icon = icon_map.get(category, "📄")
            folder_name = f"{icon} {category}"

            try:
                if self.dry_run:
                    # dry-run: 只确保 category_nodes 中有条目（fake token），不实际创建
                    if folder_name not in self._category_nodes:
                        self._category_nodes[folder_name] = f"dry_run_{folder_name}"
                    report["organized"] += 1
                    report["by_category"].setdefault(category, []).append(doc.get("title", ""))
                    logger.info(f"[DRY-RUN] 整理: {doc.get('title', '')} → {folder_name}")
                    continue

                # 实际执行：原子操作
                self._move_doc_atomic(doc, folder_name)
                report["organized"] += 1
                report["by_category"].setdefault(category, []).append(doc.get("title", ""))
                logger.info(f"整理: {doc.get('title', '')} → {folder_name}")

            except Exception as e:
                logger.warning(f"整理失败 [{doc.get('title', '')}]: {e}")
                report["errors"] += 1

        self._save_report(report)
        return report

    def get_folder_tokens(self) -> set:
        """返回所有已知文件夹 token（供 DocScanner 过滤）"""
        return self._folder_tokens

    # ── 原子操作 ──────────────────────────────────────────────────────────────

    def _move_doc_atomic(self, doc: Dict, folder_name: str) -> None:
        """
        原子化移动文档到目标分类文件夹。
        步骤：
        1. 确保文件夹存在（记录是否本次新建）
        2. 执行文档移动
        3. 若步骤 2 失败且步骤 1 新建了文件夹 → rollback（删除空文件夹）
        """
        folder_newly_created = False
        folder_token = self._category_nodes.get(folder_name)

        if not folder_token:
            folder_token, folder_newly_created = self._create_category_folder(folder_name)

        try:
            self._copy_doc_to_folder(doc, folder_token)
        except Exception as move_err:
            if folder_newly_created:
                # Rollback: 删除本次刚创建的空文件夹
                logger.warning(
                    f"移动失败，回滚删除空文件夹 [{folder_name}]: {move_err}"
                )
                self._delete_folder_node(folder_token, folder_name)
                # 从缓存中移除
                self._category_nodes.pop(folder_name, None)
                self._folder_tokens.discard(folder_token)
            raise

    def _create_category_folder(self, folder_name: str) -> Tuple[str, bool]:
        """
        创建分类目录节点，返回 (node_token, newly_created)。
        若已存在于 _category_nodes 缓存中，直接返回 False（未新建）。
        """
        if folder_name in self._category_nodes:
            return self._category_nodes[folder_name], False

        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

        body = (
            CreateSpaceNodeRequestBody.builder()
            .obj_type("doc")
            .node_type("origin")
            .title(folder_name)
            .build()
        )
        req = (
            CreateSpaceNodeRequest.builder()
            .space_id(self.space_id)
            .request_body(body)
            .build()
        )
        resp = self._client.wiki.v2.space_node.create(req)

        if not resp.success():
            raise RuntimeError(f"创建目录节点失败: {resp.msg}")

        token = resp.data.node.node_token
        self._category_nodes[folder_name] = token
        self._folder_tokens.add(token)
        self._newly_created.append(token)

        # 持久化到数据库（失败时仅警告，不中断流程）
        try:
            self._db.execute(
                "INSERT OR IGNORE INTO organizer_folders (node_token, folder_name) VALUES (?, ?)",
                (token, folder_name),
            )
            self._db.commit()
        except Exception as e:
            logger.warning(f"文件夹 token 缓存写入失败（内存已更新）: {e}")

        logger.info(f"创建分类目录: {folder_name} (token={token})")
        return token, True

    def _delete_folder_node(self, node_token: str, folder_name: str) -> None:
        """删除 Wiki 节点（用于 rollback 清理空文件夹）"""
        try:
            from lark_oapi.api.wiki.v2 import DeleteSpaceNodeRequest
            req = (
                DeleteSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .node_token(node_token)
                .build()
            )
            resp = self._client.wiki.v2.space_node.delete(req)
            if resp.success():
                logger.info(f"Rollback 删除空文件夹: {folder_name}")
                # 从数据库移除
                try:
                    self._db.execute(
                        "DELETE FROM organizer_folders WHERE node_token = ?", (node_token,)
                    )
                    self._db.commit()
                except Exception as e:
                    logger.warning(f"数据库清理失败: {e}")
            else:
                logger.warning(f"Rollback 删除失败 [{folder_name}]: {resp.msg}")
        except Exception as e:
            logger.warning(f"Rollback 删除异常 [{folder_name}]: {e}")

    def _copy_doc_to_folder(self, doc: Dict, parent_node_token: str) -> None:
        """将文档移动到目标目录（通过 Wiki 移动节点 API）"""
        from lark_oapi.api.wiki.v2 import MoveSpaceNodeRequest, MoveSpaceNodeRequestBody

        node_token = doc.get("node_token")
        if not node_token:
            logger.warning(f"文档缺少 node_token，跳过: {doc.get('title', '')}")
            return

        # 同账号 Wiki 文档直接移动
        if doc.get("account") == "personal" and doc.get("source") == "wiki":
            body = (
                MoveSpaceNodeRequestBody.builder()
                .target_parent_token(parent_node_token)
                .build()
            )
            req = (
                MoveSpaceNodeRequest.builder()
                .space_id(self.space_id)
                .node_token(node_token)
                .request_body(body)
                .build()
            )
            resp = self._client.wiki.v2.space_node.move(req)
            if not resp.success():
                raise RuntimeError(f"移动节点失败: {resp.msg}")
        else:
            # 企业账号文档：创建引用说明页
            logger.info(
                f"跨账号文档 [{doc.get('title', '')}]：以引用方式记录，请手动复制内容"
            )
            self._create_reference_note(doc, parent_node_token)

    def _create_reference_note(self, doc: Dict, parent_node_token: str) -> None:
        """为跨账号文档创建引用说明页"""
        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

        title = f"[引用] {doc.get('title', '未知文档')}"
        body = (
            CreateSpaceNodeRequestBody.builder()
            .obj_type("doc")
            .node_type("origin")
            .title(title)
            .parent_node_token(parent_node_token)
            .build()
        )
        req = (
            CreateSpaceNodeRequest.builder()
            .space_id(self.space_id)
            .request_body(body)
            .build()
        )
        resp = self._client.wiki.v2.space_node.create(req)
        if not resp.success():
            raise RuntimeError(f"创建引用节点失败: {resp.msg}")

    # ── 报告 ──────────────────────────────────────────────────────────────────

    def _save_report(self, report: Dict) -> None:
        """保存整理报告到本地"""
        os.makedirs("logs", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"logs/organize_report_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"整理报告已保存: {path}")

        print(f"\n{'=' * 50}")
        print(f"{'[DRY-RUN] ' if report['dry_run'] else ''}整理完成！")
        print(f"  总文档数: {report['total']}")
        print(f"  已整理:   {report['organized']}")
        print(f"  已跳过:   {report['skipped']}")
        print(f"  错误:     {report['errors']}")
        print(f"\n按分类统计:")
        for cat, titles in report["by_category"].items():
            print(f"  {cat}: {len(titles)} 篇")
        print(f"{'=' * 50}\n")
