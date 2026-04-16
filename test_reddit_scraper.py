"""
Reddit 图片抓取模块 — 离线单元测试（无需网络）

运行方式：
    python test_reddit_scraper.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.scrapers.reddit_image_downloader import (
    _clean, _guess_ext, _is_image, _preview_to_iredd,
    download_image, download_post_images,
    extract_image_urls_from_rss_entry,
)
from src.scrapers.reddit_rss_collector import (
    RedditRSSCollector, RunState, _extract_images, _strip_html,
)
import xml.etree.ElementTree as ET


# ── 辅助：构造 mock feedparser entry ──────────────────────────────────────────

def _entry(media_thumbnail=None, media_content=None, enclosures=None, summary=""):
    e = MagicMock()
    e.media_thumbnail = media_thumbnail or []
    e.media_content   = media_content or []
    e.enclosures      = enclosures or []
    e.summary         = summary
    return e


# ── 辅助：构造最小 Atom XML ────────────────────────────────────────────────────

_ATOM_TMPL = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:media="http://search.yahoo.com/mrss/">
{entries}
</feed>"""

_ENTRY_TMPL = """\
  <entry>
    <id>t3_{post_id}</id>
    <title>{title}</title>
    <link href="{link}" />
    <author><name>{author}</name></author>
    <updated>2026-04-16T10:00:00+00:00</updated>
    <content type="html">{content}</content>
    {media}
  </entry>"""


def _make_xml(posts):
    entries = []
    for p in posts:
        media = ""
        for url in p.get("thumbs", []):
            media += f'<media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="{url}" />\n    '
        entries.append(_ENTRY_TMPL.format(
            post_id = p["id"],
            title   = p.get("title", "Test Post"),
            link    = p.get("link", "https://www.reddit.com/r/test/comments/abc/"),
            author  = p.get("author", "user"),
            content = p.get("content", ""),
            media   = media,
        ))
    return _ATOM_TMPL.format(entries="\n".join(entries))


# ══════════════════════════════════════════════════════════════════════════════
# 1. URL 工具函数
# ══════════════════════════════════════════════════════════════════════════════

class TestClean(unittest.TestCase):

    def test_decodes_amp_entities(self):
        raw = "https://preview.redd.it/x.png?width=640&amp;s=abc"
        self.assertEqual(_clean(raw), "https://preview.redd.it/x.png?width=640&s=abc")

    def test_decodes_nested_entities(self):
        self.assertEqual(_clean("a &amp;amp; b"), "a &amp; b")

    def test_strips_whitespace(self):
        self.assertEqual(_clean("  https://example.com  "), "https://example.com")

    def test_empty_returns_empty(self):
        self.assertEqual(_clean(""), "")


class TestIsImage(unittest.TestCase):

    def test_by_extension(self):
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            self.assertTrue(_is_image(f"https://example.com/photo{ext}"), ext)

    def test_by_cdn_host(self):
        self.assertTrue(_is_image("https://i.redd.it/abc?s=tok"))
        self.assertTrue(_is_image("https://preview.redd.it/abc?w=640"))
        self.assertTrue(_is_image("https://i.imgur.com/xyz"))
        self.assertTrue(_is_image("https://b.thumbs.redditmedia.com/img"))

    def test_non_image(self):
        self.assertFalse(_is_image("https://reddit.com/r/x"))
        self.assertFalse(_is_image(""))

    def test_case_insensitive_extension(self):
        self.assertTrue(_is_image("https://example.com/IMG.PNG"))


class TestPreviewToIredd(unittest.TestCase):

    def test_converts_correctly(self):
        url = "https://preview.redd.it/abc123.png?width=640&s=xxx"
        self.assertEqual(_preview_to_iredd(url), "https://i.redd.it/abc123.png")

    def test_non_preview_returns_none(self):
        self.assertIsNone(_preview_to_iredd("https://i.redd.it/abc.png"))

    def test_empty_returns_none(self):
        self.assertIsNone(_preview_to_iredd(""))


class TestGuessExt(unittest.TestCase):

    def test_known_extension(self):
        self.assertEqual(_guess_ext("https://x.com/a.png?s=tok"), ".png")
        self.assertEqual(_guess_ext("https://x.com/a.gif"),       ".gif")
        self.assertEqual(_guess_ext("https://x.com/a.webp"),      ".webp")

    def test_default_jpg(self):
        self.assertEqual(_guess_ext("https://i.redd.it/abc"),     ".jpg")


