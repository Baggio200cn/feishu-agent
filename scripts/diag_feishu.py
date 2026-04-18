"""
飞书权限诊断脚本 — 逐项测试 Client 能做什么、做不了什么。
用法: python scripts/diag_feishu.py
"""
import json
import os
import sys

os.makedirs("logs", exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.config_loader import config_loader
from src.utils.feishu_client import FeishuClientFactory


def main():
    creds = config_loader.load_credentials()
    personal = creds["accounts"]["personal"]
    space_id = personal.get("wiki_space_id", "")

    print("=" * 70)
    print("飞书 Agent 权限诊断")
    print("=" * 70)
    print(f"app_id:        {personal.get('app_id', '')[:16]}...")
    print(f"wiki_space_id: {space_id}")
    print()

    factory = FeishuClientFactory(creds["accounts"])
    client = factory.get_client("personal")

    print("[测试 1] Client 初始化")
    print(f"  ✓ Client 已初始化（token 在第一次 API 调用时自动获取）")
    print()

    # 测试 2: 列出应用能访问的所有 Wiki 空间（最基础的只读调用）
    print("[测试 2] ListSpace — 列出应用能看到的所有 Wiki 空间（需要 wiki:wiki 或 wiki:space:read）")
    try:
        from lark_oapi.api.wiki.v2 import ListSpaceRequest
        req = ListSpaceRequest.builder().page_size(20).build()
        resp = client.wiki.v2.space.list(req)
        if resp.success():
            print(f"  ✓ 成功！应用能看到以下 Wiki 空间：")
            items = getattr(resp.data, "items", None) or []
            if not items:
                print("     （列表为空 — 应用没被任何 Wiki 空间授权或应用没安装到工作区）")
            for item in items:
                marker = "  ← 你配的那个" if item.space_id == space_id else ""
                print(f"     - {item.name} (id={item.space_id}){marker}")
        else:
            print(f"  ✗ 失败！code={resp.code}, msg={resp.msg}")
            print(f"     → 这说明应用身份的 wiki 读权限也没生效")
    except Exception as e:
        print(f"  ✗ 异常: {e}")
    print()

    # 测试 3: 访问配的那个具体空间
    print(f"[测试 3] GetSpace — 读取你配的 space_id={space_id}")
    try:
        from lark_oapi.api.wiki.v2 import GetSpaceRequest
        req = GetSpaceRequest.builder().space_id(space_id).build()
        resp = client.wiki.v2.space.get(req)
        if resp.success():
            sp = resp.data.space
            print(f"  ✓ 成功！")
            print(f"     空间名称: {sp.name}")
            print(f"     拥有者:   {sp.owner}")
            print(f"     类型:     {sp.space_type}")
        else:
            print(f"  ✗ 失败！code={resp.code}, msg={resp.msg}")
            if "99991672" in str(resp.msg):
                print(f"     → 权限没生效（对照测试 2 的结果判断是应用级还是空间级问题）")
            elif resp.code == 1313003 or "not found" in str(resp.msg).lower():
                print(f"     → space_id 不存在或不属于此应用的租户！")
                print(f"     → 如果测试 2 里根本没列出你的空间，说明应用装在别的租户")
    except Exception as e:
        print(f"  ✗ 异常: {e}")
    print()

    # 测试 4: 尝试创建一个测试节点（会实际写东西）
    print(f"[测试 4] CreateSpaceNode — 尝试在你的空间里创建一个 '诊断测试页' （会真写入）")
    try:
        from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node
        node = (Node.builder()
                .obj_type("docx")
                .node_type("origin")
                .title("[诊断] 可删除的测试页")
                .build())
        req = (CreateSpaceNodeRequest.builder()
               .space_id(space_id)
               .request_body(node)
               .build())
        resp = client.wiki.v2.space_node.create(req)
        if resp.success():
            print(f"  ✓ 成功！node_token={resp.data.node.node_token}")
            print(f"     URL: https://open.feishu.cn/wiki/{resp.data.node.node_token}")
            print(f"     → 你可以去飞书 Wiki 里看到这个页，确认无误后手动删除")
        else:
            print(f"  ✗ 失败！code={resp.code}, msg={resp.msg}")
    except Exception as e:
        print(f"  ✗ 异常: {e}")
    print()

    print("=" * 70)
    print("判读指南：")
    print("  测试 2 失败 → 应用身份的 wiki 权限整体没生效（版本未发布 / 未安装）")
    print("  测试 2 通过但空间列表为空 → 应用没安装到你的工作区")
    print("  测试 2 通过但看不到你配的空间 → 空间不属于应用所在租户")
    print("  测试 3 失败 99991672 → 空间级权限未授予应用")
    print("  测试 3 通过但测试 4 失败 → 只差 wiki:node:create 子权限")
    print("=" * 70)


if __name__ == "__main__":
    main()
