"""임시 코드와 실제 Python 자식으로 갱신 순서·잠금 수명·종료 코드를 검증한다."""
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import daily
from collector.locking import AlreadyRunning, CollectorLock
from publisher import __main__ as publisher
from publisher.push import PushError
import runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"

    def logs(self):
        return [json.loads(line) for path in (self.data / "logs").glob("runtime-*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]

    def test_busy_manual_and_daily_do_not_sync_spawn_or_change_state(self):
        self.data.mkdir()
        state = self.data / "daily_status.json"
        state.write_bytes(b"previous-state")
        for mode in ("daily", "publisher"):
            with self.subTest(mode=mode), CollectorLock(self.data), patch.object(runtime, "prepare") as prepare, \
                    patch.object(runtime.subprocess, "run") as run:
                with self.assertRaises(AlreadyRunning):
                    runtime.launch(mode, [], self.data)
                prepare.assert_not_called()
                run.assert_not_called()
                self.assertEqual(state.read_bytes(), b"previous-state")
        self.assertEqual(self.logs(), [])

    def test_prepare_failure_does_not_start_worker_and_logs_only_error_class(self):
        with patch.object(runtime, "prepare", side_effect=PushError("PRIVATE_SECRET")), \
                patch.object(runtime.subprocess, "run") as run, self.assertRaises(PushError):
            runtime.launch("publisher", [], self.data)
        run.assert_not_called()
        self.assertEqual(self.logs()[0]["phase"], "prepare")
        self.assertEqual(self.logs()[0]["exit_code"], 2)
        self.assertNotIn("PRIVATE_SECRET", json.dumps(self.logs()))
        with CollectorLock(self.data):
            pass

    def test_spawn_failure_preserves_failure_and_releases_lock(self):
        with patch.object(runtime, "prepare"), \
                patch.object(runtime.subprocess, "run", side_effect=OSError("PRIVATE_SECRET")), self.assertRaises(OSError):
            runtime.launch("daily", [], self.data)
        self.assertEqual((self.logs()[0]["phase"], self.logs()[0]["exit_code"]), ("worker", 1))
        self.assertNotIn("PRIVATE_SECRET", json.dumps(self.logs()))
        with CollectorLock(self.data):
            pass

    def test_child_exit_codes_are_preserved(self):
        for code in (0, 1, 2, 3):
            with self.subTest(code=code), patch.object(runtime, "prepare"), \
                    patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], code)):
                self.assertEqual(runtime.launch("daily", [], self.data), code)
        self.assertEqual([entry["exit_code"] for entry in self.logs()], [0, 1, 2, 3])

    def package(self, name):
        path = self.root / "pipeline" / name
        path.mkdir(parents=True)
        (path / "__init__.py").write_text("", encoding="utf-8")
        return path

    def test_new_worker_uses_updated_code_and_parent_keeps_lock_until_exit(self):
        collector = self.package("collector")
        shutil.copyfile(Path(daily.__file__).with_name("locking.py"), collector / "locking.py")
        worker = collector / "daily.py"
        worker.write_text("raise RuntimeError('이전 코드가 실행됨')\n", encoding="utf-8")
        updated = '''from pathlib import Path
from collector.locking import CollectorLock, AlreadyRunning
def main(argv, *, lock_held, synchronized):
    assert lock_held and synchronized
    data = Path(argv[argv.index('--data-dir') + 1])
    try:
        with CollectorLock(data):
            return 99
    except AlreadyRunning:
        (data / 'new-worker.txt').write_text('새 정책', encoding='utf-8')
    return 3
'''
        with patch.object(runtime, "ROOT", self.root / "pipeline"), \
                patch.object(runtime, "prepare", side_effect=lambda repo: worker.write_text(updated, encoding="utf-8")):
            self.assertEqual(runtime.launch("daily", [], self.data), 3)
        self.assertEqual((self.data / "new-worker.txt").read_text(encoding="utf-8"), "새 정책")
        with CollectorLock(self.data):
            pass

    def test_new_export_blocking_policy_applies_on_the_first_execution(self):
        collector = self.package("collector")
        shutil.copyfile(Path(daily.__file__).with_name("locking.py"), collector / "locking.py")
        target = self.package("publisher")
        shutil.copyfile(publisher.__file__, target / "__main__.py")
        shutil.copyfile(Path(publisher.__file__).with_name("push.py"), target / "push.py")
        extractor = self.package("extractor")
        (extractor / "common.py").write_text("def now_text(): return '2026-11-17T08:00:00+09:00'\n", encoding="utf-8")
        (extractor / "__main__.py").write_text("def main(argv): return 0\n", encoding="utf-8")
        verifier = self.package("verifier")
        (verifier / "__main__.py").write_text("def main(argv): return 0\n", encoding="utf-8")
        exporter = self.package("exporter") / "__main__.py"
        exporter.write_text("def main(argv): return 0\n", encoding="utf-8")
        self.data.mkdir()
        (self.data / "notices.json").write_bytes(b'{"notices": []}')
        (self.data / "analysis.json").write_text(json.dumps({
            "input_sha256": hashlib.sha256((self.data / "notices.json").read_bytes()).hexdigest()}), encoding="utf-8")
        marker = self.root / "policy-applied.txt"
        policy = f"from pathlib import Path\ndef main(argv):\n    Path({str(marker)!r}).write_text('blocked')\n    return 2\n"
        with patch.object(runtime, "ROOT", self.root / "pipeline"), \
                patch.object(runtime, "prepare", side_effect=lambda repo: exporter.write_text(policy, encoding="utf-8")):
            code = runtime.launch("publisher", ["--model", "sonnet", "--out", str(self.root / "cards.json")], self.data)
        self.assertEqual(code, 2)
        self.assertEqual(marker.read_text(), "blocked")
        self.assertFalse((self.root / "cards.json").exists())

    def test_cli_maps_lock_conflicts_without_starting_stages(self):
        with CollectorLock(self.data), patch.object(runtime, "prepare") as prepare, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(publisher.cli_main(["--model", "sonnet", "--push", "--data-dir", str(self.data)]), 2)
            self.assertEqual(daily.cli_main(["--publish-config", "unused.toml", "--data-dir", str(self.data)]), 0)
        prepare.assert_not_called()

    def test_cli_preserves_relative_path_meaning_when_worker_changes_directory(self):
        with patch.object(runtime, "launch", return_value=0) as launch:
            self.assertEqual(daily.cli_main(["--publish-config", "config/settings.toml", "--config", "local.toml"]), 0)
        args = daily.parse_args(launch.call_args.args[1])[1]
        self.assertEqual(args.config, Path("local.toml").resolve())
        self.assertEqual(args.publish_config, Path("config/settings.toml").resolve())
        with patch.object(runtime, "launch", return_value=0) as launch:
            self.assertEqual(publisher.cli_main(["--model", "sonnet", "--push", "--claude", "bin/claude.exe"]), 0)
        args = publisher.parse_args(launch.call_args.args[1])
        self.assertEqual(Path(args.claude), Path("bin/claude.exe").resolve())

    def test_invalid_publisher_public_data_path_does_not_create_lock(self):
        site = self.root / "site"
        with patch.object(publisher, "SITE", site), contextlib.redirect_stderr(io.StringIO()), \
                patch.object(runtime, "launch") as launch:
            self.assertEqual(publisher.cli_main(["--model", "sonnet", "--push", "--data-dir", str(site / "private")]), 2)
        launch.assert_not_called()
        self.assertFalse(site.exists())
