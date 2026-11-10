"""비공개 원문 버전·해시 정보와 최소 입력 호환을 학교/AI 호출 없이 확인한다."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from collector.models import Notice
from collector.repository import Repository, content_hash
from exporter.cards import CardBundle, build_card
from extractor.daily import bundle_of

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "prompts/extract_v0.2/example_output.json").read_text(encoding="utf-8"))


def notice():
    return {"source_id": "mju_ctl", "external_id": "ctl/792/1", "version_id": 4, "version_number": 2,
            "url": "https://ctl.mju.ac.kr/bbs/ctl/792/1/artclView.do", "title": "시험 공모전",
            "published_at": "2026-11-01", "body_text": "시험 본문", "images": [], "attachments": []}


def entry():
    return {"output": copy.deepcopy(EXAMPLE), "model": "fixture", "model_id": "fixture-model",
            "prompt_version": "v0.2", "prompt_fingerprint": "fixture-fingerprint"}


class BundleProvenanceTests(unittest.TestCase):
    def test_complete_notice_keeps_original_hash_and_private_version(self):
        raw = notice()
        result = bundle_of("fixture", raw, entry())
        source = result["notices"][0]
        self.assertEqual(source["input_hash"], content_hash(raw))
        self.assertEqual((source["external_id"], source["version_id"], source["version_number"]), ("ctl/792/1", 4, 2))
        self.assertEqual(result["meta"]["merge_model_id"], "fixture-model")
        self.assertEqual(result["meta"]["merge_prompt_fingerprint"], "fixture-fingerprint")
        changed = {**raw, "body_text": "수정한 본문"}
        self.assertNotEqual(bundle_of("fixture", changed, entry())["notices"][0]["input_hash"], source["input_hash"])

    def test_legacy_notice_missing_asset_keys_has_same_empty_asset_hash(self):
        raw = notice()
        expected = content_hash(raw)
        del raw["images"], raw["attachments"]
        self.assertEqual(bundle_of("fixture", raw, entry())["notices"][0]["input_hash"], expected)
        self.assertNotIn("images", raw, "호출자의 입력을 바꾸지 않는다")
        self.assertNotIn("attachments", raw)
        self.assertEqual(len(bundle_of("fixture", {}, entry())["notices"][0]["input_hash"]), 64)

    def test_public_card_contains_no_internal_provenance(self):
        result = bundle_of("fixture", notice(), entry())
        card = build_card(CardBundle(**result))
        for name in ("external_id", "version_id", "version_number", "input_hash", "merge_model", "merge_model_id", "merge_prompt_fingerprint"):
            self.assertNotIn(name, json.dumps(card, ensure_ascii=False))

    def test_export_version_number_counts_saved_content_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Repository(Path(folder))
            try:
                original = Notice("mju_ctl", "ctl/792/1", "https://ctl.mju.ac.kr/post", "title", None, "body")
                repo.save(original, b"first")
                repo.save(original, b"same content")
                path = Path(folder) / "notices.json"
                repo.export(path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["notices"][0]["version_number"], 1)
                repo.save(replace(original, body_text="new body"), b"changed")
                repo.export(path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["notices"][0]["version_number"], 2)
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
