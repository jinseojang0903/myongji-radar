import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.daily import SEOUL, recover_state, run_daily
from collector.locking import CollectorLock

ROOT = Path(__file__).resolve().parent.parent
CONFIG = '''[defaults]
adapter = "k2web"
title_selector = ".artclViewTitle"
body_selector = ".artclView"
attachment_selector = ".attfile a[href]"
[[sources]]
id = "test"
name = "Test"
rss_url = "https://example.com/rss"
allowed_hosts = ["example.com"]
'''


class DailyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.config = self.root / "sources.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.rules = ROOT / "config/rules.toml"
        self.today = datetime(2026, 11, 4, 21, tzinfo=SEOUL)
        self.numbers = [1]
        self.fail_details = set()
        self.fetcher = Mock()
        self.fetcher.get.side_effect = self.fetch
        self.patcher = patch("collector.__main__.Fetcher", return_value=self.fetcher)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fetch(self, url, hosts):
        self.assertEqual(hosts, ["example.com"])
        if url.endswith("/rss"):
            items = ''.join(f'<item><title>Private title {n}</title><link>/bbs/test/1/{n}/artclView.do</link></item>' for n in self.numbers)
            return f'<rss><channel>{items}</channel></rss>'.encode()
        number = int(url.split('/')[-2])
        if number in self.fail_details:
            raise RuntimeError("PRIVATE BODY MUST NOT APPEAR IN DAILY LOG")
        return f'<h2 class="artclViewTitle">Private title {number}</h2><div class="artclView">PRIVATE BODY {number}</div>'.encode()

    def run_daily(self):
        return run_daily(self.data, self.config, self.rules, clock=lambda: self.today)

    def state(self):
        return json.loads((self.data / "daily_status.json").read_text(encoding="utf-8"))

    def events(self):
        return [json.loads(line) for path in (self.data / "logs").glob("*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]

    def test_two_simulated_days_keep_previous_data_and_run_analysis(self):
        self.assertEqual(self.run_daily(), 0)
        old = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]
        self.today = self.today.replace(day=5)
        self.numbers = [1, 2]
        self.assertEqual(self.run_daily(), 0)
        current = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"]
        self.assertEqual(len(current), 2)
        self.assertEqual(current[0]["version_id"], old["version_id"])
        self.assertEqual(self.state()["status"], "success")
        self.assertEqual(self.state()["sources"], [{"source": "test", "new": 1, "changed": 0, "unchanged": 1, "errors": 0}])
        self.assertEqual(len(json.loads((self.data / "analysis.json").read_text(encoding="utf-8"))["notices"]), 2)
        self.assertEqual(len(list((self.data / "logs").glob("*.jsonl"))), 2)
        logs = json.dumps(self.events())
        self.assertNotIn("PRIVATE BODY", logs)
        self.assertNotIn("Private title", logs)

    def test_same_day_retry_is_skipped_without_network_or_state_overwrite(self):
        self.assertEqual(self.run_daily(), 0)
        calls = self.fetcher.get.call_count
        state = (self.data / "daily_status.json").read_bytes()
        self.assertEqual(self.run_daily(), 0)
        self.assertEqual(self.fetcher.get.call_count, calls)
        self.assertEqual((self.data / "daily_status.json").read_bytes(), state)
        self.assertEqual(self.events()[-1]["reason"], "already_attempted_today")

    def test_busy_daily_and_manual_commands_do_not_open_database(self):
        with CollectorLock(self.data):
            self.assertEqual(self.run_daily(), 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["run", "--config", str(self.config), "--data-dir", str(self.data)]), 2)
        self.fetcher.get.assert_not_called()
        self.assertFalse((self.data / "collector.sqlite3").exists())
        self.assertFalse((self.data / "daily_status.json").exists())

    def test_total_collection_failure_preserves_old_analysis(self):
        self.assertEqual(self.run_daily(), 0)
        old = (self.data / "analysis.json").read_bytes()
        self.today = self.today.replace(day=5)
        self.fail_details = {1}
        self.assertEqual(self.run_daily(), 1)
        self.assertEqual((self.data / "analysis.json").read_bytes(), old)
        self.assertEqual(self.state()["status"], "failed")
        self.assertIsNone(self.state()["analysis_exit_code"])
        self.assertEqual(self.state()["sources"][0]["errors"], 1)
        self.assertNotIn("PRIVATE BODY", json.dumps(self.events()))

    def test_partial_failure_analyzes_successful_notices_and_returns_failure(self):
        self.numbers = [1, 2]
        self.fail_details = {1}
        self.assertEqual(self.run_daily(), 1)
        self.assertEqual(self.state()["status"], "partial_failure")
        self.assertEqual(self.state()["analysis_exit_code"], 0)
        self.assertEqual(self.state()["sources"][0]["new"], 1)
        self.assertEqual(len(json.loads((self.data / "analysis.json").read_text(encoding="utf-8"))["notices"]), 1)

    def test_analysis_system_exit_is_logged_and_keeps_previous_output(self):
        self.assertEqual(self.run_daily(), 0)
        previous = (self.data / "analysis.json").read_bytes()
        self.today = self.today.replace(day=5)
        with patch("collector.__main__.analyze_file", side_effect=ValueError("PRIVATE BODY")):
            self.assertEqual(self.run_daily(), 1)
        self.assertEqual(self.state()["analysis_exit_code"], 1)
        self.assertEqual(self.state()["status"], "partial_failure")
        self.assertEqual((self.data / "analysis.json").read_bytes(), previous)
        self.assertEqual(self.events()[-1]["event"], "finished")

    def test_bad_config_can_be_fixed_before_any_network_attempt(self):
        self.config.write_text("sources = []", encoding="utf-8")
        self.assertEqual(self.run_daily(), 1)
        self.assertFalse(self.state()["collection_attempted"])
        self.fetcher.get.assert_not_called()
        self.config.write_text(CONFIG, encoding="utf-8")
        self.assertEqual(self.run_daily(), 0)

    def test_corrupt_daily_state_does_not_allow_duplicate_requests(self):
        self.data.mkdir()
        path = self.data / "daily_status.json"
        path.write_bytes(b'{"bad":')
        self.assertEqual(self.run_daily(), 1)
        self.fetcher.get.assert_not_called()
        self.assertEqual(path.read_bytes(), b'{"bad":')
        self.assertEqual(self.events()[-1]["reason"], "invalid_daily_state")

    def test_collection_system_exit_does_not_analyze_a_previous_run(self):
        self.assertEqual(self.run_daily(), 0)
        previous = (self.data / "analysis.json").read_bytes()
        self.today = self.today.replace(day=5)
        with patch("collector.daily.collector_main", side_effect=SystemExit(2)):
            self.assertEqual(self.run_daily(), 1)
        self.assertIsNone(self.state()["collection_run_id"])
        self.assertIsNone(self.state()["analysis_exit_code"])
        self.assertEqual((self.data / "analysis.json").read_bytes(), previous)

    def test_invalid_or_future_state_date_blocks_requests(self):
        self.data.mkdir()
        path = self.data / "daily_status.json"
        for local_date in ("invalid-date", "20261104", "2026-11-05"):
            with self.subTest(local_date=local_date):
                state = {"local_date": local_date, "collection_attempted": True, "status": "collecting"}
                path.write_text(json.dumps(state), encoding="utf-8")
                self.assertEqual(self.run_daily(), 1)
                self.fetcher.get.assert_not_called()
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)

    def test_process_lock_blocks_another_process_and_is_released(self):
        code = '''import sys
from pathlib import Path
from collector.locking import CollectorLock, AlreadyRunning
try:
    with CollectorLock(Path(sys.argv[1])): pass
except AlreadyRunning:
    raise SystemExit(2)
'''
        with CollectorLock(self.data):
            result = subprocess.run([sys.executable, "-c", code, str(self.data)], cwd=ROOT, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
        result = subprocess.run([sys.executable, "-c", code, str(self.data)], cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.data / "collector.lock").exists())

    def test_recovery_backs_up_bytes_blocks_today_and_resumes_tomorrow(self):
        self.data.mkdir()
        original = b'\xff{"broken":'
        path = self.data / "daily_status.json"
        path.write_bytes(original)
        self.assertEqual(recover_state(self.data, clock=lambda: self.today), 0)
        backup, = (self.data / "state_backups").glob("*.bin")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(self.state()["reason"], "manual_state_recovery_skip_today")
        self.assertEqual(self.run_daily(), 0)
        self.fetcher.get.assert_not_called()
        self.today = self.today.replace(day=5)
        self.assertEqual(self.run_daily(), 0)
        self.assertEqual(self.state()["status"], "success")

    def test_recovery_leaves_healthy_or_missing_state_unchanged(self):
        self.assertEqual(recover_state(self.data, clock=lambda: self.today), 1)
        self.assertFalse(self.data.exists())
        self.assertEqual(self.run_daily(), 0)
        before = (self.data / "daily_status.json").read_bytes()
        self.assertEqual(recover_state(self.data, clock=lambda: self.today), 0)
        self.assertEqual((self.data / "daily_status.json").read_bytes(), before)
        self.assertFalse((self.data / "state_backups").exists())

    def test_recovery_requires_lock_and_preserves_original_if_backup_fails(self):
        self.data.mkdir()
        path = self.data / "daily_status.json"
        path.write_bytes(b"broken")
        with CollectorLock(self.data):
            self.assertEqual(recover_state(self.data, clock=lambda: self.today), 2)
        self.assertEqual(path.read_bytes(), b"broken")
        # 백업 폴더 자리에 파일이 있으면 백업 실패. 손상 상태도 그대로 보존해야 한다.
        (self.data / "state_backups").write_bytes(b"not a directory")
        self.assertEqual(recover_state(self.data, clock=lambda: self.today), 1)
        self.assertEqual(path.read_bytes(), b"broken")


if __name__ == "__main__":
    unittest.main()
