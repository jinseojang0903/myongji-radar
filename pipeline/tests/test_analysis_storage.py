"""분석 이력의 버전 연결·중복 방지·기존 DB 이전을 네트워크 없이 확인한다."""
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from collector.analysis import analyze_file
from collector.models import Notice
from collector.repository import Repository
from collector.rules import field

RULES = Path(__file__).resolve().parents[1] / "config/rules.toml"


class AnalysisStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.notice = Notice("mju_ctl", "ctl/792/1", "https://ctl.mju.ac.kr/bbs/ctl/792/1/artclView.do",
                             "공모전 모집", "2026-11-01", "접수기간: 2026.11.01 ~ 2026.11.09")
        self.repo = Repository(self.data)
        self.addCleanup(self.repo.close)
        self.repo.save(self.notice, b"original")
        self.repo.export(self.data / "notices.json")
        self.original = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]

    def record(self, **changes):
        return {"notice": self.original, "generator_kind": "ai", "generator_version": "v0.2",
                "model": "sonnet", "model_id": "claude-sonnet-test", "prompt_version": "v0.2",
                "prompt_fingerprint": "prompt1", "result": {"value": "첫 결과"}, **changes}

    def test_rule_status_mapping_and_rule_runs_are_idempotent(self):
        self.assertEqual(field("value")["contract_status"], "ai")
        self.assertEqual(field("value", status="needs_review")["contract_status"], "needs_check")
        self.assertEqual(field()["contract_status"], "missing")
        for _ in range(2):
            analyze_file(self.data / "notices.json", RULES, self.data)
        rows = self.repo.db.execute("SELECT notice_version_id, generator_kind, result_json FROM analyses").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], (self.original["version_id"], "rule"))
        self.assertEqual(json.loads(rows[0][2])["fields"]["application_period"]["contract_status"], "ai")

    def test_multiple_models_prompts_and_results_remain_separate(self):
        first = self.repo.save_analyses([self.record()])[0]
        self.assertEqual(self.repo.save_analyses([self.record()]), [first])
        self.repo.save_analyses([self.record(model="opus"), self.record(prompt_fingerprint="prompt2"),
                                 self.record(result={"value": "다른 결과"})])
        self.assertEqual(self.repo.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 4)
        self.repo.save(replace(self.notice, body_text="마감 변경: 2026.11.12"), b"new")
        self.repo.export(self.data / "notices.json")
        newer = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]
        self.repo.save_analyses([self.record(notice=newer)])
        self.assertEqual(self.repo.db.execute("SELECT COUNT(DISTINCT notice_version_id) FROM analyses").fetchone()[0], 2)

    def test_wrong_identity_body_or_unknown_version_rolls_back_whole_batch(self):
        for changes in ({"source_id": "wrong"}, {"external_id": "wrong"}, {"body_text": "wrong"}, {"version_id": 999}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.repo.save_analyses([self.record(), self.record(notice={**self.original, **changes})])
            self.assertEqual(self.repo.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 0)

    def test_parser_only_refresh_preserves_earlier_analysis_input(self):
        self.repo.save_analyses([self.record()])
        updated = replace(self.notice, body_text="파서가 재해석한 본문", parser_version="k2web-v2")
        self.repo.save(updated, b"original", lambda raw, payload: updated)
        self.repo.export(self.data / "notices.json")
        reparsed = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]
        self.assertEqual(reparsed["version_id"], self.original["version_id"])
        self.repo.save_analyses([self.record(notice=reparsed)])
        self.assertEqual(self.repo.db.execute("SELECT COUNT(DISTINCT input_hash) FROM analyses").fetchone()[0], 2)

    def test_schema_v2_upgrade_preserves_originals_and_is_repeatable(self):
        old_versions = self.repo.db.execute("SELECT * FROM notice_versions").fetchall()
        with self.repo.db:
            self.repo.db.execute("DROP TABLE analyses")
            self.repo.db.execute("PRAGMA user_version=2")
        for _ in range(2):
            migrated = Repository(self.data)
            try:
                self.assertEqual(migrated.db.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(migrated.db.execute("SELECT * FROM notice_versions").fetchall(), old_versions)
                self.assertEqual(migrated.db.execute("PRAGMA foreign_key_list(analyses)").fetchone()[2], "notice_versions")
            finally:
                migrated.close()

    def test_last_seen_changes_without_creating_an_original_version(self):
        with patch("collector.repository.now", return_value="2026-11-05T12:00:00+00:00"):
            self.assertEqual(self.repo.save(self.notice, b"markup changed"), "unchanged")
        self.repo.export(self.data / "notices.json")
        current = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]
        self.assertEqual(current["last_seen_at"], "2026-11-05T12:00:00+00:00")
        self.assertEqual(current["version_id"], self.original["version_id"])
        self.assertEqual(current["collected_at"], self.original["collected_at"])

    def test_download_metadata_refresh_reuses_analysis_but_file_changes_do_not(self):
        image = {"url": "https://ctl.mju.ac.kr/poster.png", "alt": "포스터", "sha256": "a" * 64,
                 "local_path": "assets/poster.png", "download_status": "downloaded", "text_status": "not_supported"}

        def save_image(payload):
            status = self.repo.save(replace(self.notice, images=[payload]), b"poster markup")
            self.repo.export(self.data / "notices.json")
            return status, json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]

        _, original = save_image(image)
        analysis_id = self.repo.save_analyses([self.record(notice=original)])[0]
        status, metadata_only = save_image({**image, "local_path": "assets/recovered.png", "text_status": "needs_review"})
        self.assertEqual(status, "unchanged")
        self.assertEqual(metadata_only["version_id"], original["version_id"])
        self.assertEqual(self.repo.save_analyses([self.record(notice=metadata_only)]), [analysis_id])
        status, new_file = save_image({**image, "sha256": "b" * 64})
        self.assertEqual(status, "changed")
        self.assertNotEqual(new_file["version_id"], original["version_id"])
        self.assertNotEqual(self.repo.save_analyses([self.record(notice=new_file)])[0], analysis_id)


if __name__ == "__main__":
    unittest.main()
