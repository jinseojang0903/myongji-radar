"""묶음의 조건·출처·공개 근거가 함께 남고 검수로 충돌을 덮지 않는지 검사한다."""

import copy
import json
import unittest
from pathlib import Path

from exporter.cards import build_cards_json
from exporter.contest_groups import merge_groups, _merge
from exporter.reviews import is_promotable
from test_contest_groups_export import GROUP, NEW_URL, OLD_URL, field, new_card, old_card, group_bundles
from test_exporter import GENERATED_AT


FIXTURE = Path(__file__).parent / "fixtures/group_conflicts.json"


def public_conflict_fixture():
    bundles = group_bundles()
    for bundle, campus in zip(bundles, ["인문", "자연"]):
        bundle.extraction["fields"]["campus"] = {
            "value": {"any": False, "allowed": [campus]}, "flag": "found", "note": None,
            "alternatives": [], "evidence": [{"notice": 1, "location": "본문", "file": None,
                "quote": f"{campus} 캠퍼스 참가 가능. 개인 연락처 010-9876-5432, private-person@example.com"}]}
    data, _ = build_cards_json(bundles, [], GENERATED_AT)
    data["cards"], _ = merge_groups(data["cards"], [GROUP])
    return data


class ConflictTests(unittest.TestCase):
    def test_public_export_fixture_keeps_both_sides_without_private_contacts(self):
        data = public_conflict_fixture()
        serialized = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("010-9876-5432", serialized)
        self.assertNotIn("private-person@example.com", serialized)
        item = data["cards"][0]["fields"]["campus"]
        self.assertEqual(len(item["conflicts"]), 2)
        self.assertTrue(all(v["evidence"] for v in item["conflicts"]))
        self.assertEqual(data, json.loads(FIXTURE.read_text(encoding="utf-8")))

    def test_each_condition_preserves_both_values_and_matching_sources(self):
        cases = {
            "campus": (["인문"], ["자연"]),
            "grades": ([1, 2], [3, 4]),
            "enrollment": ({"allowed": ["재학"], "excluded": ["휴학"]},
                           {"allowed": ["재학", "휴학"], "excluded": []}),
            "team": ({"min": 2, "max": 3, "individual_ok": False},
                     {"min": 1, "max": 5, "individual_ok": True}),
        }
        for name, (older, newer) in cases.items():
            with self.subTest(name=name):
                cards = [old_card(**{name: field(older, quote="앞 공지 조건", source=OLD_URL)}),
                         new_card(**{name: field(newer, status="human_verified", quote="최근 공지 조건")})]
                original = copy.deepcopy(cards)
                merged = merge_groups(cards, [GROUP])[0][0]["fields"][name]
                self.assertEqual(cards, original, "입력 공개 카드는 바꾸지 않는다")
                self.assertEqual(merged["value"], newer)
                self.assertEqual(merged["status"], "needs_check")
                self.assertEqual([v["value"] for v in merged["conflicts"]], [newer, older])
                self.assertEqual([v["source"] for v in merged["conflicts"]], [NEW_URL, OLD_URL])
                self.assertEqual([v["evidence"][0]["quote"] for v in merged["conflicts"]], ["최근 공지 조건", "앞 공지 조건"])
                self.assertTrue(all(e in merged["evidence"] for v in merged["conflicts"] for e in v["evidence"]))
                self.assertFalse(is_promotable(dict(merged, reviewable=True)))
                self.assertFalse(is_promotable(dict(merged, status="auto_verified")))

    def test_equal_values_with_different_quotes_do_not_conflict(self):
        older = old_card(campus=field(["자연", "인문"], source=OLD_URL, quote="양 캠퍼스"))
        newer = new_card(campus=field(["인문", "자연"], quote="인문 및 자연 캠퍼스"))
        item = merge_groups([older, newer], [GROUP])[0][0]["fields"]["campus"]
        self.assertNotIn("conflicts", item)
        self.assertEqual(item["status"], "ai")

    def test_equal_quotes_do_not_hide_different_values(self):
        item = merge_groups([old_card(campus=field(["자연"], source=OLD_URL)),
                             new_card(campus=field(["인문"]))], [GROUP])[0][0]["fields"]["campus"]
        self.assertEqual(len(item["conflicts"]), 2)
        self.assertEqual(len(item["evidence"]), 2, "문장이 같아도 서로 다른 출처를 보존")

    def test_uncertainty_without_value_difference_is_not_explicit_conflict(self):
        item = merge_groups([old_card(campus=field("any", status="needs_check", source=OLD_URL)),
                             new_card()], [GROUP])[0][0]["fields"]["campus"]
        self.assertEqual(item["status"], "needs_check")
        self.assertNotIn("conflicts", item)
        self.assertNotIn("reviewable", item)

    def test_many_sources_are_bounded_and_disclosed(self):
        cards = []
        for index in range(7):
            c = new_card(campus=field([str(index)], quote="가" * 199 + str(index),
                                     note="확인" * 150))
            url = NEW_URL.replace("236150", str(236150 + index))
            c["id"] = f"source-{index}"
            c["sources"][0]["url"] = url
            for item in c["fields"].values():
                for evidence in item["evidence"]:
                    evidence["source"] = url
                item["note"] = "확인" * 150
            cards.append(c)
        merged = _merge("bounded", cards)[0]
        item = merged["fields"]["campus"]
        self.assertEqual(len(item["conflicts"]), 5)
        self.assertIn("최신 5개만", item["note"])
        self.assertEqual(len(merged["sources"]), 7)
        self.assertLessEqual(sum(len(i.get("note") or "") for i in merged["fields"].values()), 1000)
        quotes = {e["quote"] for i in merged["fields"].values() for e in i["evidence"]}
        self.assertLessEqual(sum(map(len, quotes)), 1500)
        self.assertTrue(all(len(i["evidence"]) <= 5 for i in merged["fields"].values()))
        self.assertTrue(all(e in item["evidence"] for v in item["conflicts"] for e in v["evidence"]))

    def test_card_wide_quote_and_note_budgets_keep_values_when_quotes_do_not_fit(self):
        pairs = {
            "apply": ({"start": None, "end": "2026-10-01", "time_given": False},
                      {"start": None, "end": "2026-10-23", "time_given": False}),
            "campus": (["인문"], ["자연"]), "grades": ([1], [2]),
            "enrollment": ({"allowed": ["재학"], "excluded": ["휴학"]},
                           {"allowed": ["재학"], "excluded": ["수료"]}),
            "team": ({"min": 2, "max": 3, "individual_ok": False},
                     {"min": 2, "max": 4, "individual_ok": False}),
        }
        older, newer = old_card(), new_card()
        for index, (name, values) in enumerate(pairs.items()):
            older["fields"][name] = field(values[0], source=OLD_URL, quote=chr(0xAC00 + index) * 200, note="확인" * 150)
            newer["fields"][name] = field(values[1], quote=chr(0xB000 + index) * 200, note="확인" * 150)
        merged = merge_groups([older, newer], [GROUP])[0][0]
        quotes = {e["quote"] for item in merged["fields"].values() for e in item["evidence"]}
        self.assertLessEqual(sum(map(len, quotes)), 1500)
        self.assertLessEqual(sum(len(item.get("note") or "") for item in merged["fields"].values()), 1000)
        self.assertTrue(any(not v["evidence"] for name in pairs for v in merged["fields"][name]["conflicts"]))
        for name in pairs:
            item = merged["fields"][name]
            self.assertEqual(len(item["conflicts"]), 2, "상한 때문에 인용을 생략해도 양쪽 값·출처는 남긴다")
            self.assertEqual(item["status"], "needs_check")
            self.assertTrue(all(e in item["evidence"] for v in item["conflicts"] for e in v["evidence"]))
