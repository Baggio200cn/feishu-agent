"""
清理 Wiki 结构脚本
将其他文件夹内的真实文档移回 Wiki 根目录，并删除空的分类目录节点。

用法:
  python cleanup_wiki.py --list          # 仅列出节点，不移动
  python cleanup_wiki.py --move          # 移动真实文档到根目录
  python cleanup_wiki.py --move --delete # 移动后删除空分类目录
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from src.utils.config_loader import config_loader as _cfg
    _creds = _cfg.load_credentials()
    _personal = _creds["accounts"]["personal"]
    SPACE_ID = _personal.get("wiki_space_id", "")
    APP_ID = _personal.get("app_id", "")
    APP_SECRET = _personal.get("app_secret", "")
except Exception as _e:
    print(f"警告: 无法从配置文件加载凭据: {_e}")
    print("请确保 config/credentials.json 中 accounts.personal 包含 wiki_space_id / app_id / app_secret")
    SPACE_ID = ""
    APP_ID = ""
    APP_SECRET = ""

CATEGORY_ICONS = ["📊", "📝", "📚", "📁", "🤖", "📌", "📄", "🗂️", "💼", "🔖"]


def get_tenant_token() -> str:
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    return data["tenant_access_token"]


def list_nodes(token: str, parent_node_token: str = "") -> list:
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{SPACE_ID}/nodes"
    headers = {"Authorization": f"Bearer {token}"}
    params = {}
    if parent_node_token:
        params["parent_node_token"] = parent_node_token

    nodes = []
    page_token = None
    while True:
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            print(f"获取节点失败: {body.get('msg')}")
            break
        items = body.get("data", {}).get("items", [])
        nodes.extend(items)
        if not body.get("data", {}).get("has_more"):
            break
        page_token = body["data"].get("page_token")

    return nodes


def move_node_to_root(token: str, node_token: str, title: str) -> bool:
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{SPACE_ID}/nodes/{node_token}/move"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "target_parent_token": "",
        "target_space_id": SPACE_ID,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") == 0:
        print(f"  已移动: {title}")
        return True
    else:
        print(f"  失败: {title} -> {data.get('msg')}")
        return False


def delete_node(token: str, node_token: str, title: str) -> bool:
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{SPACE_ID}/nodes/{node_token}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") == 0:
        print(f"  已删除: {title}")
        return True
    else:
        print(f"  删除失败: {title} -> {data.get('msg')}")
        return False


def is_category_folder(title: str) -> bool:
    for icon in CATEGORY_ICONS:
        if title.startswith(icon + " "):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="清理 Wiki 结构")
    parser.add_argument("--list", action="store_true", help="仅列出节点")
    parser.add_argument("--move", action="store_true", help="将真实文档移回根目录")
    parser.add_argument("--delete", action="store_true", help="移动后删除空分类目录（需与 --move 同用）")
    args = parser.parse_args()

    if not args.list and not args.move:
        parser.print_help()
        return

    if not APP_ID or not APP_SECRET or not SPACE_ID:
        print("错误: 凭据未配置。请检查 config/credentials.json 中 accounts.personal 字段。")
        sys.exit(1)

    print("获取访问令牌...")
    token = get_tenant_token()

    print("\n列出 Wiki 根节点...")
    root_nodes = list_nodes(token)
    print(f"根节点共 {len(root_nodes)} 个")
    for n in root_nodes:
        print(f"  [{n.get('node_token')}] {n.get('title')} (has_child={n.get('has_child')})")

    all_children = []
    for root in root_nodes:
        if root.get("has_child"):
            children = list_nodes(token, root["node_token"])
            for c in children:
                c["_parent_token"] = root["node_token"]
                c["_parent_title"] = root.get("title", "")
            all_children.extend(children)

    content_docs = [n for n in all_children if not is_category_folder(n.get("title", ""))]
    folder_nodes = [n for n in all_children if is_category_folder(n.get("title", ""))]

    print(f"\n真实文档: {len(content_docs)} 篇")
    print(f"分类目录节点: {len(folder_nodes)} 个")

    for doc in content_docs:
        print(f"  [{doc.get('node_token')}] {doc.get('title')[:50]}")

    if args.list:
        return

    if args.move:
        print(f"\n开始移动 {len(content_docs)} 篇文档到根目录...")
        success = 0
        for doc in content_docs:
            if move_node_to_root(token, doc["node_token"], doc.get("title", "")):
                success += 1
            time.sleep(0.3)

        print(f"\n移动完成: {success}/{len(content_docs)} 成功")

        if args.delete and folder_nodes:
            print(f"\n删除 {len(folder_nodes)} 个空分类目录节点...")
            for folder in folder_nodes:
                delete_node(token, folder["node_token"], folder.get("title", ""))
                time.sleep(0.3)

            print("\n尝试删除已清空的根节点...")
            for root in root_nodes:
                remaining = list_nodes(token, root["node_token"])
                if not remaining:
                    delete_node(token, root["node_token"], root.get("title", ""))
                    time.sleep(0.3)
                else:
                    print(f"  跳过 [{root.get('title')}]：仍有 {len(remaining)} 个子节点")


if __name__ == "__main__":
    main()
