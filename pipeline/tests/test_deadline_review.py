"""학교·AI 호출 없이 후보 비교를 검증한다. 본문과 추출값은 모두 합성이다."""
import copy
import unittest

from collector.repository import content_hash
from extractor.daily import bundle_of, card_id_of
from merger.groups import Group
from merger.history import compare_snapshots
from verifier.__main__ import verify_bundles
from verifier.stamp import stamp_bundle

BEFORE = "2026-10-01T10:00:00+09:00"
AFTER = "2026-10-02T10:00:00+09:00"
CHECKED = "2026-10-02T16:00:00+00:00"  # 한국 날짜는 10/3이다.


def sample(end="2026-09-30", *, revision=1, number=1, kind="모집", posted="2026-09-01"):
    body = f"합성 교내 공모전 참가자 {kind}. 주관 교수학습센터. 신청 마감: {end}"
    original = {"source_id": "mju_ctl", "external_id": f"ctl/792/{number}", "version_id": revision,
                "version_number": revision, "url": f"https://ctl.mju.ac.kr/bbs/ctl/792/{number}/artclView.do",
                "title": "합성 공모전", "body_text": body, "published_at": posted, "images": [], "attachments": []}
    def field(value, quote=body):
        return {"value": value, "flag": "found", "evidence": [{"notice": 1, "quote": quote, "location": "본문", "file": None}],
                "alternatives": [], "note": None}
    output = {"is_contest": {"value": True}, "warnings": [], "fields": {
        "title": field("합성 공모전"), "organizer": field("교수학습센터"), "scope": field("교내"), "kind": field(kind),
        "apply": field({"start": None, "end": {"date": end, "time": None, "year_inferred": False}})}}
    bundle = bundle_of(card_id_of(original["source_id"], original["external_id"]), original,
                       {"output": output, "model": "fixture", "model_id": "fixture-exact", "prompt_version": "v0.2",
                        "prompt_fingerprint": "fixture-prompt"})
    return original, bundle


def snapshot(bundle, at):
    # 경계 조건별 시험은 검증 표시 API로 입력 형식만 만든다. 의미 검증 완료라는 뜻이 아니다.
    return {"generated_at": at, "bundles": [stamp_bundle(bundle, at)]}


