"""诊断 Reddit RSS Feed 实际返回内容"""
import xml.etree.ElementTree as ET
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; feishu-agent/1.0; RSS reader)"}
URL = "https://www.reddit.com/r/AI_Agents/new.rss"

resp = requests.get(URL, headers=HEADERS, timeout=20)
print(f"HTTP {resp.status_code}  Content-Type: {resp.headers.get('Content-Type','')}")
print(f"Body 前 800 字符:\n{resp.text[:800]}\n")

if resp.status_code == 200:
    try:
        root = ET.fromstring(resp.text)
        print(f"根标签: {root.tag}")
        print(f"子元素数: {len(list(root))}")
        for child in list(root)[:5]:
            print(f"  子标签: {child.tag}")
        ATOM = "http://www.w3.org/2005/Atom"
        entries = root.findall(f"{{{ATOM}}}entry")
        print(f"\natom:entry (直接子节点): {len(entries)}")
        entries2 = root.findall(f".//{{{ATOM}}}entry")
        print(f"atom:entry (.//): {len(entries2)}")
        items = root.findall(".//item")
        print(f"item (RSS2.0): {len(items)}")
    except ET.ParseError as e:
        print(f"XML 解析失败 (可能是 HTML 页面): {e}")
