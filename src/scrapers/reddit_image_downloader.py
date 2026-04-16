"""
Reddit 图片抓取与下载模块

修复两个核心问题：
1. RSS 模式下图片未能识别 — 检查 media_content / media_thumbnail /
   enclosures / summary <img> 等多个来源，并对所有 URL 做 html.unescape()
2. 下载 preview.redd.it 图片时返回 403 — 携带浏览器 User-Agent + Referer，
   并在 403 时自动回退到 i.redd.it
"""

import html
import logging
import os
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

# 浏览器请求头：避免 Reddit CDN (preview.redd.it / i.redd.it) 返回 403
_DOWNLOAD_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.reddit.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
_IMAGE_CDN_HOSTS = frozenset({"i.redd.it", "preview.redd.it", "i.imgur.com"})


# ── 公共 API ─────────────────────────────────────────────────────────────────


def extract_image_urls_from_rss_entry(entry) -> List[str]:
    """
    从 feedparser RSS 条目中提取图片 URL（最多 3 张）。

    依次检查：
      1. entry.media_content   (media:content 标签)
      2. entry.media_thumbnail (media:thumbnail 标签)
      3. entry.enclosures      (RSS/Atom enclosure)
      4. entry.summary 内嵌 HTML 中的 <img src="...">

    所有 URL 均经 html.unescape() 解码（修复 &amp; → & 问题）。
    """
    urls: List[str] = []

    # 1. media:content
    for m in getattr(entry, "media_content", []):
        if len(urls) >= 3:
            break
        raw = m.get("url", "").strip()
        if not raw:
            continue
        url = html.unescape(raw)
        medium = m.get("medium", "")
        mime = m.get("type", "")
        if medium == "image" or mime.startswith("image/") or _is_image_url(url):
            if url not in urls:
                urls.append(url)

    # 2. media:thumbnail
    for t in getattr(entry, "media_thumbnail", []):
        if len(urls) >= 3:
            break
        raw = t.get("url", "").strip()
        if not raw:
            continue
        url = html.unescape(raw)
        if _is_image_url(url) and url not in urls:
            urls.append(url)

    # 3. enclosures
    for enc in getattr(entry, "enclosures", []):
        if len(urls) >= 3:
            break
        raw = enc.get("href", "").strip()
        if not raw:
            continue
        url = html.unescape(raw)
        mime = enc.get("type", "")
        if (mime.startswith("image/") or _is_image_url(url)) and url not in urls:
            urls.append(url)

    # 4. <img src="..."> in summary HTML
    if len(urls) < 3:
        summary_html = getattr(entry, "summary", "") or ""
        for raw in re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']', summary_html, re.IGNORECASE
        ):
            if len(urls) >= 3:
                break
            url = html.unescape(raw.strip())
            if not url or url.startswith("data:"):
                continue
            # 过滤掉 Reddit 的小图标 / emoji
            if "emoji" in url or "icon" in url or "snoo" in url:
                continue
            if url not in urls:
                urls.append(url)

    return urls[:3]


def extract_image_urls_from_json_post(data: dict) -> List[str]:
    """
    从 Reddit JSON API 帖子 data 字典中提取图片 URL（最多 3 张）。

    依次检查：
      1. post_hint == "image" 时的直接 URL
      2. is_gallery + media_metadata（图片集）
      3. preview.images[0].source.url（预览图回退）
      4. URL 本身以图片扩展名结尾时的兜底

    所有 URL 均经 html.unescape() 解码。
    """
    urls: List[str] = []

    post_hint = data.get("post_hint", "")

    # 1. 直接图片帖
    if post_hint == "image":
        url = html.unescape(data.get("url", ""))
        if url and url not in urls:
            urls.append(url)

    # 2. 图片集
    if data.get("is_gallery") and data.get("media_metadata"):
        gallery_order: List[str] = []
        if data.get("gallery_data") and data["gallery_data"].get("items"):
            gallery_order = [
                item["media_id"] for item in data["gallery_data"]["items"]
            ]
        else:
            gallery_order = list(data["media_metadata"].keys())

        for media_id in gallery_order:
            if len(urls) >= 3:
                break
            item = data["media_metadata"].get(media_id, {})
            if item.get("status") == "valid" and "s" in item:
                raw = item["s"].get("u", "")
                if raw:
                    url = html.unescape(raw)
                    if url not in urls:
                        urls.append(url)

    # 3. preview 图回退（link / self 帖内嵌预览）
    if not urls and "preview" in data:
        try:
            raw = data["preview"]["images"][0]["source"]["url"]
            url = html.unescape(raw)
            if url not in urls:
                urls.append(url)
        except (KeyError, IndexError):
            pass

    # 4. 兜底：帖子 URL 本身是图片
    if not urls:
        url = html.unescape(data.get("url", ""))
        if url and _is_image_url(url) and url not in urls:
            urls.append(url)

    return urls[:3]