# ══════════════════════════════════════════════════════════════════════════════
# 2. RSS entry 图片提取（feedparser mock）
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractImageUrlsFromRssEntry(unittest.TestCase):

    def test_media_thumbnail_decoded(self):
        """media:thumbnail URL 中 &amp; 必须被解码。"""
        entry = _entry(media_thumbnail=[
            {"url": "https://preview.redd.it/t.jpg?s=abc&amp;w=640"}
        ])
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(len(urls), 1)
        self.assertNotIn("&amp;", urls[0])
        self.assertIn("&w=640", urls[0])

    def test_media_content_image(self):
        entry = _entry(media_content=[
            {"url": "https://i.redd.it/img.jpg", "medium": "image"}
        ])
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertIn("https://i.redd.it/img.jpg", urls)

    def test_enclosure(self):
        entry = _entry(enclosures=[
            {"href": "https://i.imgur.com/photo.jpg", "type": "image/jpeg"}
        ])
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertIn("https://i.imgur.com/photo.jpg", urls)

    def test_summary_img_fallback(self):
        """summary <img> 兜底，且解码 &amp;。"""
        entry = _entry(
            summary='<img src="https://preview.redd.it/p.png?s=x&amp;w=640" />'
        )
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(len(urls), 1)
        self.assertNotIn("&amp;", urls[0])

    def test_emoji_filtered(self):
        """Reddit emoji / icon 图标应被过滤。"""
        entry = _entry(
            summary='<img src="https://www.redditstatic.com/emoji/snoo.png" />'
                    '<img src="https://i.redd.it/real.jpg" />'
        )
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(len(urls), 1)
        self.assertIn("real.jpg", urls[0])

    def test_data_uri_filtered(self):
        entry = _entry(
            summary='<img src="data:image/png;base64,abc" />'
                    '<img src="https://i.redd.it/real.png" />'
        )
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(len(urls), 1)

    def test_no_images_returns_empty(self):
        entry = _entry(summary="<p>Just text, no images.</p>")
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(urls, [])

    def test_capped_at_three(self):
        entry = _entry(media_thumbnail=[
            {"url": f"https://i.redd.it/img{i}.jpg"} for i in range(6)
        ])
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertLessEqual(len(urls), 3)

    def test_deduplication(self):
        """同一 URL 不重复出现。"""
        same = "https://i.redd.it/same.jpg"
        entry = _entry(
            media_thumbnail=[{"url": same}],
            media_content=[{"url": same, "medium": "image"}],
        )
        urls = extract_image_urls_from_rss_entry(entry)
        self.assertEqual(urls.count(same), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Atom XML 解析
# ══════════════════════════════════════════════════════════════════════════════

class TestAtomXmlParsing(unittest.TestCase):

    def setUp(self):
        self.collector = RedditRSSCollector()

    def test_parses_basic_entry(self):
        xml = _make_xml([{"id": "abc123", "title": "Hello World",
                          "link": "https://www.reddit.com/r/test/comments/abc/"}])
        posts = self.collector._parse(xml, "test", limit=10, seen_ids=set())
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "abc123")
        self.assertEqual(posts[0]["title"], "Hello World")

    def test_media_thumbnail_extracted(self):
        xml = _make_xml([{
            "id": "img1",
            "thumbs": ["https://b.thumbs.redditmedia.com/photo.jpg"]
        }])
        posts = self.collector._parse(xml, "test", limit=10, seen_ids=set())
        self.assertEqual(len(posts[0]["image_urls"]), 1)

    def test_seen_ids_skipped(self):
        xml = _make_xml([{"id": "seen1"}, {"id": "new1"}])
        posts = self.collector._parse(xml, "test", limit=10, seen_ids={"seen1"})
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "new1")

    def test_limit_respected(self):
        xml = _make_xml([{"id": f"p{i}"} for i in range(10)])
        posts = self.collector._parse(xml, "test", limit=3, seen_ids=set())
        self.assertEqual(len(posts), 3)

    def test_corrupt_xml_returns_empty(self):
        posts = self.collector._parse("not valid xml <{{}", "test", limit=10, seen_ids=set())
        self.assertEqual(posts, [])

    def test_amp_in_thumbnail_decoded(self):
        xml = _make_xml([{
            "id": "amptest",
            "thumbs": ["https://preview.redd.it/x.jpg?w=140&amp;s=abc"]
        }])
        posts = self.collector._parse(xml, "test", limit=10, seen_ids=set())
        img_url = posts[0]["image_urls"][0]
        self.assertNotIn("&amp;", img_url)
        self.assertIn("&s=abc", img_url)

    def test_selftext_truncated_at_8000(self):
        """正文 token budget：截断到 8000 字符（CLAUDE.md §2）。"""
        long_content = "A" * 20000
        xml = _make_xml([{"id": "long1", "content": long_content}])
        posts = self.collector._parse(xml, "test", limit=10, seen_ids=set())
        self.assertLessEqual(len(posts[0]["selftext"]), 8000)


# ══════════════════════════════════════════════════════════════════════════════
# 4. 图片下载（mock HTTP）
# ══════════════════════════════════════════════════════════════════════════════

