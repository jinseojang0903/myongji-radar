"""수동 공개의 잠금과 일일 실패 상태 보존. 실제 AI·학교 요청·푸시는 없다."""
import json
import unittest
from unittest.mock import patch

from collector import daily
from collector.locking import CollectorLock
import test_publisher as publishing
import test_daily as collection


class PublisherLockTests(unittest.TestCase):
    def test_manual_publisher_stops_before_work_when_collector_is_running(self):
        fixture = publishing.PublisherTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fake = publishing.FakeRun()
        with CollectorLock(fixture.data):
            code, _ = fixture.run_cli(fake)
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])
        self.assertFalse(fixture.cards.exists())
        self.assertFalse((fixture.data / "extractions").exists())


class DailyFailureCodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = collection.DailyTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_collection_failure_is_not_changed_into_success_on_repeat(self):
        f = self.fixture
        f.numbers = [1, 2]
        f.fail_details = {1}
        self.assertEqual(f.run_daily(), 1)
        before = (f.data / "daily_status.json").read_bytes()
        calls = f.fetcher.get.call_count
        self.assertEqual(f.run_daily(), 1)
        self.assertEqual(f.fetcher.get.call_count, calls)
        self.assertEqual((f.data / "daily_status.json").read_bytes(), before)
        self.assertEqual(f.events()[-1]["exit_code"], 1)

    def test_analysis_failure_is_not_changed_into_success_on_repeat(self):
        original = daily.call_collector
        def call(argv):
            return 1 if argv[0] == "analyze" else original(argv)
        with patch.object(daily, "call_collector", side_effect=call) as execute:
            self.assertEqual(self.fixture.run_daily(), 1)
            count = execute.call_count
            self.assertEqual(self.fixture.run_daily(), 1)
            self.assertEqual(execute.call_count, count)

    def test_interrupted_collection_is_not_success(self):
        f = self.fixture
        f.data.mkdir()
        (f.data / "daily_status.json").write_text(json.dumps({
            "local_date": f.today.date().isoformat(), "collection_attempted": True, "status": "collecting",
        }), encoding="utf-8")
        self.assertEqual(f.run_daily(), 1)
        f.fetcher.get.assert_not_called()
