"""
文档整理器 — 在个人 Wiki 空间建目录，将文档按分类复制/移动过来
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DocOrganizer:
    """
    在个人飞书 Wiki 空间中按分类建立目录结构，
    并将扫描到的文档（含分类信息）复制过来。
    """

    def __init__(self, personal_client, wiki_space_id: str, dry_run: bool = False):
        self._client = personal_client
        self.space_id = wiki_space_id
        self.dry_run = dry_run
        self._category_nodes: Dict[str, str] = {}  # category_name -> node_token

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
            category = doc.get("category", "其他")
            icon = icon_map.get(category, "📄")
            folder_name = f"{icon} {category}"

            try:
                node_token = self._ensure_category_folder(folder_name)
                if not self.dry_run:
                    self._copy_doc_to_folder(doc, node_token)
                report["organized"] += 1
                report["by_category"].setdefault(category, []).append(doc.get("title", ""))
                logger.info(f"{'[DRY-RUN] ' if self.dry_run else ''}整理: {doc.get('title', '')} → {folder_name}")
            except Exception as e:
                logger.warning(f"整理失败 [{doc.get('title', '')}]: {e}")
                report["errors"] += 1

        self._save_report(report)
        return report

    def _ensure_category_folder(self, folder_name: str) -> str:
        """确保分类目录（Wiki 节点）存在，返回节点 token"""
        if folder_name in self._category_nodes:
            return self._category_nodes[folder_name]

        if self.dry_run:
            fake_token = f"dry_run_{folder_name}"
            self._category_nodes[folder_name] = fake_token
            return fake_token

        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, CreateSpaceNodeRequestBody

        # obj_type="docx" 创建一个空文档节点作为分类目录
        # node_type="origin" 表示新建节点（非快捷方式）
        body = (
            CreateSpaceNodeRequestBody.builder()
            .obj_type("docx")
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
        logger.info(f"创建分类目录: {folder_name} (token={token})")
        return token

    def _copy_doc_to_folder(self, doc: Dict, parent_node_token: str) -> None:
        """将文档复制到目标目录（通过 Wiki 移动节点 API）"""
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
            # 企业账号文档：创建新节点，内容暂时以链接形式记录（跨账号复制内容需要额外授权）
            logger.info(f"跨账号文档 [{doc.get('title', '')}]：以引用方式记录，请手动复制内容")
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

    def _save_report(self, report: Dict) -> None:
        """保存整理报告到本地"""
        os.makedirs("logs", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"logs/organize_report_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"整理报告已保存: {path}")

        # 打印摘要
        print(f"\n{'='*50}")
        print(f"{'[DRY-RUN] ' if report['dry_run'] else ''}整理完成！")
        print(f"  总文档数: {report['total']}")
        print(f"  已整理:   {report['organized']}")
        print(f"  错误:     {report['errors']}")
        print(f"\n按分类统计:")
        for cat, titles in report["by_category"].items():
            print(f"  {cat}: {len(titles)} 篇")
        print(f"{'='*50}\n")