class TestDownloadImage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "img.jpg")

    def _ok_resp(self):
        r = MagicMock()
        r.iter_content.return_value = [b"fake_image_data"]
        r.raise_for_status.return_value = None
        return r

    def _http_err(self, status: int):
        import requests as req
        resp = MagicMock()
        resp.status_code = status
        return req.exceptions.HTTPError(response=resp)

    def test_successful_download(self):
        with patch("src.scrapers.reddit_image_downloader.requests.get",
                   return_value=self._ok_resp()):
            ok = download_image("https://i.redd.it/test.jpg", self.path)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.path))

    def test_amp_decoded_before_request(self):
        """Layer 0：&amp; 在发起请求前已还原为 &。"""
        with patch("src.scrapers.reddit_image_downloader.requests.get",
                   return_value=self._ok_resp()) as mock_get:
            download_image("https://preview.redd.it/x.jpg?s=abc&amp;w=640", self.path)
        called_url = mock_get.call_args[0][0]
        self.assertNotIn("&amp;", called_url)
        self.assertIn("&w=640", called_url)

    def test_403_preview_falls_back_to_iredd(self):
        """Layer 2：preview.redd.it 403 → 自动改用 i.redd.it。"""
        side = [self._http_err(403), self._http_err(403), self._ok_resp()]
        with patch("src.scrapers.reddit_image_downloader.requests.get", side_effect=side):
            with patch("src.scrapers.reddit_image_downloader.time.sleep"):
                ok = download_image(
                    "https://preview.redd.it/abc.jpg?width=640&s=xyz", self.path
                )
        self.assertTrue(ok)

    def test_permanent_failure_returns_false(self):
        """Layer 3：全部重试失败 → 返回 False，不抛异常。"""
        with patch("src.scrapers.reddit_image_downloader.requests.get",
                   side_effect=self._http_err(500)):
            with patch("src.scrapers.reddit_image_downloader.time.sleep"):
                ok = download_image("https://i.redd.it/fail.jpg", self.path)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.path))

    def test_batch_continues_on_failure(self):
        """一张失败不中止批次（CLAUDE.md §1 Never）。"""
        side = [
            self._http_err(404), self._http_err(404), self._http_err(404),  # fail1 三次全败
            self._ok_resp(),                                                  # ok
        ]
        with patch("src.scrapers.reddit_image_downloader.requests.get", side_effect=side):
            with patch("src.scrapers.reddit_image_downloader.time.sleep"):
                paths = download_post_images(
                    ["https://i.redd.it/fail.jpg", "https://i.redd.it/ok.jpg"],
                    self.tmp, "post1", delay=0,
                )
        self.assertEqual(len(paths), 1)
        self.assertIn("post1_1", paths[0])

    def test_existing_file_skipped(self):
        """已存在的文件直接跳过，不重复下载。"""
        # download_post_images 生成的文件名格式：{post_id}_{idx}{ext}
        existing = os.path.join(self.tmp, "post1_0.jpg")
        with open(existing, "wb") as f:
            f.write(b"cached")
        with patch("src.scrapers.reddit_image_downloader.requests.get") as mock_get:
            paths = download_post_images(
                ["https://i.redd.it/img.jpg"], self.tmp, "post1", delay=0
            )
        mock_get.assert_not_called()
        self.assertEqual(len(paths), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 5. RunState checkpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestRunState(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_creates_fresh_state(self):
        s = RunState.load(self.tmp)
        self.assertEqual(s.posts_fetched, 0)
        self.assertEqual(len(s.seen_ids), 0)

    def test_save_and_reload(self):
        s = RunState.load(self.tmp)
        s.seen_ids.add("abc")
        s.posts_fetched = 7
        s.save()

        s2 = RunState.load(self.tmp)
        self.assertIn("abc", s2.seen_ids)
        self.assertEqual(s2.posts_fetched, 7)

    def test_corrupt_checkpoint_starts_fresh(self):
        path = os.path.join(self.tmp, "checkpoint.json")
        with open(path, "w") as f:
            f.write("{{invalid json}}")
        s = RunState.load(self.tmp)
        self.assertEqual(s.posts_fetched, 0)

    def test_seen_ids_prevent_duplicates_in_fetch_multi(self):
        """fetch_multi 通过 state.seen_ids 自动去重。"""
        xml = _make_xml([{"id": "dup1"}, {"id": "new1"}])
        collector = RedditRSSCollector()
        state = RunState.load(self.tmp)
        state.seen_ids.add("dup1")

        with patch.object(collector, "_fetch_with_retry", return_value=xml):
            posts = collector.fetch_multi(["test"], limit_each=10, state=state)

        ids = [p["id"] for p in posts]
        self.assertNotIn("dup1", ids)
        self.assertIn("new1", ids)

    def test_subreddit_failure_does_not_abort_others(self):
        """单个 subreddit 网络失败，其余继续（CLAUDE.md Layer 2）。"""
        good_xml = _make_xml([{"id": "g1"}])
        collector = RedditRSSCollector()
        state = RunState.load(self.tmp)

        def fake_fetch(url, params, retries=2):
            return None if "bad_sub" in url else good_xml

        with patch.object(collector, "_fetch_with_retry", side_effect=fake_fetch):
            posts = collector.fetch_multi(["bad_sub", "good_sub"],
                                          limit_each=5, state=state)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "g1")
        self.assertEqual(len(state.errors), 1)


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Reddit 图片抓取模块 — 离线单元测试")
    print("=" * 60)
    unittest.main(verbosity=2)
