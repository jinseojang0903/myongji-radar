import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.adapters.k2web import K2WebAdapter
from collector.config import load_sources
from collector.models import Notice, NoticeRef, Source
from collector.repository import Repository
from collector.text_safety import safe_terminal


CONFIG = '''[defaults]
adapter="k2web"
title_selector="h1"
body_selector=".body"
attachment_selector=".files a[href]"
[[sources]]
id="test"
name="Test"
rss_url="https://test.mju.ac.kr/rss"
allowed_hosts=["test.mju.ac.kr"]
'''


class CollectorHardeningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = Source("test", "test", "k2web", "https://test.mju.ac.kr/rss", ["test.mju.ac.kr"],
                             "h1", ".body", ".files a[href]", list_url="https://test.mju.ac.kr/list")

    def test_rss_bad_dates_and_links_are_isolated_and_errors_reset(self):
        adapter = K2WebAdapter()
        def item(number, date):
            return f'<item><title>title</title><link>/bbs/test/1/{number}/artclView.do</link><pubDate>{date}</pubDate></item>'
        xml = '<rss><channel>' + item(1, '2026-11-07') + item(2, 'bad-date') + item(3, '2026-11-08') + '<item/>' + '</channel></rss>'
        refs = adapter.discover(xml.encode(), self.source)
        self.assertEqual([ref.external_id for ref in refs], ["test/1/1", "test/1/3"])
        self.assertEqual(len(adapter.discovery_errors), 2)
        self.assertEqual(adapter.discovery_errors[0]["url"], "https://test.mju.ac.kr/bbs/test/1/2/artclView.do")
        adapter.discover(('<rss><channel>' + item(1, '2026-11-07') + '</channel></rss>').encode(), self.source)
        self.assertEqual(adapter.discovery_errors, [])

    def test_html_bad_dates_and_links_are_isolated(self):
        adapter = K2WebAdapter()
        def row(url, date):
            return f'<tr><td><a class="artclLinkView" href="{url}">title</a></td><td class="_artclTdRdate">{date}</td></tr>'
        html = ('<table><tbody>' + row('/bbs/test/1/1/artclView.do', '2026.11.07')
                + row('/bbs/test/1/2/artclView.do', '2026.02.30') + row('/unknown', '2026.11.07') + '</tbody></table>')
        refs = adapter.discover(html.encode(), replace(self.source, discovery="html"))
        self.assertEqual([ref.external_id for ref in refs], ["test/1/1"])
        self.assertEqual(len(adapter.discovery_errors), 2)

    def test_cli_saves_valid_notices_and_records_discovery_errors(self):
        config = self.root / "sources.toml"
        config.write_text(CONFIG, encoding="utf-8")
        data = self.root / "data"
        rss = b'<rss><channel><item><title>good</title><link>/bbs/test/1/1/artclView.do</link></item><item><link>/bbs/test/1/2/artclView.do</link><pubDate>invalid</pubDate></item></channel></rss>'
        fetcher = Mock()
        fetcher.get.side_effect = [rss, b'<h1>good</h1><div class="body">body</div>']
        with patch("collector.__main__.Fetcher", return_value=fetcher), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--config", str(config), "--data-dir", str(data)]), 1)
        self.assertEqual(fetcher.get.call_count, 2)
        notices = json.loads((data / "notices.json").read_text(encoding="utf-8"))["notices"]
        self.assertEqual(len(notices), 1)
        repo = Repository(data)
        try:
            result = json.loads(repo.db.execute("SELECT result_json FROM runs").fetchone()[0])[0]
            self.assertEqual(result["new"], 1)
            self.assertEqual(result["errors"], [{"url": "https://test.mju.ac.kr/bbs/test/1/2/artclView.do", "error": "invalid_rss_item"}])
        finally:
            repo.close()

    def test_escape_sequences_and_bidi_are_removed_but_korean_layout_is_kept(self):
        raw = '제목\x1b[31m빨강\x1b[0m\x9b2J\u202e\x07\n본문\t줄'
        self.assertEqual(safe_terminal(raw), '제목빨강\n본문\t줄')
        html = ('<h1>' + raw + '</h1><div class="body">본문\x1b[2J\u202e내용</div>').encode()
        notice = K2WebAdapter().parse_detail(html, NoticeRef("test/1/1", "https://test.mju.ac.kr/post", "", None), self.source)
        self.assertNotIn('\x1b', notice.title)
        self.assertEqual(notice.body_text, '본문내용')
        self.assertEqual(notice.parser_version, 'k2web-v1.1')

    def test_raw_replace_failure_preserves_database_and_cleans_temporary_file(self):
        repo = Repository(self.root / "data")
        try:
            first = Notice("test", "1", "https://test.mju.ac.kr/post", "title", None, "first")
            repo.save(first, b"first raw")
            before = repo.db.execute("SELECT * FROM notice_versions").fetchall()
            with patch.object(Path, "replace", side_effect=OSError("write failed")), self.assertRaises(OSError):
                repo.save(replace(first, body_text="new"), b"new raw")
            self.assertEqual(repo.db.execute("SELECT * FROM notice_versions").fetchall(), before)
            files = list((self.root / "data/raw").iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"first raw")
        finally:
            repo.close()

    def test_unterminated_osc_repetition_does_not_stall_parser(self):
        result = subprocess.run([sys.executable, "-c",
                                 "from collector.text_safety import safe_terminal; print(len(safe_terminal(chr(157)*100000)))"],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=5, check=True)
        self.assertEqual(result.stdout.strip(), b"0")

    def test_host_allowlist_rejects_external_suffix_tricks_and_credentials_at_startup(self):
        for host in ("example.com", "evil-mju.ac.kr", "www.mju.ac.kr.evil.example", "mju.ac.kr", "*.mju.ac.kr", "WWW.MJU.AC.KR", "www.mju.ac.kr:443"):
            with self.subTest(host=host):
                path = self.root / "sources.toml"
                path.write_text(CONFIG.replace('allowed_hosts=["test.mju.ac.kr"]', 'allowed_hosts=["' + host + '"]'), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "allowed_hosts"):
                    load_sources(path)
        for url in ("http://test.mju.ac.kr/rss", "https://evil.example/rss", "https://user@test.mju.ac.kr/rss", "https://test.mju.ac.kr:444/rss"):
            path.write_text(CONFIG.replace('https://test.mju.ac.kr/rss', url), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rss_url"):
                load_sources(path)

    def test_invalid_host_cli_stops_before_database_or_network(self):
        config = self.root / "sources.toml"
        config.write_text(CONFIG.replace('test.mju.ac.kr', 'evil.example'), encoding="utf-8")
        data = self.root / "data"
        with patch("collector.__main__.Fetcher") as fetcher, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["run", "--config", str(config), "--data-dir", str(data)])
        fetcher.assert_not_called()
        self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
