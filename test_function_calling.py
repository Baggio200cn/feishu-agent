"""
测试豆包模型是否支持 Function Calling
用法: python test_function_calling.py [--model MODEL] [--api-key KEY]
"""
import argparse
import json
import sys
import os

try:
    import requests
except ImportError:
    sys.exit("请先安装依赖: pip install requests")

# ── 默认从 credentials.json 读取 ──────────────────────────────────────────────
def load_ai_config():
    cred_path = os.path.join(os.path.dirname(__file__), "config", "credentials.json")
    if os.path.exists(cred_path):
        with open(cred_path, encoding="utf-8") as f:
            data = json.load(f)
        ai = data.get("ai", {})
        return ai.get("api_key", ""), ai.get("model", ""), ai.get("base_url", "")
    return "", "", ""

# ── 工具定义（模拟天气查询）──────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的当前天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如：北京、上海"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "温度单位"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "查询股票当前价格",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如：AAPL、000001"
                    }
                },
                "required": ["symbol"]
            }
        }
    }
]

# ── 模拟工具执行 ───────────────────────────────────────────────────────────────
def execute_tool(name: str, args: dict) -> str:
    if name == "get_weather":
        city = args.get("city", "未知")
        unit = args.get("unit", "celsius")
        temp = "22°C" if unit == "celsius" else "72°F"
        return json.dumps({"city": city, "temperature": temp, "condition": "晴天", "humidity": "45%"}, ensure_ascii=False)
    elif name == "get_stock_price":
        symbol = args.get("symbol", "?")
        return json.dumps({"symbol": symbol, "price": "158.32", "change": "+1.2%"}, ensure_ascii=False)
    return json.dumps({"error": "unknown tool"})


def run_test(api_key: str, model: str, base_url: str):
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    print(f"\n{'='*60}")
    print(f"模型  : {model}")
    print(f"端点  : {url}")
    print(f"{'='*60}\n")

    # ── Round 1：发送带工具的请求 ───────────────────────────────────────────
    messages = [
        {"role": "user", "content": "北京今天天气怎么样？用摄氏度告诉我。"}
    ]
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": 512,
    }

    print("【Round 1】发送请求（含工具定义）...")
    print(f"  用户消息: {messages[0]['content']}\n")

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    choice = result["choices"][0]
    msg = choice["message"]
    finish_reason = choice.get("finish_reason", "")

    print(f"  finish_reason : {finish_reason}")
    print(f"  content       : {msg.get('content', '(空)')}")

    tool_calls = msg.get("tool_calls", [])
    if not tool_calls:
        print("\n❌ 模型未返回 tool_calls，不支持 Function Calling 或未触发工具调用。")
        print("   （finish_reason 应为 'tool_calls'，实际为: " + finish_reason + "）")
        return False

    print(f"\n✅ 模型返回了 {len(tool_calls)} 个 tool_call：")
    for tc in tool_calls:
        fn = tc["function"]
        print(f"   - {fn['name']}({fn['arguments']})")

    # ── Round 2：执行工具并回传结果 ────────────────────────────────────────
    print("\n【Round 2】执行工具并回传结果...")
    messages.append(msg)  # 追加 assistant 消息（含 tool_calls）

    for tc in tool_calls:
        fn_name = tc["function"]["name"]
        fn_args = json.loads(tc["function"]["arguments"])
        tool_result = execute_tool(fn_name, fn_args)
        print(f"   工具 {fn_name} 返回: {tool_result}")
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": tool_result
        })

    payload2 = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
    }
    resp2 = requests.post(url, headers=headers, json=payload2, timeout=30)
    resp2.raise_for_status()
    result2 = resp2.json()

    final_content = result2["choices"][0]["message"].get("content", "")
    print(f"\n【最终回答】\n  {final_content}")

    # ── 汇总 ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"✅ 测试通过！模型 {model} 支持 Function Calling。")
    usage = result.get("usage", {})
    print(f"   Token 用量: prompt={usage.get('prompt_tokens','?')}, "
          f"completion={usage.get('completion_tokens','?')}")
    print(f"{'='*60}\n")
    return True


def main():
    default_key, default_model, default_url = load_ai_config()

    parser = argparse.ArgumentParser(description="测试豆包模型 Function Calling")
    parser.add_argument("--model",   default=default_model or "doubao-seed-2-0-code-preview-260215",
                        help="模型 ID（默认从 credentials.json 读取）")
    parser.add_argument("--api-key", default=default_key,
                        help="API Key（默认从 credentials.json 读取）")
    parser.add_argument("--base-url", default=default_url or "https://ark.cn-beijing.volces.com/api/v3",
                        help="API base URL")
    args = parser.parse_args()

    if not args.api_key:
        sys.exit("❌ 请提供 API Key：--api-key <KEY>  或在 config/credentials.json 中配置 ai.api_key")

    try:
        run_test(args.api_key, args.model, args.base_url)
    except requests.HTTPError as e:
        print(f"\n❌ HTTP 错误 {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
