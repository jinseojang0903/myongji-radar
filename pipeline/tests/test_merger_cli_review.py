"""미리보기의 비공개 출력 정책과 CLI 실패 코드를 학교 요청 없이 검사한다."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from merger import __main__ as cli


REPO = Path(__file__).resolve().parents[2]


class MergerCliReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.notices = self.data / "notices.json"
        self.bundles = self.data / "bundles.json"
        self.groups = self.data / "groups.toml"
        self.blocked = self.data / "blocked.toml"
        notice = {"source_id": "mju_ctl", "external_id": "ctl/792/1"}
        bundle = {"card_id": "mju-ctl-ctl-792-1", "notices": [notice], "extraction": {"fields": {}}}
        self.notices.write_text(json.dumps({"notices": [notice]}), encoding="utf-8")
        self.bundles.write_text(json.dumps({"bundles": [bundle]}), encoding="utf-8")
        self.groups.write_text("version=1\ngroups=[]\n", encoding="utf-8")
        self.blocked.write_text("blocked=[]\n", encoding="utf-8")
        self.output = self.root / "preview" / "contest-preview.json"

    def run_cli(self, output=None):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["--bundles", str(self.bundles), "--notices", str(self.notices),
                             "--groups", str(self.groups), "--blocked", str(self.blocked),
                             "--out", str(output or self.output)])
        return code, stderr.getvalue()

    def test_preview_files_are_ignored_at_any_depth(self):
        for relative in ("contest-preview.json", "pipeline/preview/contest-preview.json",
                         "pipeline/preview-data/other-private-file.json", "nested/contest-preview.json"):
            with self.subTest(path=relative):
                result = subprocess.run(["git", "check-ignore", "-q", relative], cwd=REPO,
                                        capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, relative)

    def test_repository_output_only_allows_ignored_preview_folder(self):
        repo = self.root / "repo"
        private = repo / "pipeline" / "preview-data"
        with patch.object(cli, "REPO", repo), patch.object(cli, "PRIVATE_PREVIEW", private), \
                patch.object(cli, "SITE", repo / "site"):
            for target in (repo / "preview" / "contest-preview.json", repo / "contest-preview.json",
                           repo / "site" / "data" / "contest-preview.json"):
                with self.subTest(path=target):
                    self.assertEqual(self.run_cli(target)[0], 2)
                    self.assertFalse(target.exists())
            self.assertEqual(self.run_cli(private / "contest-preview.json")[0], 0)

    def test_input_subtree_still_cannot_be_output(self):
        self.assertEqual(self.run_cli(self.data / "nested" / "contest-preview.json")[0], 2)
        self.assertFalse((self.data / "nested").exists())

    def test_unexpected_errors_return_two_and_preserve_output_without_private_message(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep previous output")
        for exception in (AttributeError("private quote"), RecursionError("private quote"), RuntimeError("private quote")):
            with self.subTest(error=type(exception).__name__), patch.object(cli, "preview", side_effect=exception):
                code, stderr = self.run_cli()
                self.assertEqual(code, 2)
                self.assertIn(type(exception).__name__, stderr)
                self.assertNotIn("private quote", stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(self.output.read_bytes(), b"keep previous output")

    def test_invalid_groups_return_two_without_replacing_output(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep previous output")
        for text in ("version=[", "version=1\ngroups=[]\nunexpected=true\n"):
            with self.subTest(config=text):
                self.groups.write_text(text, encoding="utf-8")
                self.assertEqual(self.run_cli()[0], 2)
                self.assertEqual(self.output.read_bytes(), b"keep previous output")

    def test_malformed_preview_result_is_checked_before_output_is_written(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep previous output")
        malformed = {"candidates": None, "report": {"groups": [], "blocked": {"unmatched_requested_ids": []}}}
        with patch.object(cli, "preview", return_value=malformed):
            code, stderr = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("TypeError", stderr)
        self.assertEqual(self.output.read_bytes(), b"keep previous output")

    def test_fallback_and_unmatched_requested_block_return_one(self):
        self.groups.write_text('version=1\n[[groups]]\nid="fixed"\nmembers=['
                               '{source_id="mju_ctl",external_id="ctl/792/1"},'
                               '{source_id="mju_ctl",external_id="ctl/792/2"}]\n', encoding="utf-8")
        self.assertEqual(self.run_cli()[0], 1)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["report"]["groups"][0]["reason"], "missing_member")
        self.groups.write_text("version=1\ngroups=[]\n", encoding="utf-8")
        self.blocked.write_text('[[blocked]]\ncard_id="unknown"\nreason="fixture"\nsince=2026-11-12\n', encoding="utf-8")
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["report"]["blocked"]["unmatched_requested_ids"], ["unknown"])

    def test_writer_receives_the_resolved_checked_target(self):
        target = self.root / "unused" / ".." / "preview" / "contest-preview.json"
        with patch.object(cli, "write_json") as writer:
            self.assertEqual(self.run_cli(target)[0], 0)
        self.assertEqual(writer.call_args.args[0], target.resolve())


if __name__ == "__main__":
    unittest.main()