def download_image(url: str, save_path: str, timeout: int = 20, retries: int = 2) -> bool:
    """
    下载单张图片并保存到 save_path。

    修复要点：
    - 对 URL 调用 html.unescape()，防止 &amp; 等编码导致 403
    - 携带浏览器 User-Agent + Referer，绕过 Reddit CDN 鉴权
    - preview.redd.it 返回 403 时自动尝试 i.redd.it 备用地址
    - 指数退避重试

    Returns:
        True 表示下载成功，False 表示最终失败
    """
    # 确保 URL 中没有 HTML 实体（&amp; → &）
    url = html.unescape(url.strip())

    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url, headers=_DOWNLOAD_HEADERS, timeout=timeout, stream=True
            )
            resp.raise_for_status()

            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"图片已下载: {save_path}")
            return True

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            logger.warning(f"⚠️ 图片下载失败 ({url}): {e}")

            # 403 on preview.redd.it → 尝试 i.redd.it 备用
            if status == 403 and "preview.redd.it" in url:
                fallback = _preview_to_iredd(url)
                if fallback and fallback != url:
                    logger.info(f"  回退到备用 URL: {fallback}")
                    url = fallback
                    continue  # 用新 URL 立即重试，不计入 attempt

            if attempt < retries:
                wait = 2 ** attempt
                logger.debug(f"  {wait}s 后重试…")
                time.sleep(wait)
            else:
                return False

        except Exception as e:
            logger.warning(f"⚠️ 图片下载失败 ({url}): {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                return False

    return False


def download_post_images(
    image_urls: List[str],
    output_dir: str,
    post_id: str,
    delay: float = 0.5,
) -> List[str]:
    """
    下载一个帖子的所有图片，返回成功下载的本地路径列表。

    Args:
        image_urls: 图片 URL 列表
        output_dir: 本地保存目录
        post_id:    帖子 ID，用于生成文件名
        delay:      每次下载之间的间隔秒数（避免触发限流）
    """
    saved_paths: List[str] = []

    for idx, url in enumerate(image_urls):
        ext = _guess_extension(url)
        filename = f"{post_id}_{idx}{ext}"
        save_path = os.path.join(output_dir, filename)

        if os.path.exists(save_path):
            logger.debug(f"图片已存在，跳过: {save_path}")
            saved_paths.append(save_path)
            continue

        if download_image(url, save_path):
            saved_paths.append(save_path)

        if delay > 0 and idx < len(image_urls) - 1:
            time.sleep(delay)

    return saved_paths


# ── 私有工具 ──────────────────────────────────────────────────────────────────


def _is_image_url(url: str) -> bool:
    """按扩展名或 CDN 域名判断 URL 是否为图片。"""
    if not url:
        return False
    base = url.split("?")[0]
    parsed = urlparse(base)
    if any(parsed.path.lower().endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True
    return parsed.netloc in _IMAGE_CDN_HOSTS


def _preview_to_iredd(preview_url: str) -> Optional[str]:
    """
    将 preview.redd.it URL 转换为 i.redd.it URL（去除签名参数）。

    示例：
      https://preview.redd.it/abc123.png?width=640&s=xxx
      → https://i.redd.it/abc123.png
    """
    try:
        parsed = urlparse(preview_url)
        if parsed.netloc == "preview.redd.it":
            return f"https://i.redd.it{parsed.path}"
    except Exception:
        pass
    return None


def _guess_extension(url: str) -> str:
    """从 URL 路径猜测图片扩展名，默认 .jpg。"""
    path = urlparse(url.split("?")[0]).path.lower()
    for ext in _IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return ext
    return ".jpg"
