"""마감 비교 보고서 CLI의 비공개 경로·잠금·실패 보존을 합성 입력으로 검증한다."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from collector.locking import CollectorLock
from merger import deadline_review as cli
from test_deadline_review import AFTER, BEFORE, sample
from verifier.__main__ import verify_bundles

REPO = Path(__file__).resolve().parents[2]


class DeadlineReviewCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.before = self.root / "old" / "verified.json"
        self.after = self.root / "new" / "verified.json"
        for path, marker in ((self.before, "before"), (self.after, "after")):
            path.parent.mkdir()
            path.write_text(json.dumps({"marker": marker}), encoding="utf-8")
        self.groups = self.root / "config" / "groups.toml"
        self.groups.parent.mkdir()
        self.groups.write_text("version=1\ngroups=[]\n", encoding="utf-8")
        self.output = self.root / "review" / "deadline-review.json"
        self.empty = {"review_version": 1, "proposals": [], "skipped": []}

    def run_cli(self, target=None, before=None, after=None):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["--before", str(before or self.before), "--after", str(after or self.after),
                             "--groups", str(self.groups), "--out", str(target or self.output)])
        return code, stderr.getvalue()

    def test_report_is_ignored_at_any_depth(self):
        for path in ("deadline-review.json", "pipeline/preview-data/deadline-review.json", "nested/deadline-review.json"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO,
                                        capture_output=True, check=False)
                self.assertEqual(result.returncode, 0)

    def test_real_comparison_of_empty_snapshots_writes_no_proposals(self):
        self.before.write_text(json.dumps({"generated_at": BEFORE, "bundles": []}), encoding="utf-8")
        self.after.write_text(json.dumps({"generated_at": AFTER, "bundles": []}), encoding="utf-8")
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")),
                         {**self.empty, "before_generated_at": BEFORE, "after_generated_at": AFTER, "unchanged_ids": []})

    def test_real_verified_snapshots_unchanged_is_zero_and_change_requires_review(self):
        old_raw, old = sample()
        before, checks = verify_bundles({"generated_at": BEFORE, "bundles": [old]}, {old_raw["url"]: old_raw})
        self.assertEqual(checks, [])
        for changed, expected_code in ((False, 0), (True, 1)):
            with self.subTest(changed=changed):
                new_raw, new = sample("2026-10-23", revision=2) if changed else (old_raw, old)
                after, checks = verify_bundles({"generated_at": AFTER, "bundles": [new]}, {new_raw["url"]: new_raw})
                self.assertEqual(checks, [])
                self.before.write_text(json.dumps(before), encoding="utf-8")
                self.after.write_text(json.dumps(after), encoding="utf-8")
                before_bytes, after_bytes = self.before.read_bytes(), self.after.read_bytes()
                self.assertEqual(self.run_cli()[0], expected_code)
                result = json.loads(self.output.read_text(encoding="utf-8"))
                self.assertEqual(result["before_generated_at"], BEFORE)
                self.assertEqual(result["after_generated_at"], AFTER)
                self.assertEqual(result["skipped"], [])
                if changed:
                    self.assertEqual(result["unchanged_ids"], [])
                    self.assertEqual(result["proposals"][0]["review_status"], "needs_review")
                    self.assertEqual((result["proposals"][0]["change"]["old"], result["proposals"][0]["change"]["new"]),
                                     ("2026-09-30", "2026-10-23"))
                else:
                    self.assertEqual(result["proposals"], [])
                    self.assertEqual(result["unchanged_ids"], [old["card_id"]])
                self.assertEqual((self.before.read_bytes(), self.after.read_bytes()), (before_bytes, after_bytes))

    def test_no_change_and_review_needed_codes_preserve_inputs(self):
        before_bytes, after_bytes = self.before.read_bytes(), self.after.read_bytes()
        for result, expected in ((self.empty, 0), ({**self.empty, "proposals": [{"review_status": "needs_review"}]}, 1),
                                 ({**self.empty, "skipped": [{"reason": "requires_review"}]}, 1)):
            with self.subTest(result=result), patch.object(cli, "compare_snapshots", return_value=result) as compare:
                self.assertEqual(self.run_cli()[0], expected)
                self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), result)
                self.assertEqual(compare.call_args.args[:2], ({"marker": "before"}, {"marker": "after"}))
                self.assertIn("checked_at", compare.call_args.kwargs)
                self.assertEqual(self.before.read_bytes(), before_bytes)
                self.assertEqual(self.after.read_bytes(), after_bytes)

    def test_input_subtree_same_snapshot_and_wrong_name_are_rejected(self):
        with patch.object(cli, "compare_snapshots") as compare:
            for path in (self.before.parent / "deadline-review.json", self.after.parent / "nested" / "deadline-review.json",
                         self.root / "review" / "collector.sqlite3"):
                with self.subTest(path=path):
                    self.assertEqual(self.run_cli(path)[0], 2)
                    self.assertFalse(path.exists())
            self.assertEqual(self.run_cli(after=self.before)[0], 2)
            compare.assert_not_called()

    def test_repository_and_site_output_policy(self):
        repo = self.root / "repo"
        private = repo / "pipeline" / "preview-data"
        with patch.object(cli, "REPO", repo), patch.object(cli, "SITE", repo / "site"), \
                patch.object(cli, "PRIVATE_PREVIEW", private), patch.object(cli, "compare_snapshots", return_value=self.empty):
            for path in (repo / "deadline-review.json", repo / "site" / "deadline-review.json"):
                with self.subTest(path=path):
                    self.assertEqual(self.run_cli(path)[0], 2)
                    self.assertFalse(path.exists())
            self.assertEqual(self.run_cli(private / "deadline-review.json")[0], 0)

    def test_errors_and_bad_result_never_replace_existing_report_or_leak_message(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"previous report")
        for error in (RuntimeError("private quote"), AttributeError("private quote"), RecursionError("private quote")):
            with self.subTest(error=type(error).__name__), patch.object(cli, "compare_snapshots", side_effect=error):
                code, stderr = self.run_cli()
                self.assertEqual(code, 2)
                self.assertIn(type(error).__name__, stderr)
                self.assertNotIn("private quote", stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(self.output.read_bytes(), b"previous report")
        with patch.object(cli, "compare_snapshots", return_value={"proposals": [], "skipped": None}):
            self.assertEqual(self.run_cli()[0], 2)
            self.assertEqual(self.output.read_bytes(), b"previous report")

    def test_busy_snapshot_lock_stops_and_releases_other_acquired_lock(self):
        with CollectorLock(self.before.parent), patch.object(cli, "compare_snapshots", return_value=self.empty) as compare:
            self.assertEqual(self.run_cli()[0], 2)
            compare.assert_not_called()
            self.assertFalse(self.output.exists())
        with patch.object(cli, "compare_snapshots", return_value=self.empty):
            self.assertEqual(self.run_cli()[0], 0)

    def test_duplicate_snapshot_directory_is_locked_once_and_writer_uses_resolved_path(self):
        shared_after = self.before.parent / "after.json"
        shared_after.write_text("{}", encoding="utf-8")
        directories = []

        @contextlib.contextmanager
        def lock(directory):
            directories.append(directory)
            yield

        target = self.root / "unused" / ".." / "review" / "deadline-review.json"
        with patch.object(cli, "CollectorLock", side_effect=lock), patch.object(cli, "compare_snapshots", return_value=self.empty), \
                patch.object(cli, "write_json") as writer:
            self.assertEqual(self.run_cli(target, after=shared_after)[0], 0)
            self.assertEqual(directories, [self.before.parent.resolve()])
            self.assertEqual(writer.call_args.args[0], target.resolve())

    def test_bad_snapshot_or_group_config_is_failure_without_output(self):
        for broken_file in (self.before, self.groups):
            original = broken_file.read_bytes()
            try:
                broken_file.write_text("invalid [", encoding="utf-8")
                with patch.object(cli, "compare_snapshots") as compare:
                    self.assertEqual(self.run_cli()[0], 2)
                    compare.assert_not_called()
                    self.assertFalse(self.output.exists())
            finally:
                broken_file.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
