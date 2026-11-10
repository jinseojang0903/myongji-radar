"""미리보기는 운영 상태를 바꾸지 않고 병합 실패 시 개별 입력을 보존한다."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from collector.repository import content_hash
from exporter.__main__ import read_bundles
from extractor.daily import card_id_of
from merger.__main__ import main
from merger.core import expand_blocked, preview
from merger.groups import Group, load_groups


def sample(number, *, posted="2026-10-01"):
    original = {"source_id": "mju_ctl", "external_id": f"ctl/792/{number}", "version_id": number,
                "title": "대회 모집", "published_at": posted, "body_text": "교내 대회 참가자 모집",
                "url": f"https://ctl.mju.ac.kr/bbs/ctl/792/{number}/artclView.do", "images": [], "attachments": []}
    notice = {key: original[key] for key in ("source_id", "external_id", "version_id", "title", "published_at", "url")}
    notice["input_hash"] = content_hash(original)
    def field(value):
        return {"value": value, "flag": "found", "evidence": [{"notice": 1, "quote": "교내 대회 참가자 모집", "location": "본문", "file": None}],
                "alternatives": [], "note": None}
    bundle = {"card_id": card_id_of("mju_ctl", original["external_id"]), "notices": [notice],
              "extraction": {"is_contest": {"value": True}, "warnings": [],
                             "fields": {name: field(value) for name, value in (("title", "대회"), ("organizer", "센터"), ("scope", "교내"),
                                                                            ("kind", "모집"), ("apply", "9/30까지"))}},
              "statuses": {"title": {"status": "human_verified"}}, "deadline_history": [], "meta": {"private": "keep"}}
    return original, bundle


class ManualMergerTests(unittest.TestCase):
    def setUp(self):
        self.first, self.second = sample(1), sample(2, posted="2026-10-02")
        self.group = Group("fixed", (("mju_ctl", "ctl/792/1"), ("mju_ctl", "ctl/792/2")))

    def run_preview(self, pairs=None, *, groups=None, blocked=frozenset()):
        pairs = pairs if pairs is not None else [self.first, self.second]
        return preview({"bundles": [pair[1] for pair in pairs]}, [pair[0] for pair in pairs],
                       [self.group] if groups is None else groups, blocked)

    def test_equal_fields_merge_remap_evidence_without_review_or_history(self):
        original = copy.deepcopy([self.first, self.second])
        result = self.run_preview()
        self.assertNotIn("bundles", result)
        merged = result["candidates"][0]
        self.assertEqual(merged["card_id"], "fixed")
        self.assertEqual(merged["statuses"], {})
        self.assertEqual(merged["deadline_history"], [])
        self.assertEqual([item["notice"] for item in merged["extraction"]["fields"]["apply"]["evidence"]], [1, 2])
        self.assertEqual([self.first, self.second], original)

    def test_unregistered_input_is_identical_including_ordinary_deadline_and_metadata(self):
        self.first[1]["extraction"]["fields"]["apply"]["note"] = "연도 없는 9/30까지"
        self.assertEqual(self.run_preview([self.first], groups=[])["candidates"], [self.first[1]])

    def test_missing_member_preserves_every_available_individual(self):
        result = self.run_preview([self.first])
        self.assertEqual(result["candidates"], [self.first[1]])
        self.assertEqual(result["report"]["groups"][0]["reason"], "missing_member")

    def test_conflicting_or_uncertain_fields_fall_back_without_mutation(self):
        for name in ("title", "organizer", "scope", "apply"):
            with self.subTest(name=name):
                second = copy.deepcopy(self.second)
                second[1]["extraction"]["fields"][name]["value"] = "다른 값"
                result = self.run_preview([self.first, second])
                self.assertEqual(result["candidates"], [self.first[1], second[1]])
        self.second[1]["extraction"]["fields"]["apply"]["flag"] = "needs_check"
        self.assertEqual(self.run_preview()["candidates"], [self.first[1], self.second[1]])

    def test_stale_provenance_falls_back_until_fresh_input_arrives(self):
        self.second[1]["notices"][0]["input_hash"] = "0" * 64
        result = self.run_preview()
        self.assertEqual(result["report"]["groups"][0]["reason"], "stale_or_missing_provenance")
        self.assertEqual(result["candidates"], [self.first[1], self.second[1]])
        self.second[1]["notices"][0]["input_hash"] = content_hash(self.second[0])
        self.assertEqual(self.run_preview()["report"]["groups"][0]["action"], "merged")

    def test_terminal_and_existing_history_are_kept_as_individual_inputs(self):
        self.second[1]["extraction"]["fields"]["kind"]["value"] = "결과"
        self.assertEqual(self.run_preview()["candidates"], [self.first[1], self.second[1]])
        self.second[1]["extraction"]["fields"]["kind"]["value"] = "모집"
        self.second[1]["deadline_history"] = [{"old": "keep"}]
        self.assertEqual(self.run_preview()["candidates"], [self.first[1], self.second[1]])
        self.assertEqual(self.run_preview()["report"]["groups"][0]["reason"], "history_requires_review")

    def test_malformed_contest_or_fields_fall_back_and_boolean_version_is_not_an_integer(self):
        for value in (None, [], "yes"):
            with self.subTest(value=value):
                second = copy.deepcopy(self.second)
                second[1]["extraction"]["is_contest"] = value
                self.assertEqual(self.run_preview([self.first, second])["candidates"], [self.first[1], second[1]])
                second[1]["extraction"]["is_contest"] = {"value": True}
                second[1]["extraction"]["fields"] = value
                self.assertEqual(self.run_preview([self.first, second])["candidates"], [self.first[1], second[1]])
        self.first[0]["version_id"] = True
        self.assertEqual(self.run_preview()["report"]["groups"][0]["reason"], "stale_or_missing_provenance")

    def test_block_expansion_is_bidirectional_and_does_not_warn_about_generated_ids(self):
        member_ids = {self.first[1]["card_id"], self.second[1]["card_id"]}
        for requested in ("fixed", self.first[1]["card_id"]):
            with self.subTest(requested=requested):
                self.assertEqual(expand_blocked({requested}, [self.group]), member_ids | {"fixed"})
                result = self.run_preview(blocked={requested})
                self.assertEqual(result["candidates"], [])
                report = result["report"]["blocked"]
                self.assertEqual(report["requested_ids"], [requested])
                self.assertEqual(report["unmatched_requested_ids"], [])
                self.assertEqual(result["report"]["groups"][0]["action"], "blocked")
        result = self.run_preview([self.first], blocked={"fixed"})
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["report"]["blocked"]["unmatched_requested_ids"], [])

    def test_duplicate_groups_input_or_canonical_collision_are_rejected(self):
        with self.assertRaises(ValueError):
            self.run_preview(groups=[self.group, self.group])
        with self.assertRaises(ValueError):
            self.run_preview([self.first, self.first])
        with self.assertRaises(ValueError):
            self.run_preview(groups=[Group(self.first[1]["card_id"], self.group.members)])

    def test_curated_groups_have_five_ids_and_no_campus_specific_evc(self):
        groups = load_groups(Path(__file__).resolve().parents[1] / "config/contest_groups.toml")
        self.assertEqual(len(groups), 5)
        self.assertFalse(any(group.id.startswith("evc-") for group in groups))

    def test_cli_preview_cannot_be_exported_and_never_writes_input_or_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, output = root / "data", root / "preview" / "contest-preview.json"
            data.mkdir()
            notices, bundles, groups, blocked = (data / name for name in ("notices.json", "bundles.json", "groups.toml", "blocked.toml"))
            notices.write_text(json.dumps({"notices": [self.first[0]]}), encoding="utf-8")
            bundles.write_text(json.dumps({"bundles": [self.first[1]]}), encoding="utf-8")
            groups.write_text('version=1\ngroups=[]\n', encoding="utf-8")
            blocked.write_text('blocked=[]\n', encoding="utf-8")
            before = {path: path.read_bytes() for path in (notices, bundles, groups, blocked)}
            argv = ["--bundles", str(bundles), "--notices", str(notices), "--groups", str(groups), "--blocked", str(blocked)]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*argv, "--out", str(output)]), 0)
                with self.assertRaises(ValueError):
                    read_bundles(output)
                for target in (data / "contest-preview.json", data / "nested" / "contest-preview.json", data / "collector.sqlite3", notices):
                    self.assertEqual(main([*argv, "--out", str(target)]), 2)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertFalse((data / "collector.sqlite3").exists())
            self.assertFalse((data / "card_bundles.merged.json").exists())