class DeadlineReviewTests(unittest.TestCase):
    def pair(self):
        return sample()[1], sample("2026-10-23", revision=2)[1]

    def compare(self, old, new, groups=()):
        return compare_snapshots(snapshot(old, BEFORE), snapshot(new, AFTER), list(groups), checked_at=CHECKED)

    def test_same_notice_real_verifier_roundtrip_and_no_public_history_or_input_mutation(self):
        old_raw, old = sample()
        new_raw, new = sample("2026-10-23", revision=2)
        before, old_checks = verify_bundles({"generated_at": BEFORE, "bundles": [old]}, {old_raw["url"]: old_raw})
        after, new_checks = verify_bundles({"generated_at": AFTER, "bundles": [new]}, {new_raw["url"]: new_raw})
        self.assertEqual(old_checks + new_checks, [])
        saved = copy.deepcopy((before, after))
        result = compare_snapshots(before, after, [], checked_at=CHECKED)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["review_status"], "needs_review")
        self.assertEqual(proposal["change"], {"field": "apply_end", "old": "2026-09-30", "new": "2026-10-23",
                                             "old_source": old_raw["url"], "new_source": new_raw["url"], "checked_at": "2026-10-03"})
        self.assertNotIn("deadline_history", result)
        self.assertNotIn("bundles", result)
        self.assertNotIn("quote", str(result))
        self.assertEqual((before, after), saved)

    def test_actual_verifier_rejects_fabricated_evidence_from_proposals(self):
        old_raw, old = sample()
        new_raw, new = sample("2026-10-23", revision=2)
        new["extraction"]["fields"]["apply"]["evidence"][0]["quote"] = "원문에 없는 날짜 근거"
        before, _ = verify_bundles({"generated_at": BEFORE, "bundles": [old]}, {old_raw["url"]: old_raw})
        after, checks = verify_bundles({"generated_at": AFTER, "bundles": [new]}, {new_raw["url"]: new_raw})
        self.assertTrue(checks)
        self.assertEqual(compare_snapshots(before, after, [], checked_at=CHECKED)["proposals"], [])

    def test_reextract_model_change_version_gap_rollback_and_unchanged_are_skipped(self):
        cases = []
        old, new = self.pair()
        unchanged = self.compare(old, copy.deepcopy(old))
        self.assertEqual(unchanged["skipped"], [])
        self.assertEqual(unchanged["unchanged_ids"], [old["card_id"]])
        current = copy.deepcopy(new)
        current["notices"][0].update(input_hash=old["notices"][0]["input_hash"], version_id=1, version_number=1)
        cases.append((current, "same_source_reextracted"))
        current = copy.deepcopy(new)
        current["meta"]["merge_model_id"] = "other"
        cases.append((current, "generation_signature_changed"))
        current = copy.deepcopy(new)
        current["notices"][0].update(version_id=3, version_number=3)
        cases.append((current, "nonconsecutive_versions"))
        for current, reason in cases:
            with self.subTest(reason=reason):
                result = self.compare(old, current)
                self.assertEqual(result["proposals"], [])
                self.assertEqual(result["skipped"][0]["reason"], reason)
        old["notices"][0].update(version_id=4, version_number=4)
        self.assertEqual(self.compare(old, new)["skipped"][0]["reason"], "version_rollback")

    def test_invalid_stamp_and_tampering_are_skipped(self):
        old, new = self.pair()
        before, after = snapshot(old, BEFORE), snapshot(new, AFTER)
        for change in ("missing", "content", "timestamp"):
            with self.subTest(change=change):
                current = copy.deepcopy(after)
                if change == "missing":
                    del current["bundles"][0]["verified"]
                elif change == "content":
                    current["bundles"][0]["extraction"]["fields"]["apply"]["note"] = "tamper"
                else:
                    current["generated_at"] = "2026-10-02T11:00:00+09:00"
                self.assertEqual(compare_snapshots(before, current, [], checked_at=CHECKED)["skipped"][0]["reason"], "invalid_verification_stamp")

    def test_unchanged_deadline_does_not_hide_inconsistent_source_versions(self):
        old = sample(revision=2)[1]
        cases = [
            ({"version_id": 1, "version_number": 1}, "version_rollback"),
            ({"version_id": 3, "version_number": 1}, "version_rollback"),
            ({"version_id": 2, "version_number": 3}, "inconsistent_version_metadata"),
            ({"version_id": 3, "version_number": 2}, "inconsistent_version_metadata"),
            ({"input_hash": "a" * 64}, "inconsistent_version_metadata"),
            ({"url": "https://www.mju.ac.kr/bbs/ctl/792/1/artclView.do"}, "source_url_changed"),
        ]
        for update, reason in cases:
            with self.subTest(update=update):
                current = copy.deepcopy(old)
                current["notices"][0].update(update)
                result = self.compare(old, current)
                self.assertEqual(result["proposals"], [])
                self.assertEqual(result["unchanged_ids"], [])
                self.assertEqual(result["skipped"][0]["reason"], reason)
        repeated = self.compare(old, copy.deepcopy(old))
        self.assertEqual(repeated["skipped"], [])
        self.assertEqual(repeated["unchanged_ids"], [old["card_id"]])

    def test_verifier_unverifiable_short_quote_and_failed_kind_are_skipped(self):
        old_raw, old = sample()
        for name, quote in (("apply", "마감"), ("kind", "실제 원문에 없는 연장 근거")):
            with self.subTest(name=name):
                new_raw, new = sample("2026-10-23", revision=2)
                new["extraction"]["fields"][name]["evidence"][0]["quote"] = quote
                before, _ = verify_bundles({"generated_at": BEFORE, "bundles": [old]}, {old_raw["url"]: old_raw})
                after, checks = verify_bundles({"generated_at": AFTER, "bundles": [new]}, {new_raw["url"]: new_raw})
                self.assertTrue(checks)
                self.assertEqual(compare_snapshots(before, after, [], checked_at=CHECKED)["proposals"], [])

    def test_unconfirmed_inferred_image_attachment_and_missing_evidence_are_skipped(self):
        old, new = self.pair()
        variants = []
        for flag in ("needs_check", "conflict", "missing"):
            current = copy.deepcopy(new)
            current["extraction"]["fields"]["apply"]["flag"] = flag
            variants.append(current)
        current = copy.deepcopy(new)
        current["extraction"]["fields"]["apply"]["value"]["end"]["year_inferred"] = True
        variants.append(current)
        for location in ("이미지", "첨부"):
            current = copy.deepcopy(new)
            current["extraction"]["fields"]["apply"]["evidence"][0].update(location=location, file="poster.png")
            variants.append(current)
        current = copy.deepcopy(new)
        current["extraction"]["fields"]["apply"]["evidence"] = []
        variants.append(current)
        current = copy.deepcopy(new)
        current["statuses"]["apply"] = {"status": "needs_check", "evidence_failed": False}
        variants.append(current)
        for current in variants:
            with self.subTest(field=current["extraction"]["fields"]["apply"]):
                self.assertEqual(self.compare(old, current)["proposals"], [])

    def test_manual_cross_notice_group_requires_first_versions_order_and_update(self):
        old = sample()[1]
        new = sample("2026-10-23", number=2, posted="2026-09-02", kind="연장")[1]
        group = Group("fixed", (("mju_ctl", "ctl/792/1"), ("mju_ctl", "ctl/792/2")))
        result = self.compare(old, new, [group])
        self.assertEqual(result["proposals"][0]["id"], "fixed")
        self.assertEqual(self.compare(old, new)["proposals"], [])
        for key, value in (("version_number", 2), ("published_at", "2026-09-01")):
            current = copy.deepcopy(new)
            current["notices"][0][key] = value
            self.assertEqual(self.compare(old, current, [group])["proposals"], [])
        new["extraction"]["fields"]["kind"]["value"] = "모집"
        self.assertEqual(self.compare(old, new, [group])["proposals"], [])

    def test_same_date_time_precision_and_decreasing_extension_are_skipped(self):
        old, new = self.pair()
        new["extraction"]["fields"]["apply"]["value"]["end"].update(date="2026-09-30", time="18:00")
        self.assertEqual(self.compare(old, new)["skipped"][0]["reason"], "ambiguous_time_precision")
        new["extraction"]["fields"]["apply"]["value"]["end"].update(date="2026-09-29", time=None)
        new["extraction"]["fields"]["kind"]["value"] = "연장"
        self.assertEqual(self.compare(old, new)["skipped"][0]["reason"], "decreasing_extension")

    def test_duplicate_group_notices_are_ambiguous_and_missing_snapshots_are_reported(self):
        old, new = self.pair()
        before, after = snapshot(old, BEFORE), snapshot(new, AFTER)
        after["bundles"].append(copy.deepcopy(after["bundles"][0]))
        self.assertEqual(compare_snapshots(before, after, [], checked_at=CHECKED)["skipped"][0]["reason"], "ambiguous_multiple_notices")
        after["bundles"] = []
        self.assertEqual(compare_snapshots(before, after, [], checked_at=CHECKED)["skipped"][0]["reason"], "missing_snapshot_member")

    def test_timestamp_order_and_invalid_input_are_rejected(self):
        old, new = self.pair()
        for generated, checked in ((BEFORE, CHECKED), ("2026-10-02T10:00:00", CHECKED), (AFTER, BEFORE)):
            with self.subTest(generated=generated, checked=checked), self.assertRaises(ValueError):
                compare_snapshots(snapshot(old, BEFORE), snapshot(new, generated), [], checked_at=checked)
        with self.assertRaises(ValueError):
            compare_snapshots(None, {}, [], checked_at=CHECKED)

    def test_invalid_source_and_generation_metadata_never_produce_candidate(self):
        old, new = self.pair()
        for key, value in (("version_id", True), ("version_number", 0), ("input_hash", "bad"),
                           ("url", "https://evil.example/bbs/ctl/792/1/artclView.do"),
                           ("url", new["notices"][0]["url"] + "?private=test")):
            with self.subTest(key=key):
                current = copy.deepcopy(new)
                current["notices"][0][key] = value
                self.assertEqual(self.compare(old, current)["proposals"], [])
        for key in list(new["meta"]):
            current = copy.deepcopy(new)
            del current["meta"][key]
            if key in ("merge_model", "merge_model_id", "merge_prompt_fingerprint", "prompt_version"):
                self.assertEqual(self.compare(old, current)["proposals"], [])


if __name__ == "__main__":
    unittest.main()
