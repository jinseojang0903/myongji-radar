import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from collector.config import load_sources


ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = '''
[defaults]
adapter = "k2web"
title_selector = ".title"
body_selector = ".body"
attachment_selector = ".files a[href]"
'''
RSS_SOURCE = '''
[[sources]]
id = "example"
name = "Example board"
rss_url = "https://example.com/rss"
allowed_hosts = ["example.com"]
'''


class SourceConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.toml"
            path.write_text(dedent(text), encoding="utf-8")
            return load_sources(path)

    def test_defaults_and_source_overrides(self):
        source, = self.load(DEFAULTS + RSS_SOURCE + 'body_selector = ".custom"\n')
        self.assertEqual(source.adapter, "k2web")
        self.assertEqual(source.title_selector, ".title")
        self.assertEqual(source.body_selector, ".custom")
        self.assertEqual(source.attachment_selector, ".files a[href]")
        self.assertEqual(source.discovery, "rss")

    def test_priority_is_sorted_and_ties_keep_file_order(self):
        entries = "".join(RSS_SOURCE.replace('id = "example"', f'id = "{name}"')
                          + f"priority = {priority}\n" for name, priority in
                          [("last", 90), ("first", 1), ("second", 1)])
        self.assertEqual([s.id for s in self.load(DEFAULTS + entries)], ["first", "second", "last"])

    def test_html_does_not_require_rss_url(self):
        html = RSS_SOURCE.replace('rss_url = "https://example.com/rss"',
                                  'discovery = "html"\nlist_url = "https://example.com/list"')
        source, = self.load(DEFAULTS + html)
        self.assertEqual(source.rss_url, "")
        self.assertEqual(source.discovery, "html")

    def test_discovery_requires_matching_url(self):
        for mode, field in [("rss", "rss_url"), ("html", "list_url")]:
            with self.subTest(mode=mode):
                text = RSS_SOURCE.replace('rss_url = "https://example.com/rss"', f'discovery = "{mode}"')
                with self.assertRaisesRegex(ValueError, field):
                    self.load(DEFAULTS + text)

    def test_invalid_discovery_and_adapter(self):
        for addition, message in [('discovery = "browser"', "discovery"),
                                  ('adapter = "unknown"', "미등록 어댑터")]:
            with self.subTest(addition=addition), self.assertRaisesRegex(ValueError, message):
                self.load(DEFAULTS + RSS_SOURCE + addition)

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "출처 ID 중복"):
            self.load(DEFAULTS + RSS_SOURCE + RSS_SOURCE)

    def test_empty_tables_and_wrong_types_are_rejected(self):
        for text, message in [('sources = []', "sources"), ('defaults = []\nsources = []', "defaults"),
                              ('[sources]\nid = "example"', "sources")]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self.load(text)

    def test_invalid_fields_are_rejected(self):
        cases = [
            (RSS_SOURCE + 'unexpected = "field"', "설정 필드"),
            (RSS_SOURCE.replace('id = "example"', 'id = " "'), "id"),
            (RSS_SOURCE.replace('["example.com"]', '[]'), "allowed_hosts"),
            (RSS_SOURCE.replace('["example.com"]', '"example.com"'), "allowed_hosts"),
            (RSS_SOURCE.replace('["example.com"]', '[4]'), "allowed_hosts"),
            (RSS_SOURCE + 'priority = true', "priority"),
            (RSS_SOURCE + 'repair_rss_ampersands = "true"', "repair_rss_ampersands"),
            (RSS_SOURCE + 'title_selector = ""', "title_selector"),
            (RSS_SOURCE.replace('rss_url = "https://example.com/rss"', 'rss_url = 42'), "rss_url"),
        ]
        for entry, message in cases:
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, message):
                self.load(DEFAULTS + entry)

    def test_invalid_cli_config_fails_before_creating_database(self):
        # CLI 설정 오류는 외부 요청이나 DB 생성 전에 끝나야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "invalid.toml"
            config.write_text(DEFAULTS + RSS_SOURCE.replace('rss_url = "https://example.com/rss"', ''), encoding="utf-8")
            data = Path(tmp) / "data"
            result = subprocess.run([sys.executable, "-m", "collector", "run", "--config", str(config),
                                     "--data-dir", str(data)], cwd=ROOT, capture_output=True, timeout=10,
                                    env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(result.returncode, 2)
            self.assertIn("출처 설정 오류", result.stderr.decode("utf-8"))
            self.assertFalse(data.exists())

    def test_registered_sources_keep_ids_and_discovery(self):
        sources = load_sources(ROOT / "config/sources.toml")
        self.assertEqual([s.id for s in sources], ["mju_startup", "mju_abix", "mju_innovation",
                                                 "mju_ecc", "mju_scict", "mju_general", "mju_ctl"])
        self.assertEqual([s.id for s in sources if s.discovery == "html"], ["mju_innovation"])
        self.assertTrue(all(s.title_selector == ".artclViewTitle" for s in sources))


if __name__ == "__main__":
    unittest.main()
