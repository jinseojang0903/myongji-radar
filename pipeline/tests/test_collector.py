import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from collector.adapters.k2web import K2WebAdapter
from collector.fetcher import Fetcher
from collector.models import Notice, NoticeRef, Source
from collector.repository import Repository


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.source = Source("test", "테스트", "k2web", "https://example.com/rss",
                             ["example.com"], ".artclViewTitle", ".artclView", ".attfile a[href]")
        self.adapter = K2WebAdapter()

    def test_rss_relative_url_date_and_duplicate(self):
        item = """<item><title>예시</title><link>/bbs/test/1/2/artclView.do</link>
                  <pubDate>2026-11-02 10:24:28.0</pubDate></item>"""
        refs = self.adapter.discover(f"<rss><channel>{item}{item}</channel></rss>".encode(), self.source)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].url, "https://example.com/bbs/test/1/2/artclView.do")
        self.assertEqual(refs[0].published_at, "2026-11-02T10:24:28+09:00")

    def test_image_only_body_and_attachment(self):
        html = b'''<h2 class="artclViewTitle">Title</h2>
            <div class="artclView"><script>ignore()</script><img src="/poster.png"></div>
            <div class="attfile"><a href="/file/download.do">file.pdf</a></div>'''
        notice = self.adapter.parse_detail(html, NoticeRef("test/1/2", "https://example.com/post", "", None), self.source)
        self.assertEqual(notice.body_text, "")
        self.assertEqual(notice.images[0]["url"], "https://example.com/poster.png")
        self.assertEqual(notice.attachments[0]["url"], "https://example.com/file/download.do")

    def test_broken_ampersand_repair_preserves_entities_and_cdata(self):
        xml = b'''<rss><channel><item><title>A & B &amp; C</title>
            <link>/bbs/test/1/2/artclView.do</link><description><![CDATA[X & Y]]></description>
            </item></channel></rss>'''
        refs = self.adapter.discover(xml, replace(self.source, repair_rss_ampersands=True))
        self.assertEqual(refs[0].title, "A & B & C")

    def test_html_discovery_ignores_new_badge_and_deduplicates(self):
        row = b'''<tr><td><a class="artclLinkView" href="/bbs/test/1/2/artclView.do">
            <strong>Title</strong><span>NEW</span></a></td><td class="_artclTdRdate">2026.11.02</td></tr>'''
        refs = self.adapter.discover(b'<table><tbody>'+row+row+b'</tbody></table>',
            replace(self.source, discovery="html", list_url="https://example.com/list"))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].title, "Title")
        self.assertEqual(refs[0].published_at, "2026-11-02")

    def test_unsupported_rss_is_not_a_notice(self):
        refs = self.adapter.discover(b'<rss><channel><item><title>NOT SUPPORT RSS</title><link/></item></channel></rss>', self.source)
        self.assertEqual(refs, [])
        self.assertEqual(self.adapter.discovery_errors, [{"url": self.source.rss_url, "error": "invalid_rss_item"}])

    def test_html_pinned_markers_do_not_depend_on_notice_title(self):
        html = '''<table><tbody>
            <tr class="headline"><td class="_artclTdNum">1</td><td>
                <a class="artclLinkView" href="/bbs/test/1/1/artclView.do"><strong>First</strong></a></td></tr>
            <tr><td class="_artclTdNum"><span class="_artclNotice">Pinned</span></td><td>
                <a class="artclLinkView" href="/bbs/test/1/2/artclView.do"><strong>Second</strong></a></td></tr>
            <tr><td class="_artclTdNum">3</td><td>
                <a class="artclLinkView" href="/bbs/test/1/3/artclView.do"><strong>공지 안내</strong></a></td></tr>
            </tbody></table>'''
        refs = self.adapter.discover(html.encode(), replace(self.source, discovery="html", list_url="https://example.com/list"))
        self.assertEqual([ref.is_pinned for ref in refs], [True, True, False])

    def test_html_duplicate_keeps_pinned_marker_in_either_order(self):
        pinned = '<tr class="headline"><td><a class="artclLinkView" href="/bbs/test/1/1/artclView.do">Pinned</a></td></tr>'
        regular = pinned.replace(' class="headline"', '')
        for rows in (pinned + regular, regular + pinned):
            with self.subTest(rows=rows):
                refs = self.adapter.discover(('<table><tbody>' + rows + '</tbody></table>').encode(),
                                             replace(self.source, discovery="html", list_url="https://example.com/list"))
                self.assertEqual(len(refs), 1)
                self.assertTrue(refs[0].is_pinned)

    def test_invalid_pages_fail_explicitly(self):
        with self.assertRaises(ValueError):
            self.adapter.discover(b"<rss><channel/></rss>", self.source)
        with self.assertRaises(ValueError):
            self.adapter.parse_detail(b"<html>login</html>", NoticeRef("2", "https://example.com/post", "", None), self.source)

    def test_duplicate_change_and_revert_preserve_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp))
            try:
                notice = Notice("test", "test/1/2", "https://example.com/post", "제목", None, "처음")
                self.assertEqual(repo.save(notice, b"first"), "new")
                self.assertEqual(repo.save(notice, b"views changed"), "unchanged")
                notice.body_text = "연장"
                self.assertEqual(repo.save(notice, b"extended"), "changed")
                notice.body_text = "처음"
                self.assertEqual(repo.save(notice, b"reverted"), "changed")
                self.assertEqual(repo.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 1)
                self.assertEqual(repo.db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0], 3)
                self.assertEqual(repo.export(Path(tmp) / "notices.json"), 1)
                result = json.loads((Path(tmp) / "notices.json").read_text(encoding="utf-8"))
                self.assertEqual(result["notices"][0]["body_text"], "처음")
            finally:
                repo.close()

    def test_unregistered_host_rejected_before_request(self):
        fetcher = Fetcher()
        try:
            with self.assertRaises(ValueError):
                fetcher.get("https://unregistered.example/post", ["example.com"])
        finally:
            fetcher.close()


if __name__ == "__main__":
    unittest.main()
