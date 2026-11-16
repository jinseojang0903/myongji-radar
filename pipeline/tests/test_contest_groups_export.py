"""같은 대회 묶음(#16)을 공개 카드 한 장으로 합치기: exporter/contest_groups.py, 묶음 단위 차단, CLI 연결."""

import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from exporter.cards import CardBundle, build_cards_json
from exporter.contest_groups import DIFFERENT_NOTE, group_aliases, merge_groups
from merger.groups import Group
import test_exporter as exporter_tests
from test_exporter import GENERATED_AT, META, example_fields, extraction

OLD_URL = "https://innov.mju.ac.kr/bbs/innovation/56/235961/artclView.do"
NEW_URL = "https://www.mju.ac.kr/bbs/mjukr/141/236150/artclView.do"
GROUP = Group("history-video-5", (("mju_innovation", "innovation/56/235961"), ("mju_general", "mjukr/141/236150")))
OLD_ID, NEW_ID = "mju-innovation-innovation-56-235961", "mju-general-mjukr-141-236150"


def field(value, status="ai", quote="근거", source=NEW_URL, note=None):
    item = {"value": value, "status": status,
            "evidence": [{"quote": quote, "location": "본문", "source": source}] if value is not None else []}
    if note:
        item["note"] = note
    return item


def card(card_id, url, posted, board, end, **fields):
    base = {"apply": field({"start": None, "end": end, "time_given": False}, source=url),
            "campus": field("any", source=url),
            "benefits": field("상장", source=url)}
    base.update(fields)
    return {"id": card_id, "title": "역사 영상 공모전", "organizer": "방목기초교육대학", "scope": "교내",
            "sources": [{"board_id": board, "url": url, "title": "공지", "posted_at": posted, "last_seen_at": None}],
            "fields": base, "deadline_history": [], "meta": dict(META)}


def old_card(**fields):
    return card(OLD_ID, OLD_URL, "2026-10-02", "mju-innovation", "2026-09-30", **fields)


def new_card(**fields):
    return card(NEW_ID, NEW_URL, "2026-10-07", "mju-general", "2026-10-23", **fields)


