"""
Reddit 图片下载模块

职责：
  1. 从 RSS 条目中提取图片 URL（支持 media:thumbnail / <img> 等多来源）
  2. 下载图片到本地（修复 403 / &amp; 编码问题）

错误路径（CLAUDE.md §1）：
  Layer 1 — 重试 2 次，指数退避
  Layer 2 — preview.redd.it 返回 403 时回退到 i.redd.it
  Layer 3 — 最终失败记录结构化日志，返回 False，绝不抛出异常
"""

import html
import logging
import os
import re
import time
from typing import List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

# 伪装成浏览器，避免 Reddit CDN 返回 403
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.reddit.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

_IMAGE_EXTS   = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
_IMAGE_HOSTS  = frozenset({"i.redd.it", "preview.redd.it", "i.imgur.com",
                            "b.thumbs.redditmedia.com", "a.thumbs.redditmedia.com"})


# ── 公共接口 ───────────────────────────────────────────────────────────────────

def extract_image_urls_from_rss_entry(entry) -> List[str]:
    """
    从 feedparser RSS 条目中提取图片 URL，最多 3 张。

    检查顺序（越靠前优先级越高）：
      1. media:thumbnail  — 最可靠，Reddit 几乎所有图片帖都有
      2. media:content    — 画廊帖 / 视频帖封面
      3. enclosures       — Atom/RSS 附件
      4. <img src> in summary HTML — 兜底

    所有 URL 统一经 html.unescape() 解码（修复 &amp; → & 问题）。
    """
    urls: List[str] = []

    # 1. media:thumbnail（最常见）
    for t in getattr(entry, "media_thumbnail", []):
        if len(urls) >= 3:
            break
        url = _clean(t.get("url", ""))
        if url and url not in urls:
            urls.append(url)

    # 2. media:content
    for m in getattr(entry, "media_content", []):
        if len(urls) >= 3:
            break
        url = _clean(m.get("url", ""))
        medium = m.get("medium", "")
        mime   = m.get("type", "")
        if url and (medium == "image" or mime.startswith("image/") or _is_image(url)):
            if url not in urls:
                urls.append(url)

    # 3. enclosures
    for enc in getattr(entry, "enclosures", []):
        if len(urls) >= 3:
            break
        url  = _clean(enc.get("href", ""))
        mime = enc.get("type", "")
        if url and (mime.startswith("image/") or _is_image(url)) and url not in urls:
            urls.append(url)

    # 4. <img src="..."> in summary HTML（兜底）
    if len(urls) < 3:
        for raw in re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']',
            getattr(entry, "summary", "") or "",
            re.IGNORECASE,
        ):
            if len(urls) >= 3:
                break
            url = _clean(raw)
            if not url or url.startswith("data:"):
                continue
            # 过滤 Reddit 图标 / emoji（体积小，无意义）
            if any(kw in url for kw in ("emoji", "icon", "snoo", "static")):
                continue
            if url not in urls:
                urls.append(url)

    return urls[:3]


def download_image(url: str, save_path: str, timeout: int = 20) -> bool:
    """
    下载单张图片到 save_path。

    Layer 1 — _try_download 最多重试 2 次（指数退避）
    Layer 2 — preview.redd.it 403 → 自动改用 i.redd.it
    Layer 3 — 全部失败 → 结构化日志，返回 False
    """
    url = _clean(url)

    # Layer 1
    if _try_download(url, save_path, timeout, retries=2):
        return True

    # Layer 2：preview.redd.it 失败时尝试原图
    if "preview.redd.it" in url:
        fallback = _preview_to_iredd(url)
        if fallback:
            logger.info("image_fallback src=%s fallback=%s", url, fallback)
            if _try_download(fallback, save_path, timeout, retries=1):
                return True

    # Layer 3
    logger.warning("image_download_failed url=%s path=%s", url, save_path)
    return False


def download_post_images(
    image_urls: List[str],
    output_dir: str,
    post_id: str,
    delay: float = 0.5,
) -> List[str]:
    """
    下载一个帖子的图片列表，返回成功保存的本地路径。
    单张失败不影响其余（CLAUDE.md：一条失败不中止批次）。
    """
    saved: List[str] = []
    for idx, url in enumerate(image_urls):
        ext       = _guess_ext(url)
        save_path = os.path.join(output_dir, f"{post_id}_{idx}{ext}")

        if os.path.exists(save_path):
            saved.append(save_path)
            continue

        if download_image(url, save_path):
            saved.append(save_path)

        if delay > 0 and idx < len(image_urls) - 1:
            time.sleep(delay)

    return saved


# ── 内部实现 ───────────────────────────────────────────────────────────────────

def _try_download(url: str, save_path: str, timeout: int, retries: int) -> bool:
    """CLAUDE.md §1 具体重试模式。"""
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout, stream=True)
            resp.raise_for_status()
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            logger.info("image_saved url=%s path=%s", url, save_path)
            return True
        except requests.RequestException as e:
            if attempt < retries:
                time.sleep(2 ** attempt)   # 1s, 2s
                continue
            logger.warning("fetch_failed url=%s error=%s", url, e)
            return False
    return False


def _clean(raw: str) -> str:
    """html.unescape + strip（&amp; → & 等）。"""
    return html.unescape(raw.strip()) if raw else ""


def _is_image(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.split("?")[0])
    return (
        any(parsed.path.lower().endswith(ext) for ext in _IMAGE_EXTS)
        or parsed.netloc in _IMAGE_HOSTS
    )


def _preview_to_iredd(url: str) -> Optional[str]:
    """https://preview.redd.it/X.png?... → https://i.redd.it/X.png"""
    try:
        p = urlparse(url)
        if p.netloc == "preview.redd.it":
            return f"https://i.redd.it{p.path}"
    except Exception:
        pass
    return None


def _guess_ext(url: str) -> str:
    path = urlparse(url.split("?")[0]).path.lower()
    for ext in _IMAGE_EXTS:
        if path.endswith(ext):
            return ext
    return ".jpg"
