"""
本机代理端口扫描 + 代理联通性诊断。
用法: python scripts/diag_proxy.py

对 127.0.0.1 上常见 VPN/代理端口做:
  1. TCP 握手（端口是否有进程在监听）
  2. 对能握手的端口，尝试通过 HTTP_PROXY 发 GET 到 www.reddit.com
  3. 打印哪个端口真能把 Reddit 的响应 200 带回来
"""
import socket
import sys

import requests

# 常见 VPN 客户端的 HTTP 代理默认端口
CANDIDATE_PORTS = [
    7890,   # Clash / Mihomo 默认混合端口
    7891,   # Clash SOCKS5
    7892,   # Clash redir
    1080,   # SSR / Shadowsocks 默认 SOCKS5
    8080,   # 通用 HTTP 代理
    8118,   # Privoxy
    10800,  # v2rayN 默认 SOCKS5
    10801,  # v2rayN 备用
    10809,  # v2rayN 默认 HTTP
    10810,  # v2rayN 备用
    12536,  # 用户提到的一个
    20171,  # Lantern
    51837,  # Trojan / NaïveProxy 常见
    53472,  # 有些自定义 Mihomo 用
]

TEST_URL = "https://www.reddit.com/r/MachineLearning/top.json?t=day&limit=1"
USER_AGENT = "feishu-agent-diag/0.1"


def tcp_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    """只握 TCP 三次手，不发 HTTP。能连上 = 有进程在监听"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def probe_proxy(port: int) -> str:
    """通过代理试发 Reddit 请求，返回 'OK 200' / 'HTTP xxx' / 错误串"""
    proxy_url = f"http://127.0.0.1:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        r = requests.get(
            TEST_URL,
            headers={"User-Agent": USER_AGENT},
            proxies=proxies,
            timeout=8,
        )
        return f"✓ HTTP {r.status_code}" if r.status_code == 200 else f"⚠ HTTP {r.status_code}"
    except requests.exceptions.ProxyError as e:
        msg = str(e).split(":")[-1].strip()[:60]
        return f"✗ 代理错误: {msg}"
    except requests.exceptions.ConnectTimeout:
        return "✗ 连接超时"
    except requests.exceptions.ReadTimeout:
        return "✗ 读超时（代理可能在但无法翻墙）"
    except requests.exceptions.SSLError as e:
        return f"✗ SSL 错误（SOCKS 代理用 HTTP 前缀会这样）"
    except Exception as e:
        return f"✗ {type(e).__name__}: {str(e)[:60]}"


def main():
    host = "127.0.0.1"
    print("=" * 70)
    print("代理端口扫描")
    print("=" * 70)
    print(f"目标: {TEST_URL}")
    print(f"扫描 {len(CANDIDATE_PORTS)} 个常见端口...")
    print()

    listening: list = []
    for port in CANDIDATE_PORTS:
        if tcp_alive(host, port):
            listening.append(port)
            print(f"  [监听] 127.0.0.1:{port} ← 有进程")
        else:
            print(f"  [空]   127.0.0.1:{port}")

    if not listening:
        print()
        print("✗ 没有任何候选端口在监听。")
        print("  → VPN 客户端可能没开，或用了列表外的端口。")
        print("  → 打开 VPN 客户端，找 'HTTP 代理端口' / 'Local Port' 设置。")
        print("  → Clash 用户看右上角 '系统代理' 或 'General → Port'。")
        return

    print()
    print("=" * 70)
    print(f"对 {len(listening)} 个监听端口做代理联通测试（每个最多等 8 秒）...")
    print("=" * 70)
    winners: list = []
    for port in listening:
        result = probe_proxy(port)
        print(f"  127.0.0.1:{port}  →  {result}")
        if result.startswith("✓"):
            winners.append(port)

    print()
    print("=" * 70)
    if winners:
        best = winners[0]
        print(f"🎉 可用端口: {winners}")
        print()
        print("把下面两行贴进 PowerShell（每次开窗口都要设）:")
        print(f'  $env:HTTP_PROXY  = "http://127.0.0.1:{best}"')
        print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{best}"')
        print()
        print("然后跑:")
        print("  python main.py import-reddit")
    else:
        print("✗ 所有监听端口都不能成功代理到 Reddit。")
        print("  可能原因:")
        print("  1. VPN 节点没选对（换个节点试试）")
        print("  2. 代理是 SOCKS5，不是 HTTP（某些 VPN 只开 SOCKS）")
        print("  3. 代理规则把 reddit.com 设成直连了（检查 VPN 的分流规则）")
    print("=" * 70)


if __name__ == "__main__":
    main()
