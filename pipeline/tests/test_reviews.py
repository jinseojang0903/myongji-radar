"""사람 검수 기록 (exporter/reviews.py, #99) 시험."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from exporter.__main__ import main as cli_main
from exporter.reviews import (REVIEWABLE_FIELDS, ReviewsError, apply_all, canonical_json, evidence_fingerprint,
                              load_reviews)
from verifier.stamp import stamp_bundle
from test_exporter import GENERATED_AT, bundle

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "exported_cards.json"
# scripts/review_card.mjs 시험(tests/review_card.test.js)도 같은 값을 확인한다: 두 언어의 지문이 같아야 검수가 맞는다
FIXTURE_APPLY_FINGERPRINT = "f17b4e21f56bfce13eef7d5b6a849e1a44fcc53aefa1205adf78d059aa585b11"


def entry(card_id, field, value, fingerprint, since="2026-11-16"):
    return (f'[[review]]\ncard_id = {json.dumps(card_id)}\nfield = {json.dumps(field)}\n'
            f'value = {json.dumps(canonical_json(value), ensure_ascii=False)}\nevidence_sha256 = "{fingerprint}"\nsince = {since}\n')


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.card = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["cards"][0])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, text):
        path = self.folder / "reviews.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def review_of(self, name, card=None):
        card = card or self.card
        field = card["fields"][name]
        return entry(card["id"], name, field["value"], evidence_fingerprint(field["evidence"]))

    def test_fingerprint_matches_the_node_helper(self):
        self.assertEqual(evidence_fingerprint(self.card["fields"]["apply"]["evidence"]), FIXTURE_APPLY_FINGERPRINT)
        self.assertEqual(canonical_json({"b": 1, "a": ["가", None]}), '{"a":["가",null],"b":1}')

    def test_matching_review_promotes_only_that_field(self):
        self.card["fields"]["apply"]["status"] = "ai"
        reviews = load_reviews(self.write(self.review_of("apply")))
        data, report = {"cards": [self.card]}, {"warnings": []}
        self.assertEqual(apply_all(data, reviews, report), 1)
        self.assertEqual(self.card["fields"]["apply"]["status"], "human_verified")
        self.assertTrue(self.card["fields"]["apply"]["note"].startswith("팀이 원문과 대조함 (2026-11-16)"))
        self.assertEqual(report["warnings"], [])

    def test_changed_value_or_evidence_is_not_trusted(self):
        for change in ("value", "evidence"):
            card = copy.deepcopy(self.card)
            card["fields"]["apply"]["status"] = "ai"
            reviews = load_reviews(self.write(self.review_of("apply", card)))
            if change == "value":
                card["fields"]["apply"]["value"]["end"] = "2026-10-22"
            else:
                card["fields"]["apply"]["evidence"][0]["quote"] += " (바뀜)"
            report = {"warnings": []}
            self.assertEqual(apply_all({"cards": [card]}, reviews, report), 0, change)
            self.assertEqual(card["fields"]["apply"]["status"], "ai", change)
            self.assertIn("검수 기록이 지금 값·근거와 달라", report["warnings"][0]["reason"])

    def test_fields_without_evidence_or_value_are_not_promoted(self):
        self.card["fields"]["apply"]["status"] = "needs_check"
        reviews = load_reviews(self.write(self.review_of("apply")))
        self.card["fields"]["apply"]["evidence"] = []  # 근거 대조에 실패해 근거를 비운 항목
        report = {"warnings": []}
        apply_all({"cards": [self.card]}, reviews, report)
        self.assertEqual(self.card["fields"]["apply"]["status"], "needs_check")

    def test_review_for_missing_card_is_reported(self):
        reviews = load_reviews(self.write(entry("gone-card", "apply", {"end": "2026-10-01"}, "0" * 64)))
        report = {"warnings": []}
        apply_all({"cards": [self.card]}, reviews, report)
        self.assertEqual(report["warnings"][0]["id"], "gone-card")

    def test_format_errors(self):
        good = self.review_of("apply")
        cases = {
            "contact": good.replace('field = "apply"', 'field = "contact"'),
            "no key": good.replace('since = 2026-11-16\n', ''),
            "bad sha": good.replace(evidence_fingerprint(self.card["fields"]["apply"]["evidence"]), "ABC"),
            "string date": good.replace("since = 2026-11-16", 'since = "2026-11-16"'),
            "null value": entry(self.card["id"], "apply", None, "0" * 64),
            "duplicate": good + good,
            "other top": "blocked = []\n",
            "bad id": good.replace(self.card["id"], "Bad Id"),
        }
        for name, text in cases.items():
            with self.assertRaises(ReviewsError, msg=name):
                load_reviews(self.write(text))
        self.assertEqual(load_reviews(self.write("review = []\n")), {})
        self.assertNotIn("contact", REVIEWABLE_FIELDS)

    def test_repository_file_is_valid(self):
        load_reviews(Path(__file__).resolve().parents[1] / "config" / "human_reviews.toml")


class ReviewCliTests(unittest.TestCase):
    def run_cli(self, folder, reviews_path):
        bundles = folder / "bundles.json"
        bundles.write_text(json.dumps({"generated_at": GENERATED_AT,
                                       "bundles": [stamp_bundle(asdict(bundle()), GENERATED_AT)]}, ensure_ascii=False),
                           encoding="utf-8")
        blocked = folder / "blocked.toml"
        blocked.write_text("blocked = []\n", encoding="utf-8")
        out = folder / "cards.json"
        argv = ["exporter", "--bundles", str(bundles), "--out", str(out), "--blocked", str(blocked), "--reviews", str(reviews_path),
                "--report", str(folder / "report.json")]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli_main()
        return code, json.loads(out.read_text(encoding="utf-8")), json.loads((folder / "report.json").read_text(encoding="utf-8"))

    def test_review_written_from_the_public_card_promotes_it_on_the_next_export(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            reviews = folder / "reviews.toml"
            reviews.write_text("review = []\n", encoding="utf-8")
            _, data, _ = self.run_cli(folder, reviews)
            card = data["cards"][0]
            target = next(field for field in REVIEWABLE_FIELDS
                          if card["fields"][field]["status"] in ("ai", "needs_check") and card["fields"][field]["evidence"])
            field = card["fields"][target]
            reviews.write_text(entry(card["id"], target, field["value"], evidence_fingerprint(field["evidence"])), encoding="utf-8")
            _, data, _ = self.run_cli(folder, reviews)
            self.assertEqual(data["cards"][0]["fields"][target]["status"], "human_verified")

    def test_broken_review_file_publishes_without_reviews(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            reviews = folder / "reviews.toml"
            reviews.write_text("이건 TOML이 아님", encoding="utf-8")
            code, data, report = self.run_cli(folder, reviews)
            self.assertEqual(code, 1, "검수 없이 공개하고 보고에 남긴다 (멈추지 않음)")
            self.assertTrue(data["cards"])
            self.assertTrue(any("검수 기록을 읽지 못해" in item["reason"] for item in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
