import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from collector.assets import AssetCollector
from collector.fetcher import FetchPolicyError, MAX_FILE_BYTES, ResponseTooLarge
from collector.models import Notice
from collector.repository import Repository


class AssetPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)

    def notice(self):
        return Notice("test", "1", "https://www.mju.ac.kr/post", "title", "2026-11-01", "body",
                      attachments=[{"url": "https://www.mju.ac.kr/file", "name": "file"}])

    def test_permanent_and_transient_failures_are_distinct(self):
        def http_error(status):
            response = requests.Response()
            response.status_code = status
            return requests.HTTPError("private body", response=response)

        for error, status in ((FetchPolicyError("host"), "skipped"), (ResponseTooLarge("big"), "skipped"),
                              (http_error(404), "skipped"), (http_error(403), "skipped"),
                              (http_error(429), "failed"), (http_error(500), "failed"),
                              (requests.Timeout("timeout"), "failed")):
            with self.subTest(error=type(error).__name__, status=status):
                notice = self.notice()
                events = AssetCollector(self.data, Mock(get=Mock(side_effect=error))).enrich(notice, ["www.mju.ac.kr"])
                self.assertEqual(notice.attachments[0]["download_status"], status)
                self.assertEqual(events[0]["severity"], "warning" if status == "skipped" else "error")
                self.assertNotIn("private body", json.dumps(events))

    def test_conversion_exception_keeps_new_download_success(self):
        notice = self.notice()
        previous = {"attachments": [{**notice.attachments[0], "sha256": "old", "local_path": "assets/old.bin"}]}
        with patch("collector.assets.convert_document", side_effect=RuntimeError("private content")):
            events = AssetCollector(self.data, Mock(get=Mock(return_value=b"new bytes"))).enrich(notice, ["www.mju.ac.kr"], previous)
        item = notice.attachments[0]
        self.assertEqual(events, [])
        self.assertEqual(item["sha256"], hashlib.sha256(b"new bytes").hexdigest())
        self.assertEqual(item["download_status"], "downloaded")
        self.assertEqual(item["text_status"], "needs_review")
        self.assertEqual(item["text_reason"], "conversion_RuntimeError")
        self.assertEqual((self.data / item["local_path"]).read_bytes(), b"new bytes")

    def test_same_file_text_cache_reused_across_runs(self):
        with patch("collector.assets.convert_document", return_value=("extracted text", "")) as convert:
            for _ in range(2):
                notice = self.notice()
                collector = AssetCollector(self.data, Mock(get=Mock(return_value=b"same bytes")))
                collector.enrich(notice, ["www.mju.ac.kr"])
                self.assertEqual(notice.attachments[0]["text_status"], "extracted")
        self.assertEqual(convert.call_count, 1)

    def test_run_request_byte_and_time_limits_stop_more_downloads(self):
        for constant, limit in (("MAX_RUN_REQUESTS", 0), ("MAX_RUN_BYTES", 0), ("MAX_RUN_SECONDS", 0)):
            with self.subTest(constant=constant):
                fetcher = Mock()
                with patch("collector.assets." + constant, limit):
                    events = AssetCollector(self.data, fetcher).enrich(self.notice(), ["www.mju.ac.kr"])
                fetcher.get.assert_not_called()
                self.assertEqual(events[0]["severity"], "warning")

    def test_metadata_refresh_preserves_version_url_and_date(self):
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        first = self.notice()
        repo.save(first, b"html")
        changed_metadata = replace(first, url=first.url + "?new=1", published_at="2026-11-07",
                                   attachments=[{**first.attachments[0], "download_status": "skipped"}])
        self.assertEqual(repo.save(changed_metadata, b"html"), "unchanged")
        current = repo.latest_payload("test", "1")
        self.assertEqual(current["url"], first.url)
        self.assertEqual(current["published_at"], first.published_at)
        self.assertEqual(current["attachments"][0]["download_status"], "skipped")