class MergeGroupsTests(unittest.TestCase):
    def test_merges_into_one_card_with_all_sources_newest_first_and_deadline_history(self):
        other = card("other-card", "https://www.mju.ac.kr/bbs/mjukr/141/1/artclView.do", "2026-10-05", "mju-general", "2026-10-10")
        cards, report = merge_groups([old_card(), other, new_card()], [GROUP], "2026-11-16")
        self.assertEqual([item["id"] for item in cards], ["history-video-5", "other-card"], "묶음은 앞 구성 카드 자리에")
        merged = cards[0]
        self.assertEqual([source["url"] for source in merged["sources"]], [NEW_URL, OLD_URL], "계약: 첫 번째가 최신")
        self.assertEqual(merged["fields"]["apply"]["value"]["end"], "2026-10-23", "값은 가장 최근 공지 것")
        self.assertEqual(merged["deadline_history"], [{"field": "apply_end", "old": "2026-09-30", "new": "2026-10-23",
                                                       "old_source": OLD_URL, "new_source": NEW_URL, "checked_at": "2026-11-16"}])
        self.assertEqual(report, [{"id": "history-video-5", "reason": "카드 2장을 한 장으로 (apply 다름)"}])

    def test_judged_field_that_differs_is_lowered_never_raised(self):
        cards, _ = merge_groups([old_card(), new_card()], [GROUP], "2026-11-16")
        apply = cards[0]["fields"]["apply"]
        self.assertEqual(apply["status"], "needs_check")
        self.assertTrue(apply["note"].startswith(DIFFERENT_NOTE))
        self.assertEqual(cards[0]["fields"]["campus"]["status"], "ai", "값이 같은 항목은 그대로")
        # 판정에 쓰지 않는 항목은 달라도 최신 값을 그대로 쓴다
        cards, _ = merge_groups([old_card(benefits=field("상금 10만 원")), new_card()], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["fields"]["benefits"], field("상장", source=NEW_URL))
        # 이미 확인 필요인 항목은 그대로 (올리지 않는다), 자동 검증 통과는 낮춘다
        cards, _ = merge_groups([old_card(campus=field(["인문"], status="auto_verified", source=OLD_URL)),
                                 new_card(campus=field("any", status="auto_verified"))], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["fields"]["campus"]["status"], "needs_check")

    def test_missing_field_in_newest_card_uses_older_card_field_with_its_evidence(self):
        older_campus = field(["자연"], source=OLD_URL)
        cards, _ = merge_groups([old_card(campus=older_campus), new_card(campus=field(None, status="missing"))],
                                [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["fields"]["campus"], older_campus)
        self.assertEqual(cards[0]["fields"]["campus"]["evidence"][0]["source"], OLD_URL, "근거는 값이 나온 공지")

    def test_missing_top_values_fall_back_to_older_card(self):
        newest = new_card()
        newest["organizer"] = None
        cards, _ = merge_groups([old_card(), newest], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["organizer"], "방목기초교육대학")

    def test_same_deadline_adds_no_history_and_own_history_is_kept_once(self):
        own = {"field": "apply_end", "old": "2026-09-30", "new": "2026-10-23", "old_source": OLD_URL,
               "new_source": NEW_URL, "checked_at": "2026-11-10"}
        newest = new_card()
        newest["deadline_history"] = [own]
        cards, _ = merge_groups([old_card(), newest], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["deadline_history"], [own], "같은 변경은 한 번만")
        same = old_card()
        same["fields"]["apply"]["value"]["end"] = "2026-10-23"
        cards, _ = merge_groups([same, new_card()], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["deadline_history"], [])
        self.assertEqual(cards[0]["fields"]["apply"]["status"], "ai")
        # 한쪽에만 시각이 있는 같은 날짜는 변경 이력이 아니다
        timed = old_card()
        timed["fields"]["apply"]["value"]["end"] = "2026-10-23 15:00"
        cards, _ = merge_groups([timed, new_card()], [GROUP], "2026-11-16")
        self.assertEqual(cards[0]["deadline_history"], [])

    def test_one_present_member_or_id_collision_keeps_cards_unchanged(self):
        only = [new_card()]
        self.assertEqual(merge_groups(only, [GROUP], "2026-11-16"), (only, []))
        colliding = card("history-video-5", "https://www.mju.ac.kr/bbs/mjukr/141/2/artclView.do", "2026-10-01", "mju-general", None)
        cards, report = merge_groups([old_card(), new_card(), colliding], [GROUP], "2026-11-16")
        self.assertEqual(len(cards), 3)
        self.assertIn("합치지 않음", report[0]["reason"])

    def test_input_cards_are_not_modified(self):
        original = [old_card(), new_card()]
        snapshot = copy.deepcopy(original)
        merge_groups(original, [GROUP], "2026-11-16")
        self.assertEqual(original, snapshot)


def member_bundle(card_id, notice, end):
    fields = example_fields()
    fields["apply"]["value"]["end"]["date"] = end
    return CardBundle(card_id=card_id, notices=[notice], extraction=extraction(fields), meta=dict(META))


OLD_NOTICE = {"source_id": "mju_innovation", "url": OLD_URL, "title": "역사 영상 공모전", "published_at": "2026-10-02", "files": []}
NEW_NOTICE = {"source_id": "mju_general", "url": NEW_URL, "title": "역사 영상 공모전(연장)", "published_at": "2026-10-07", "files": []}


def group_bundles():
    return [member_bundle(OLD_ID, OLD_NOTICE, "2026-09-30"), member_bundle(NEW_ID, NEW_NOTICE, "2026-10-23")]


class GroupBlockingTests(unittest.TestCase):
    def test_blocking_group_id_or_one_member_blocks_every_member(self):
        aliases = group_aliases([GROUP])
        for blocked in ({"history-video-5"}, {OLD_ID}):
            data, report = build_cards_json(group_bundles(), [], GENERATED_AT, blocked=blocked, aliases=aliases)
            self.assertEqual(data["cards"], [], blocked)
            self.assertEqual(sorted(item["id"] for item in report["blocked"]), sorted([OLD_ID, NEW_ID]))
            self.assertFalse(any("차단 목록에 있지만" in item["reason"] for item in report["warnings"]), "묶음 id도 찾은 것으로 본다")

    def test_without_aliases_blocking_is_per_card_as_before(self):
        data, _ = build_cards_json(group_bundles(), [], GENERATED_AT, blocked={OLD_ID})
        self.assertEqual([item["id"] for item in data["cards"]], [NEW_ID])


GROUPS_TOML = """version = 1
[[groups]]
id = "history-video-5"
members = [
  {source_id = "mju_innovation", external_id = "innovation/56/235961"},
  {source_id = "mju_general", external_id = "mjukr/141/236150"},
]
"""


class GroupCliTests(unittest.TestCase):
    # 실행 도우미만 빌려 쓴다 (클래스를 이어받으면 test_exporter의 시험이 이 파일에서 한 번 더 돈다)
    run_cli = exporter_tests.ExporterCliTests.run_cli
    bundle_json = exporter_tests.ExporterCliTests.bundle_json

    def run_groups(self, groups_text):
        with tempfile.TemporaryDirectory() as folder:
            out, report_path, groups = (Path(folder) / name for name in ("cards.json", "report.json", "groups.toml"))
            groups.write_text(groups_text, encoding="utf-8")
            code, errors = self.run_cli(self.bundle_json(group_bundles()), out, ["--groups", str(groups), "--report", str(report_path)])
            cards = json.loads(out.read_text(encoding="utf-8"))["cards"] if out.exists() else None
            return code, errors, cards, json.loads(report_path.read_text(encoding="utf-8"))

    def test_cli_publishes_one_card_per_confirmed_group(self):
        code, errors, cards, report = self.run_groups(GROUPS_TOML)
        self.assertEqual([item["id"] for item in cards], ["history-video-5"])
        self.assertEqual(len(cards[0]["sources"]), 2)
        self.assertTrue(cards[0]["deadline_history"][0]["new"].startswith("2026-10-23"))
        self.assertIn("[묶음] history-video-5", errors)
        self.assertEqual(report["counts"]["merged"], 1)
        self.assertEqual(report["merged"][0]["id"], "history-video-5")
        self.assertIn(code, (0, 1))

    def test_cli_unreadable_groups_publishes_cards_unmerged_and_warns(self):
        code, errors, cards, report = self.run_groups("version = 2\n")
        self.assertEqual(sorted(item["id"] for item in cards), sorted([OLD_ID, NEW_ID]))
        self.assertEqual(code, 1)
        self.assertIn("같은 대회 묶음을 읽지 못해 합치지 않고 공개", errors)
        self.assertEqual(report["counts"]["merged"], 0)


if __name__ == "__main__":
    unittest.main()
