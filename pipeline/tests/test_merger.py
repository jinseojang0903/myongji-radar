"""실제 AI·학교 요청 없이 병합 식별자·근거·추출 마감 변경을 검증한다."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector.repository import content_hash
from extractor.daily import card_id_of
from merger.groups import Group, load_groups
from merger.merge import merge_document


def raw_field(value, flag="found", notice=1):
    return {"value": value, "flag": flag,
            "evidence": [{"quote": "원문 인용", "notice": notice, "location": "본문", "file": None}],
            "note": None, "alternatives": []}


def sample(number, end="2026-09-30", *, posted="2026-09-01", kind="모집", version=1,
           revision=1, model="sonnet", fingerprint="prompt1", campus=None):
    source_id = "mju_general"
    external_id = f"mjukr/141/{number}"
    original = {"source_id": source_id, "external_id": external_id, "version_id": version,
                "version_number": revision, "title": "예시 공모전", "body_text": "마감 " + end,
                "images": [], "attachments": [], "published_at": posted,
                "url": f"https://www.mju.ac.kr/bbs/{external_id}/artclView.do"}
    notice = {key: original[key] for key in ("source_id", "external_id", "version_id", "version_number", "title", "published_at", "url")}
    notice["input_hash"] = content_hash(original)
    notice["files"] = []
    fields = {"title": raw_field("예시 공모전"), "kind": raw_field(kind),
              "apply": raw_field({"start": None, "end": {"date": end, "time": None, "year_inferred": False}}),
              "campus": raw_field(campus if campus is not None else {"any": True, "allowed": []})}
    bundle = {"card_id": card_id_of(source_id, external_id), "notices": [notice],
              "extraction": {"is_contest": {"value": True, "reason": "공모전"}, "fields": fields, "warnings": []},
              "statuses": {"campus": {"status": "human_verified"}}, "deadline_history": [],
              "meta": {"prompt_version": "v0.2", "merge_model": model, "merge_model_id": model + "-actual",
                       "merge_prompt_fingerprint": fingerprint, "merge_apply_verified": True, "merge_kind_verified": True}}
    analysis = {"source_id": source_id, "external_id": external_id, "source_version_id": version,
                "classification": {"notice_kind": {"value": "extension" if kind == "연장" else "recruitment"}}}
    return bundle, original, analysis


def run_merge(samples, *, groups=None, previous=None):
    groups = groups if groups is not None else [Group("contest-2026", tuple((item[1]["source_id"], item[1]["external_id"]) for item in samples))]
    return merge_document({"generated_at": "2026-11-07T12:00:00+09:00", "bundles": [item[0] for item in samples]},
                          {(item[1]["source_id"], item[1]["external_id"]): item[1] for item in samples},
                          {"notices": [item[2] for item in samples]}, groups,
                          previous=previous, checked_at="2026-11-07T12:00:00+09:00")


class MergerTests(unittest.TestCase):
    def test_exact_group_and_source_order_remap_evidence_and_clear_review(self):
        first, second = sample(1), sample(2, posted="2026-09-02")
        second[0]["extraction"]["fields"]["title"]["alternatives"] = [{"text": "다른 표기", "notice": 1}]
        document = run_merge([second, first])
        merged = document["bundles"][0]
        self.assertEqual(merged["card_id"], "contest-2026")
        self.assertEqual([item["external_id"] for item in merged["notices"]], ["mjukr/141/1", "mjukr/141/2"])
        self.assertEqual(merged["extraction"]["fields"]["title"]["evidence"][0]["notice"], 2)
        self.assertEqual(merged["extraction"]["fields"]["title"]["alternatives"][0]["notice"], 2)
        self.assertEqual(merged["statuses"], {})
        self.assertEqual(second[0]["extraction"]["fields"]["title"]["evidence"][0]["notice"], 1, "입력을 수정하지 않는다")

    def test_only_registered_group_merges_and_id_collisions_are_rejected(self):
        first, second = sample(1), sample(2)
        group = Group("fixed-id", (("mju_general", "mjukr/141/1"),))
        document = run_merge([first, second], groups=[group])
        second_id = second[0]["card_id"]
        self.assertEqual({bundle["card_id"] for bundle in document["bundles"]}, {"fixed-id", second_id})
        with self.assertRaises(ValueError):
            run_merge([first, second], groups=[Group(second_id, group.members)])

    def test_explicit_extension_records_0930_to_1023_once(self):
        first = sample(1)
        second = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
        document = run_merge([first, second])
        history = document["bundles"][0]["deadline_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]["old"], history[0]["new"]), ("2026-09-30", "2026-10-23"))
        self.assertEqual(history[0]["old_source"], first[1]["url"])
        self.assertEqual(history[0]["new_source"], second[1]["url"])
        self.assertEqual(document["bundles"][0]["extraction"]["fields"]["apply"]["flag"], "found")
        repeated = run_merge([first, second], previous=document)
        self.assertEqual(repeated["bundles"][0]["deadline_history"], history)

    def test_conflicting_campus_is_not_unioned(self):
        first = sample(1, campus={"any": False, "allowed": ["자연"]})
        second = sample(2, posted="2026-09-02", campus={"any": False, "allowed": ["인문"]})
        field = run_merge([first, second])["bundles"][0]["extraction"]["fields"]["campus"]
        self.assertEqual(field["value"], {"any": False, "allowed": ["인문"]})
        self.assertEqual(field["flag"], "conflict")
        self.assertEqual(field["alternatives"][0]["notice"], 1)

    def test_unconfirmed_top_level_identity_is_withheld_not_exported_as_fact(self):
        for name, old, new in (("title", "예시 공모전", "다른 제목"),
                               ("organizer", "대학혁신지원사업단", "외부기관"),
                               ("scope", "교내", "외부")):
            with self.subTest(name=name):
                first, latest = sample(1), sample(2, posted="2026-09-02")
                first[0]["extraction"]["fields"][name] = raw_field(old)
                latest[0]["extraction"]["fields"][name] = raw_field(new)
                result = run_merge([first, latest])
                self.assertEqual(result["bundles"], [])
                self.assertEqual(result["merge_report"]["withheld"], 1)
                # 같은 원문의 재추출로 차이를 없애도 자동 확정하지 않는다.
                latest[0]["extraction"]["fields"][name] = raw_field(old)
                repeated = run_merge([first, latest], previous=result)
                self.assertEqual(repeated["bundles"], [])
                self.assertEqual(repeated["merge_report"]["withheld"], 1)

    def test_latest_missing_retains_older_value_only_as_unconfirmed(self):
        first, second = sample(1), sample(2, posted="2026-09-02")
        second[0]["extraction"]["fields"]["campus"] = raw_field(None, "missing")
        field = run_merge([first, second])["bundles"][0]["extraction"]["fields"]["campus"]
        self.assertEqual(field["flag"], "needs_check")
        self.assertEqual(field["value"], first[0]["extraction"]["fields"]["campus"]["value"])

    def test_latest_missing_key_cannot_promote_older_value(self):
        first, second = sample(1), sample(2, posted="2026-09-02")
        del second[0]["extraction"]["fields"]["campus"]
        result = run_merge([first, second])["bundles"][0]["extraction"]["fields"]["campus"]
        self.assertEqual(result["flag"], "needs_check")
        self.assertEqual(result["evidence"][0]["notice"], 1)
        self.assertEqual(result["value"], first[0]["extraction"]["fields"]["campus"]["value"])

    def test_earlier_uncertainty_is_not_erased_by_equal_later_value(self):
        first, second = sample(1), sample(2, posted="2026-09-02")
        for flag in ("needs_check", "conflict"):
            with self.subTest(flag=flag):
                first[0]["extraction"]["fields"]["campus"]["flag"] = flag
                result = run_merge([first, second])["bundles"][0]["extraction"]["fields"]["campus"]
                self.assertEqual(result["flag"], "needs_check")

    def test_missing_expected_notice_withholds_group_even_for_terminal_candidate(self):
        first = sample(1)
        group = Group("fixed", (("mju_general", "mjukr/141/1"), ("mju_general", "mjukr/141/2")))
        document = run_merge([first], groups=[group])
        self.assertEqual(document["bundles"], [])
        self.assertEqual(document["merge_report"]["withheld_ids"], ["fixed"])
        self.assertEqual(document["merge_state"]["cards"]["fixed"]["members"], [list(pair) for pair in group.members])
        first[2]["classification"]["notice_kind"]["value"] = "results"
        with patch("merger.merge.terminal_notice_is_grounded", return_value=True):
            document = run_merge([first], groups=[group])
        self.assertEqual(document["merge_report"]["withheld_ids"], ["fixed"])
        self.assertEqual(document["bundles"], [])

    def test_delayed_notice_withholds_group_without_ai_bundle(self):
        first, delayed = sample(1), sample(2, posted="2026-09-02")
        delayed[2]["classification"]["notice_kind"]["value"] = "result_delayed"
        group = Group("fixed", (("mju_general", "mjukr/141/1"), ("mju_general", "mjukr/141/2")))
        document = merge_document({"bundles": [first[0]]},
                                  {("mju_general", item[1]["external_id"]): item[1] for item in (first, delayed)},
                                  {"notices": [first[2], delayed[2]]}, [group], checked_at="2026-11-07")
        self.assertEqual(document["merge_report"]["withheld_ids"], ["fixed"])
        self.assertEqual(document["bundles"], [])

    def test_same_url_real_versions_keep_change_and_reversion_history(self):
        previous = None
        for revision, end in enumerate(("2026-09-30", "2026-10-23", "2026-09-30", "2026-10-23"), 1):
            current = sample(1, end, version=revision, revision=revision)
            previous = run_merge([current], previous=previous)
        history = previous["bundles"][0]["deadline_history"]
        self.assertEqual(len(history), 3)
        self.assertEqual([item["new"] for item in history], ["2026-10-23", "2026-09-30", "2026-10-23"])

    def test_latest_notice_edit_continues_history_without_rewriting_older_notice(self):
        first = sample(1)
        latest = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
        previous = run_merge([first, latest])
        for revision, end in ((2, "2026-10-25"), (3, "2026-10-27")):
            latest = sample(2, end, posted="2026-10-07", kind="연장", version=revision + 1, revision=revision)
            previous = run_merge([first, latest], previous=previous)
            self.assertEqual(previous["bundles"][0]["extraction"]["fields"]["apply"]["flag"], "found")
        self.assertEqual([item["new"] for item in previous["bundles"][0]["deadline_history"]],
                         ["2026-10-23", "2026-10-25", "2026-10-27"])

    def test_reextract_same_raw_changes_no_history_and_remains_conflict(self):
        first = sample(1)
        previous = run_merge([first])
        changed = copy.deepcopy(first)
        changed[0]["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-10-23"
        document = run_merge([changed], previous=previous)
        self.assertEqual(document["bundles"][0]["deadline_history"], [])
        self.assertEqual(document["bundles"][0]["extraction"]["fields"]["apply"]["flag"], "conflict")
        self.assertEqual(run_merge([changed], previous=document)["bundles"][0]["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_reextract_after_unverified_latest_does_not_create_initial_event(self):
        first = sample(1)
        latest = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
        latest[0]["extraction"]["fields"]["apply"]["flag"] = "needs_check"
        previous = run_merge([first, latest])
        self.assertEqual(previous["bundles"][0]["deadline_history"], [])
        corrected = copy.deepcopy(latest)
        corrected[0]["extraction"]["fields"]["apply"]["flag"] = "found"
        result = run_merge([first, corrected], previous=previous)["bundles"][0]
        self.assertEqual(result["deadline_history"], [])
        self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_initial_extension_requires_grounded_kind_and_rule_stage(self):
        first = sample(1)
        for change in ("needs_check", "conflict", "missing", "unverified", "unknown_stage"):
            latest = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
            if change in ("needs_check", "conflict", "missing"):
                latest[0]["extraction"]["fields"]["kind"]["flag"] = change
            elif change == "unverified":
                latest[0]["meta"]["merge_kind_verified"] = False
            else:
                latest[2]["classification"]["notice_kind"]["value"] = "unknown"
            with self.subTest(change=change):
                result = run_merge([first, latest])["bundles"][0]
                self.assertEqual(result["deadline_history"], [])
                self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_ambiguous_time_precision_or_decreasing_extension_is_not_history(self):
        first = sample(1)
        same_day = sample(2, posted="2026-10-07", kind="연장")
        same_day[0]["extraction"]["fields"]["apply"]["value"]["end"]["time"] = "15:00"
        earlier = sample(2, "2026-09-20", posted="2026-10-07", kind="연장")
        for latest in (same_day, earlier):
            result = run_merge([first, latest])["bundles"][0]
            self.assertEqual(result["deadline_history"], [])
            self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_version_rollback_stops_all_fields_and_missing_intermediate_version_has_no_event(self):
        previous = run_merge([sample(1, "2026-10-23", version=2, revision=2)])
        with self.assertRaises(ValueError):
            run_merge([sample(1, "2026-09-30", version=1, revision=1)], previous=previous)
        previous = run_merge([sample(1, "2026-09-30", version=1, revision=1)])
        result = run_merge([sample(1, "2026-11-01", version=3, revision=3)], previous=previous)["bundles"][0]
        self.assertEqual(result["deadline_history"], [])
        self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_unverified_intermediate_revision_breaks_history_chain(self):
        previous = run_merge([sample(1, "2026-09-30", version=1, revision=1)])
        middle = sample(1, "2026-10-23", version=2, revision=2)
        middle[0]["meta"]["merge_apply_verified"] = False
        previous = run_merge([middle], previous=previous)
        result = run_merge([sample(1, "2026-11-01", version=3, revision=3)], previous=previous)["bundles"][0]
        self.assertEqual(result["deadline_history"], [])

    def test_unverified_middle_notice_breaks_initial_cross_notice_history(self):
        first = sample(1)
        middle = sample(2, "2026-10-10", posted="2026-10-02")
        middle[0]["extraction"]["fields"]["apply"]["flag"] = "needs_check"
        middle[0]["meta"]["merge_apply_verified"] = False
        latest = sample(3, "2026-10-23", posted="2026-10-07", kind="연장")
        result = run_merge([first, middle, latest])["bundles"][0]
        self.assertEqual(result["deadline_history"], [])
        self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_reextract_and_new_member_cannot_fill_initial_history_gap(self):
        first = sample(1)
        first[0]["extraction"]["fields"]["apply"]["flag"] = "needs_check"
        previous = run_merge([first])
        corrected = copy.deepcopy(first)
        corrected[0]["extraction"]["fields"]["apply"]["flag"] = "found"
        latest = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
        result = run_merge([corrected, latest], previous=previous)["bundles"][0]
        self.assertEqual(result["deadline_history"], [])
        self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_existing_history_is_quarantined_when_same_raw_is_reinterpreted(self):
        previous = run_merge([sample(1, "2026-09-30", version=1, revision=1)])
        second = sample(1, "2026-10-23", version=2, revision=2)
        previous = run_merge([second], previous=previous)
        history = previous["bundles"][0]["deadline_history"]
        self.assertEqual(len(history), 1)
        changed = copy.deepcopy(second)
        changed[0]["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-10-24"
        result = run_merge([changed], previous=previous)
        self.assertEqual(result["bundles"][0]["deadline_history"], [])
        state = result["merge_state"]["cards"]["contest-2026"]
        self.assertTrue(state["history_quarantined"])
        self.assertEqual([event["change"] for event in state["events"]], history)
        self.assertEqual(result["merge_report"]["history_review"], 1)
        later = run_merge([sample(1, "2026-11-01", version=3, revision=3)], previous=result)
        self.assertEqual(later["bundles"][0]["deadline_history"], [])
        self.assertEqual(later["merge_report"]["history_review"], 1)

    def test_old_source_reextract_with_added_group_member_is_not_history(self):
        first = sample(1)
        previous = run_merge([first])
        reextracted = copy.deepcopy(first)
        reextracted[0]["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-10-10"
        latest = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
        result = run_merge([reextracted, latest], previous=previous)["bundles"][0]
        self.assertEqual(result["deadline_history"], [])
        self.assertEqual(result["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_closed_group_is_not_reopened_by_restored_analysis(self):
        first = sample(1)
        first[2]["classification"]["notice_kind"]["value"] = "cancellation"
        with patch("merger.merge.terminal_notice_is_grounded", return_value=True):
            closed = run_merge([first])
        first[2]["classification"]["notice_kind"]["value"] = "recruitment"
        result = run_merge([first], previous=closed)
        self.assertEqual(result["bundles"], [])
        self.assertEqual(result["merge_report"]["closed"], 1)

    def test_group_id_rename_or_removal_fails_closed(self):
        first = sample(1)
        previous = run_merge([first])
        members = ((first[1]["source_id"], first[1]["external_id"]),)
        for groups in ([Group("renamed", members)], []):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                run_merge([first], groups=groups, previous=previous)

    def test_ai_terminal_short_quote_alone_never_confirms_closure(self):
        first, latest = sample(1), sample(2, posted="2026-09-02")
        first[0]["extraction"]["fields"]["kind"] = raw_field("취소")
        first[0]["meta"]["merge_terminal_verified"] = False
        document = run_merge([first, latest])
        self.assertEqual(document["bundles"], [])
        self.assertEqual(document["merge_report"]["withheld"], 1)
        first[0]["meta"]["merge_terminal_verified"] = True
        document = run_merge([first, latest])
        self.assertEqual(document["bundles"], [])
        self.assertEqual(document["merge_report"]["withheld"], 1)

    def test_confirmed_terminal_must_follow_every_other_group_notice(self):
        first, latest = sample(1), sample(2, posted="2026-09-02")
        first[2]["classification"]["notice_kind"]["value"] = "results"
        with patch("merger.merge.terminal_notice_is_grounded", return_value=True):
            old_result = run_merge([first, latest])
            self.assertEqual(old_result["merge_report"]["withheld"], 1)
            first[2]["classification"]["notice_kind"]["value"] = "recruitment"
            latest[2]["classification"]["notice_kind"]["value"] = "cancellation"
            final_result = run_merge([first, latest])
            self.assertEqual(final_result["merge_report"]["closed"], 1)
            same_day = sample(2, posted="2026-09-01")
            same_day[2]["classification"]["notice_kind"]["value"] = "cancellation"
            ambiguous_order = run_merge([first, same_day])
            self.assertEqual(ambiguous_order["merge_report"]["withheld"], 1)

    def test_unverified_terminal_hold_survives_same_raw_reextract(self):
        first = sample(1)
        first[0]["extraction"]["fields"]["kind"] = raw_field("취소", "needs_check")
        first[0]["meta"]["merge_terminal_verified"] = False
        held = run_merge([first])
        self.assertEqual(held["bundles"], [])
        first[0]["extraction"]["fields"]["kind"] = raw_field("모집")
        still_held = run_merge([first], previous=held)
        self.assertEqual(still_held["bundles"], [])
        self.assertEqual(still_held["merge_report"]["withheld"], 1)
        changed = sample(1, version=2, revision=2)
        changed[1]["body_text"] += " 원문이 실제로 수정됨"
        changed[0]["notices"][0]["input_hash"] = content_hash(changed[1])
        resolved = run_merge([changed], previous=still_held)
        self.assertEqual(resolved["merge_report"]["merged"], 1)

    def test_long_existing_note_cannot_hide_new_conflict_reason(self):
        first = sample(1, campus={"any": False, "allowed": ["자연"]})
        second = sample(2, posted="2026-09-02", campus={"any": False, "allowed": ["인문"]})
        second[0]["extraction"]["fields"]["campus"]["note"] = "가" * 300
        result = run_merge([first, second])["bundles"][0]["extraction"]["fields"]["campus"]
        self.assertLessEqual(len(result["note"]), 300)
        self.assertIn("충돌", result["note"])

    def test_different_model_prompt_legacy_revision_or_same_day_do_not_invent_initial_change(self):
        for changes in ({"model": "opus"}, {"fingerprint": "other"}, {"revision": 2}, {"posted": "2026-09-01"}):
            first = sample(1)
            kwargs = {"posted": "2026-10-07", "kind": "연장", **changes}
            second = sample(2, "2026-10-23", **kwargs)
            with self.subTest(changes=changes):
                document = run_merge([first, second])
                self.assertEqual(document["bundles"][0]["deadline_history"], [])
                self.assertEqual(document["bundles"][0]["extraction"]["fields"]["apply"]["flag"], "conflict")

    def test_unverified_deadline_and_inferred_year_never_create_history(self):
        for unverified in (True, False):
            first = sample(1)
            second = sample(2, "2026-10-23", posted="2026-10-07", kind="연장")
            if unverified:
                second[0]["meta"]["merge_apply_verified"] = False
            else:
                second[0]["extraction"]["fields"]["apply"]["value"]["end"]["year_inferred"] = True
            self.assertEqual(run_merge([first, second])["bundles"][0]["deadline_history"], [])

    def test_stale_raw_duplicate_identity_or_wrong_evidence_number_rejected(self):
        first = sample(1)
        for change in ("version", "hash", "url", "number"):
            item = copy.deepcopy(first)
            if change == "number":
                item[0]["extraction"]["fields"]["apply"]["evidence"][0]["notice"] = 2
            else:
                item[0]["notices"][0][{"version": "version_id", "hash": "input_hash", "url": "url"}[change]] = 999 if change == "version" else "wrong"
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_merge([item])
        with self.assertRaises(ValueError):
            run_merge([first, copy.deepcopy(first)], groups=[])

    def test_invalid_previous_state_or_group_reassignment_rejected(self):
        first = sample(1)
        previous = run_merge([first])
        with self.assertRaises(ValueError):
            run_merge([first], previous={})
        changed = sample(2)
        with self.assertRaises(ValueError):
            run_merge([changed], previous=previous)


class GroupConfigTests(unittest.TestCase):
    def test_strict_config_and_cross_group_duplicates(self):
        valid = 'version=1\n[[groups]]\nid="contest-2026"\nmembers=[{source_id="mju_ctl", external_id="ctl/792/1"}]\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "groups.toml"
            path.write_text(valid, encoding="utf-8")
            self.assertEqual(load_groups(path), [Group("contest-2026", (("mju_ctl", "ctl/792/1"),))])
            for text in (valid + 'extra=true', valid.replace('version=1', 'version=true'),
                         valid.replace('contest-2026', '../bad'), valid.replace('members=[{source_id="mju_ctl", external_id="ctl/792/1"}]', 'members=[]'),
                         valid + valid.split('version=1\n', 1)[1].replace('contest-2026', 'other')):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_groups(path)


if __name__ == "__main__":
    unittest.main()
