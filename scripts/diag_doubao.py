"""
豆包 API 连通性诊断
用法: python scripts/diag_doubao.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from src.utils.config_loader import config_loader


def main():
    cfg = config_loader.get_ai_config()
    base_url = cfg.get("base_url", "")
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "")

    print("=" * 60)
    print("豆包 API 诊断")
    print("=" * 60)
    print(f"base_url: {base_url}")
    print(f"model:    {model}")
    print(f"api_key:  {api_key[:8]}...（共 {len(api_key)} 位）")
    print()

    if not api_key or api_key.startswith("your_") or "your_doubao" in api_key:
        print("✗ api_key 未配置或仍是占位值")
        return

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "用一句话说你好"}],
    }

    print(f"POST {url}")
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
    except requests.RequestException as e:
        print(f"✗ 请求异常: {e}")
        return

    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print()

    if r.status_code == 200:
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(f"✓ 豆包回复: {content}")
        except Exception as e:
            print(f"✗ 响应解析失败: {e}")
    elif r.status_code == 401:
        print("✗ 401 Unauthorized — api_key 不对，去火山方舟控制台复查")
    elif r.status_code == 403:
        print("✗ 403 Forbidden — api_key 没开通这个模型的权限")
    elif r.status_code == 404:
        print(f"✗ 404 Not Found — 模型名 '{model}' 在你账号下不存在")
        print("   去 https://console.volcengine.com/ark/region:ark+cn-beijing/model 看你实际开通了哪些模型")


if __name__ == "__main__":
    main()
