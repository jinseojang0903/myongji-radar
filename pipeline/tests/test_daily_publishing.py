"""실제 AI·학교·Git 쓰기 없이 같은 잠금의 매일 공개 연결을 검증한다."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch
import requests

from collector import daily
from collector.locking import AlreadyRunning, CollectorLock
from collector.publish_config import load_publishing
import test_daily as fixtures


class DailyPublishingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DailyTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = self.fixture.root / "publishing.toml"
        self.write_settings()

    def write_settings(self, *, enabled=True, model="opus", push=True):
        self.settings.write_text(f'version=1\nenabled={str(enabled).lower()}\nmodel="{model}"\npush={str(push).lower()}\n', encoding="utf-8")

    def run_day(self):
        return daily.run_daily(self.fixture.data, self.fixture.config, self.fixture.rules,
                               clock=lambda: self.fixture.today, publish_config=self.settings)

    def state(self):
        return self.fixture.state()

    def test_success_and_warning_keep_same_lock_and_do_not_repeat_today(self):
        for published in (0, 1):
            with self.subTest(published=published):
                if published:
                    self.fixture.today = self.fixture.today.replace(day=5)
                def publish(argv):
                    with self.assertRaises(AlreadyRunning), CollectorLock(self.fixture.data):
                        pass
                    self.assertEqual(argv, ["--model", "opus", "--data-dir", str(self.fixture.data), "--push"])
                    return published
                with patch.object(daily, "call_publisher", side_effect=publish) as called:
                    self.assertEqual(self.run_day(), 0)
                    self.assertEqual(self.state()["status"], "success")
                    self.assertEqual(self.state()["publish_exit_code"], published)
                    before = (self.fixture.data / "daily_status.json").read_bytes()
                    self.assertEqual(self.run_day(), 0)
                    called.assert_called_once()
                    self.assertEqual((self.fixture.data / "daily_status.json").read_bytes(), before)

    def test_publish_stopped_or_push_failed_are_not_success(self):
        for code, status in ((2, "failed"), (3, "partial_failure")):
            with self.subTest(code=code), patch.object(daily, "call_publisher", return_value=code):
                if code == 3:
                    self.fixture.today = self.fixture.today.replace(day=5)
                self.assertEqual(self.run_day(), code)
                self.assertEqual(self.state()["publish_exit_code"], code)
                self.assertEqual(self.state()["status"], status)
                self.assertEqual(self.state()["failure_phase"], "publish")

    def test_collection_partial_failure_still_analyzes_but_never_publishes(self):
        self.fixture.numbers = [1, 2]
        self.fixture.fail_details = {1}
        with patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 1)
        publish.assert_not_called()
        self.assertEqual(self.state()["analysis_exit_code"], 0)
        self.assertEqual(self.state()["publish_skipped_reason"], "collection_or_analysis_failed")

    def test_analysis_failure_never_publishes(self):
        original = daily.call_collector
        def collect(argv):
            return 2 if argv[0] == "analyze" else original(argv)
        with patch.object(daily, "call_collector", side_effect=collect), patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 1)
        publish.assert_not_called()
        self.assertEqual(self.state()["failure_phase"], "analysis")

    def test_no_saved_notices_never_publishes(self):
        self.fixture.numbers = []
        with patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 1)
        publish.assert_not_called()
        self.assertIsNone(self.state()["analysis_exit_code"])

    def test_old_completed_run_cannot_be_published_as_todays_collection(self):
        with patch.object(daily, "call_publisher", return_value=0):
            self.assertEqual(self.run_day(), 0)
        old_analysis = (self.fixture.data / "analysis.json").read_bytes()
        self.fixture.today = self.fixture.today.replace(day=5)
        with patch.object(daily, "call_collector", return_value=0) as collect, \
                patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 1)
        collect.assert_called_once()
        publish.assert_not_called()
        self.assertIsNone(self.state()["collection_run_id"])
        self.assertIsNone(self.state()["analysis_exit_code"])
        self.assertEqual((self.fixture.data / "analysis.json").read_bytes(), old_analysis)

    def test_disabled_settings_keep_collect_only(self):
        self.write_settings(enabled=False)
        with patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 0)
        publish.assert_not_called()
        self.assertFalse(self.state()["publish_enabled"])

    def test_invalid_settings_stop_before_any_collection_and_allow_fix(self):
        self.settings.write_text('version=1\nenabled=true\nmodel="--unsafe"\npush=true\n', encoding="utf-8")
        with patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.run_day(), 1)
            self.fixture.fetcher.get.assert_not_called()
            self.assertFalse(self.state()["collection_attempted"])
            self.write_settings()
            publish.return_value = 0
            self.assertEqual(self.run_day(), 0)
        publish.assert_called_once()

    def test_publisher_exception_or_invalid_code_is_failure_without_private_message(self):
        for result in (RuntimeError("PRIVATE_PUBLISH_SECRET"), 99, True, None, False):
            self.fixture.today = self.fixture.today.replace(day=self.fixture.today.day + 1)
            with self.subTest(result=type(result).__name__), patch.object(daily, "call_publisher") as publish:
                if isinstance(result, Exception):
                    publish.side_effect = result
                else:
                    publish.return_value = result
                self.assertEqual(self.run_day(), 1)
                self.assertEqual(self.state()["status"], "failed")
                self.assertEqual(self.state()["failure_phase"], "publish")
                self.assertNotIn("PRIVATE_PUBLISH_SECRET", json.dumps(self.fixture.events()))

    def test_keyboard_interrupt_never_records_publish_as_success(self):
        with patch.object(daily, "call_publisher", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_day()
        self.assertEqual(self.state()["status"], "failed")
        self.assertEqual(self.state()["exit_code"], 1)
        self.assertEqual(self.state()["failure_phase"], "publish")

    def test_warning_only_collection_can_publish_without_push(self):
        self.write_settings(push=False)
        def fetch(url, hosts):
            if url.endswith("/missing.png"):
                response = requests.Response()
                response.status_code = 404
                raise requests.HTTPError("private missing asset", response=response)
            content = self.fixture.fetch(url, hosts)
            return content.replace(b"</div>", b'<img src="/missing.png"></div>') if "artclView" in url else content
        self.fixture.fetcher.get.side_effect = fetch
        with patch.object(daily, "call_publisher", return_value=0) as publish:
            self.assertEqual(self.run_day(), 0)
        self.assertGreater(self.state()["sources"][0]["warnings"], 0)
        self.assertEqual(self.state()["collection_exit_code"], 0)
        self.assertNotIn("--push", publish.call_args.args[0])

    def test_dry_run_checks_publishing_without_collection_or_publisher(self):
        argv = ["collector.daily", "--dry-run", "--config", str(self.fixture.config), "--rules", str(self.fixture.rules),
                "--data-dir", str(self.fixture.data), "--publish-config", str(self.settings)]
        with patch("sys.argv", argv), patch.object(daily, "run_daily") as run, \
                patch.object(daily, "call_publisher") as publish, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(daily.main(), 0)
        run.assert_not_called()
        publish.assert_not_called()
        self.fixture.fetcher.get.assert_not_called()
        self.assertFalse(self.fixture.data.exists())

    def test_omitted_publishing_configuration_keeps_existing_collect_only_default(self):
        with patch.object(daily, "call_publisher") as publish:
            self.assertEqual(self.fixture.run_daily(), 0)
        publish.assert_not_called()

    def test_settings_schema_is_strict_including_disabled_configuration(self):
        for text in ('version=true\nenabled=false\nmodel="opus"\npush=true',
                     'version=1\nenabled=false\nmodel="opus"\npush="true"',
                     'version=1\nenabled=false\nmodel="opus"\npush=true\nextra=1',
                     'version=1\nenabled=false\nmodel="unknown"\npush=true'):
            self.settings.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_publishing(self.settings)

    def test_call_publisher_discards_external_output_and_returns_exit_code(self):
        def publisher(argv):
            print("PRIVATE_PUBLISH_SECRET")
            raise SystemExit(3)
        output = io.StringIO()
        with patch("publisher.__main__.main", side_effect=publisher), contextlib.redirect_stdout(output):
            self.assertEqual(daily.call_publisher(["--model", "opus"]), 3)
        self.assertNotIn("PRIVATE_PUBLISH_SECRET", output.getvalue())


if __name__ == "__main__":
    unittest.main()
