"""수집 전체 예산은 합성 입력과 가짜 시계로 검증한다."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.assets import AssetCollector
from collector.daily import run_daily
from collector.fetcher import RunDeadlineExceeded
from collector.models import Notice
from collector.repository import Repository
from test_collector_hardening import CONFIG

ROOT = Path(__file__).resolve().parents[1]
RSS = b'<rss><channel><item><title>good</title><link>/bbs/test/1/1/artclView.do</link></item></channel></rss>'
DETAIL = b'<h1>good</h1><div class="body">body</div>'


class RunDeadlineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "sources.toml"
        self.config.write_text(CONFIG + '\n[[sources]]\nid="other"\nname="Other"\nrss_url="https://test.mju.ac.kr/other"\nallowed_hosts=["test.mju.ac.kr"]\n', encoding="utf-8")
        self.data = self.root / "data"
        self.now = 0.0
        self.fetcher = Mock()

    def fetch(self, url, hosts):
        if url.endswith('/rss'):
            return RSS
        self.now = 11.0
        return DETAIL

    def test_deadline_preserves_saved_notice_and_stops_next_source(self):
        self.fetcher.get.side_effect = self.fetch
        with patch("collector.__main__.Fetcher", return_value=self.fetcher) as factory, \
                patch("collector.__main__.time.monotonic", side_effect=lambda: self.now), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--config", str(self.config), "--data-dir", str(self.data), "--run-timeout", "10"]), 1)
        factory.assert_called_once_with(run_deadline=10.0)
        self.assertEqual(self.fetcher.get.call_count, 2)
        self.assertEqual(len(json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"]), 1)
        repo = Repository(self.data)
        try:
            finished, result = repo.db.execute("SELECT finished_at,result_json FROM runs").fetchone()
            self.assertTrue(finished)
            rows = json.loads(result)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["new"], 1)
            self.assertEqual(rows[0]["errors"][-1]["error"], "run_deadline_exceeded")
        finally:
            repo.close()

    def test_deadline_during_discovery_finishes_run_without_more_requests(self):
        self.fetcher.get.side_effect = RunDeadlineExceeded("budget")
        with patch("collector.__main__.Fetcher", return_value=self.fetcher), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--config", str(self.config), "--data-dir", str(self.data)]), 1)
        self.assertEqual(self.fetcher.get.call_count, 1)
        repo = Repository(self.data)
        try:
            finished, result = repo.db.execute("SELECT finished_at,result_json FROM runs").fetchone()
            self.assertTrue(finished)
            self.assertEqual(json.loads(result)[0]["errors"][0]["error"], "run_deadline_exceeded")
        finally:
            repo.close()

    def test_invalid_budget_stops_before_database_or_fetcher(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), patch("collector.__main__.Fetcher") as fetcher, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["run", "--config", str(self.config), "--data-dir", str(self.data), "--run-timeout", value])
            fetcher.assert_not_called()
            self.assertFalse(self.data.exists())

    def test_expired_budget_prevents_asset_cache_and_conversion_work(self):
        self.fetcher.run_deadline = 10.0
        notice = Notice("test", "1", "https://test.mju.ac.kr/post", "title", None, "body",
                        attachments=[{"url": "https://test.mju.ac.kr/doc.pdf", "name": "doc.pdf"}])
        assets = AssetCollector(self.data, self.fetcher)
        with patch("collector.assets.time.monotonic", return_value=11.0), patch.object(assets, "convert") as convert:
            errors = assets.enrich(notice, ["test.mju.ac.kr"])
        self.fetcher.get.assert_not_called()
        convert.assert_not_called()
        self.assertEqual(errors[0]["severity"], "error")
        self.assertEqual(notice.attachments[0]["download_status"], "failed")

    def test_daily_marks_completed_deadline_run_partial_failure(self):
        self.fetcher.get.side_effect = self.fetch
        with patch("collector.__main__.Fetcher", return_value=self.fetcher), \
                patch("collector.__main__.time.monotonic", side_effect=lambda: self.now):
            code = run_daily(self.data, self.config, ROOT / "config/rules.toml", run_timeout=10,
                             clock=lambda: datetime(2026, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(code, 1)
        state = json.loads((self.data / "daily_status.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "partial_failure")
        self.assertEqual(state["collection_exit_code"], 1)
        self.assertEqual(state["analysis_exit_code"], 0)

    def test_download_finished_after_budget_keeps_bytes_without_starting_converter(self):
        self.fetcher.run_deadline = 10.0
        def download(*args):
            self.now = 11.0
            return b"%PDF-synthetic"
        self.fetcher.get.side_effect = download
        notice = Notice("test", "1", "https://test.mju.ac.kr/post", "title", None, "body",
                        attachments=[{"url": "https://test.mju.ac.kr/doc.pdf", "name": "doc.pdf"}])
        with patch("collector.assets.time.monotonic", side_effect=lambda: self.now), \
                patch("collector.assets.convert_document") as convert:
            assets = AssetCollector(self.data, self.fetcher)
            assets.enrich(notice, ["test.mju.ac.kr"])
        convert.assert_not_called()
        item = notice.attachments[0]
        self.assertEqual(item["download_status"], "downloaded")
        self.assertEqual(item["text_status"], "needs_review")
        self.assertEqual((self.data / item["local_path"]).read_bytes(), b"%PDF-synthetic")


if __name__ == "__main__":
    unittest.main()
