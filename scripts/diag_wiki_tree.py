"""
列出 Wiki 空间顶层节点 — 用于确认父节点标题是否和配置的 parent_folder 完全一致。

用法: python scripts/diag_wiki_tree.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.config_loader import config_loader
from src.utils.feishu_client import FeishuClientFactory


def main():
    creds = config_loader.load_credentials()
    space_id = creds["accounts"]["personal"].get("wiki_space_id", "")
    parent_folder = creds.get("github", {}).get("parent_folder", "github专区")

    print("=" * 70)
    print("Wiki 空间节点诊断")
    print("=" * 70)
    print(f"space_id:       {space_id}")
    print(f"要查找的父节点:  '{parent_folder}'")
    print()

    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")

    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    page_token = None
    all_nodes = []
    for page in range(10):
        builder = ListSpaceNodeRequest.builder().space_id(space_id).page_size(50)
        if page_token:
            builder.page_token(page_token)
        resp = client.wiki.v2.space_node.list(builder.build())

        if not resp.success():
            print(f"✗ 列节点失败: code={resp.code}, msg={resp.msg}")
            return

        items = getattr(resp.data, "items", None) or []
        all_nodes.extend(items)
        print(f"[第 {page + 1} 页] 获得 {len(items)} 个节点")

        if not getattr(resp.data, "has_more", False):
            break
        page_token = getattr(resp.data, "page_token", None)
        if not page_token:
            break

    print()
    print(f"空间顶层节点总数: {len(all_nodes)}")
    print()
    print(f"{'序号':<4} {'标题':<40} {'node_token':<25} {'类型'}")
    print("-" * 90)

    match_exact = None
    match_fuzzy = []

    for i, n in enumerate(all_nodes, 1):
        title = getattr(n, "title", "") or ""
        token = getattr(n, "node_token", "") or ""
        obj_type = getattr(n, "obj_type", "") or ""
        print(f"{i:<4} {title:<40} {token:<25} {obj_type}")

        if title == parent_folder:
            match_exact = n
        elif parent_folder in title or title in parent_folder:
            match_fuzzy.append(n)

    print()
    print("=" * 70)
    if match_exact:
        print(f"✓ 精确匹配到 '{parent_folder}'")
        print(f"  node_token = {match_exact.node_token}")
    elif match_fuzzy:
        print(f"△ 没有精确匹配 '{parent_folder}'，但以下节点名字接近:")
        for n in match_fuzzy:
            print(f"    '{n.title}' (node_token={n.node_token})")
        print()
        print("  → 可能是空格/大小写/emoji 差异。把 credentials.json 的 github.parent_folder")
        print("    改成上面列出来的那个精确标题即可。")
    else:
        print(f"✗ 没有任何节点叫 '{parent_folder}' 或接近的名字")
        print(f"  → 在飞书 Wiki 里手动创建一个叫 '{parent_folder}' 的页面（节点），")
        print(f"    或者把配置里的 parent_folder 改成上面列出来的某个现有节点标题。")
    print("=" * 70)


if __name__ == "__main__":
    main()
