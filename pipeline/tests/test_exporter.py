"""cards.json 내보내기(#17) 테스트: 계약 형식, flag·검증 → status, 날짜 변환, 안전 검사
(키 화이트리스트·길이·근거 합계·본문 유출·링크 허용 목록·개인 연락처·보이지 않는 글자), CLI 종료 코드.

pipeline/tests/fixtures/exported_cards.json은 이 테스트가 만드는 결과와 같아야 한다. 사이트 쪽 Node 테스트
(tests/export_contract.test.js)가 같은 파일을 사이트의 형식 검사·판정으로 다시 확인한다 (파이프라인과 사이트가 같은 계약을 보는지).
결과를 바꿨으면 `UPDATE_FIXTURES=1 python -m unittest tests.test_exporter`로 다시 만든다.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from collector.models import Source
from exporter.__main__ import main as cli_main
from exporter.cards import (
    CARD_FIELDS, MAX_CARD_QUOTE_CHARS, MAX_EVIDENCE, MAX_QUOTE_CHARS, MAX_TEXT_CHARS, CardBundle, CardExportError,
    CardSkipped, boards_from_sources, build_card, build_cards_json, write_cards_json,
)
from verifier.stamp import stamp_bundle

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "prompts/extract_v0.2/example_output.json").read_text(encoding="utf-8"))
FIXTURE = Path(__file__).parent / "fixtures" / "exported_cards.json"
EXAMPLE_FILES = sorted({item["file"] for value in EXAMPLE["fields"].values() for item in value.get("evidence", []) if item.get("file")})
NOTICE = {"source_id": "mju_ctl", "url": "https://ctl.mju.ac.kr/bbs/ctl/792/235972/artclView.do",
          "title": "2026학년도 AI 리터러시 경진대회 개최 안내", "published_at": "2026-09-29T10:00:00+09:00", "files": EXAMPLE_FILES}
META = {"prompt_version": "v0.2", "extract_model": "시험 고정값", "cross_model": None, "checked_at": "2026-11-05"}
KEY_FIELDS = ["apply", "campus", "grades", "enrollment", "team"]
CONTRACT_CARD_KEYS = {"id", "title", "organizer", "scope", "sources", "fields", "deadline_history", "meta"}
CONTRACT_FIELD_KEYS = {"value", "status", "evidence", "note"}
GENERATED_AT = "2026-11-05T09:00:00+09:00"


def extraction(fields, warnings=()):
    return {"is_contest": {"value": True, "reason": ""}, "fields": fields, "warnings": list(warnings)}


def example_fields():
    return copy.deepcopy(EXAMPLE["fields"])


def bundle(**changes):
    base = CardBundle(card_id="ai-literacy-2026", notices=[dict(NOTICE)], extraction=copy.deepcopy(EXAMPLE), meta=dict(META))
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def with_fields(fields, **changes):
    return bundle(extraction=extraction(fields), **changes)


def field(value, flag="found", quote="근거 문장", **extra):
    return {"value": value, "flag": flag, "evidence": [{"quote": quote, "notice": 1, "location": "본문", "file": None}] if quote else [],
            "note": None, "alternatives": [], **extra}


def verified(fields, names, status="human_verified"):
    """#15가 줄 검증 결과: 검증한 값을 함께 준다."""
    return {name: {"status": status, "checked_value": copy.deepcopy(fields[name]["value"])} for name in names}


def one_field(name, raw, **changes):
    fields = example_fields()
    fields[name] = raw
    return build_card(with_fields(fields, **changes))["fields"][name]


def checked_contact(raw):
    """사람이 검수한 문의처 (학교 메일은 검수 뒤에만 공개되므로)."""
    return one_field("contact", raw, statuses={"contact": {"status": "human_verified", "checked_value": copy.deepcopy(raw["value"])}})


def fixture_bundles():
    """결과 파일에 넣는 카드 2장: 예시 추출 결과(핵심 항목 검수 완료) + 상태가 섞인 카드."""
    checked = bundle(statuses=verified(EXAMPLE["fields"], KEY_FIELDS) | verified(EXAMPLE["fields"], ["deliverables"], "auto_verified"))
    mixed_fields = example_fields()
    mixed_fields["title"]["value"] = "시험용 상태 혼합 공모전"
    mixed_fields["campus"] = field({"any": False, "allowed": ["자연"]}, flag="needs_check")
    mixed_fields["grades"] = field(None, flag="missing", quote=None)
    mixed_fields["team"] = field({"min": 2, "max": 5, "individual_ok": False}, flag="conflict")
    mixed_fields["stages"] = field([{"name": "신청", "label": "신청", "start": {"date": None, "time": None, "year_inferred": False},
                                     "end": {"date": "2026-11-20", "time": None, "year_inferred": True}},
                                    {"name": "발표", "label": "결과 발표", "start": {"date": None, "time": None, "year_inferred": False},
                                     "end": {"date": "2026-12-01", "time": None, "year_inferred": True}}])
    mixed_fields["apply"] = field({"start": {"date": "2026-11-01", "time": None, "year_inferred": False},
                                   "end": {"date": "2026-11-20", "time": None, "year_inferred": True}})
    mixed = CardBundle(card_id="mixed-status-2026", notices=[
        {"source_id": "mju_general", "url": "https://www.mju.ac.kr/bbs/mjukr/141/1/artclView.do", "title": "공지 1", "published_at": "2026-10-30",
         "files": EXAMPLE_FILES},
        {"source_id": "mju_innovation", "url": "https://innov.mju.ac.kr/bbs/innovation/56/2/artclView.do", "title": "공지 2", "published_at": "2026-11-02"},
    ], extraction=extraction(mixed_fields),
        deadline_history=[{"field": "apply_end", "old": "2026-11-13", "new": "2026-11-20",
                           "old_source": "https://www.mju.ac.kr/bbs/mjukr/141/1/artclView.do",
                           "new_source": "https://innov.mju.ac.kr/bbs/innovation/56/2/artclView.do", "checked_at": "2026-11-05"}],
        meta=dict(META))
    return [checked, mixed]


FIXTURE_BOARDS = [
    {"id": "mju-ctl", "name": "명지대학교 교수학습센터", "list_url": None},
    {"id": "mju-general", "name": "명지대학교 일반공지", "list_url": None},
    {"id": "mju-innovation", "name": "대학혁신지원사업", "list_url": "https://innov.mju.ac.kr/innovation/1/subview.do"},
]


class ExporterContractTests(unittest.TestCase):
    def test_example_output_becomes_contract_card(self):
        card = build_card(bundle())
        self.assertEqual(set(card), CONTRACT_CARD_KEYS)
        self.assertEqual(list(card["fields"]), CARD_FIELDS)
        for name, item in card["fields"].items():
            self.assertLessEqual(set(item), CONTRACT_FIELD_KEYS, name)
        self.assertEqual(card["title"], EXAMPLE["fields"]["title"]["value"])
        self.assertEqual(card["scope"], "교내")
        self.assertEqual(card["sources"], [{"board_id": "mju-ctl", "url": NOTICE["url"], "title": NOTICE["title"], "posted_at": "2026-09-29", "last_seen_at": None}])
        self.assertEqual(card["fields"]["apply"]["value"], {"start": "2026-10-01", "end": "2026-10-21 15:00", "time_given": True})
        self.assertEqual(card["fields"]["apply"]["status"], "ai", "검증 전 항목은 ai")
        evidence = card["fields"]["apply"]["evidence"][0]
        self.assertEqual(evidence["source"], NOTICE["url"], "근거의 공지 번호는 원문 주소로")
        self.assertIn(evidence.get("file"), EXAMPLE_FILES)
        self.assertEqual(card["fields"]["contact"]["value"], {"dept": "교수학습센터", "email": "", "phone": ""}, "검수 전 학교 메일은 비움")
        self.assertEqual(card["meta"], META)

    def test_flag_and_verification_become_status(self):
        fields = example_fields()
        fields["grades"] = field(None, flag="missing", quote=None)
        fields["team"] = field({"min": None, "max": 3, "individual_ok": True}, flag="conflict")
        fields["benefits"] = field("상금", flag="needs_check")
        fields["mileage"] = field({"text": "창의 10점", "area": "창의", "points": 10}, quote=None)
        fields["how_to_apply"] = field({"text": "신청", "url": None}, flag="이상한값")
        statuses = verified(fields, ["campus", "team", "mileage"]) | verified(fields, ["enrollment"], "auto_verified")
        statuses["contact"] = "needs_check"
        statuses["deliverables"] = "human_verified"  # 검증한 값이 없는 옛 형식
        card = build_card(with_fields(fields, statuses=statuses))
        status = {name: item["status"] for name, item in card["fields"].items()}
        self.assertEqual(status["grades"], "missing")
        self.assertIsNone(card["fields"]["grades"]["value"])
        self.assertEqual(status["team"], "needs_check", "conflict는 검수 결과보다 앞선다")
        self.assertEqual(status["benefits"], "needs_check")
        self.assertEqual(status["how_to_apply"], "needs_check", "모르는 flag")
        self.assertEqual(status["campus"], "human_verified")
        self.assertEqual(status["enrollment"], "auto_verified")
        self.assertEqual(status["contact"], "needs_check")
        self.assertEqual(status["mileage"], "ai", "근거 없는 검수 완료는 계약 위반이라 ai로")
        self.assertEqual(status["deliverables"], "ai", "검증한 값이 없으면 검증 결과를 쓰지 않는다")

    def test_verification_is_bound_to_checked_value(self):
        fields = example_fields()
        statuses = verified(fields, ["campus"])
        fields["campus"]["value"] = {"any": False, "allowed": ["인문"]}  # 재추출로 값이 바뀜
        item = build_card(with_fields(fields, statuses=statuses))["fields"]["campus"]
        self.assertEqual(item["status"], "ai")
        self.assertIn("검증한 값과 지금 값이 달라", item["note"])
        failed = {"apply": {"status": "needs_check", "note": "근거 대조 실패", "evidence_failed": True}}
        item = build_card(with_fields(example_fields(), statuses=failed))["fields"]["apply"]
        self.assertEqual((item["status"], item["evidence"]), ("needs_check", []), "원문에 없는 근거는 공개하지 않는다")
        self.assertIn("근거 대조 실패", item["note"])

    def test_extraction_warnings_block_auto_verification(self):
        fields = example_fields()
        statuses = verified(fields, ["campus"], "auto_verified") | verified(fields, ["grades"])
        card = build_card(bundle(extraction=extraction(fields, warnings=["공지에 지시문"]), statuses=statuses))
        self.assertEqual(card["fields"]["campus"]["status"], "ai", "주입 의심이면 자동 검증 통과를 쓰지 않는다")
        self.assertIn("지시", card["fields"]["campus"]["note"])
        self.assertEqual(card["fields"]["grades"]["status"], "human_verified", "사람이 본 것은 그대로")
        self.assertNotIn("공지에 지시문", json.dumps(card, ensure_ascii=False), "경고 문구 자체는 공개하지 않는다")

    def test_conflict_without_value_is_needs_check_not_missing(self):
        item = one_field("team", field(None, flag="conflict", alternatives=[{"value": 1}, {"value": 2}]))
        self.assertEqual((item["value"], item["status"]), (None, "needs_check"))
        self.assertIn("공지마다 값이 다름", item["note"])

    def test_dates_are_never_filled_in(self):
        item = one_field("apply", field({"start": {"date": "2026-10-01", "time": None, "year_inferred": False},
                                         "end": {"date": "2026-10-23", "time": None, "year_inferred": True}}))
        self.assertEqual(item["value"], {"start": "2026-10-01", "end": "2026-10-23", "time_given": False})
        self.assertIn("연도", item["note"])
        for bad in [{"date": "2026-02-30", "time": None}, {"date": "10월 23일", "time": None}]:
            item = one_field("apply", field({"start": None, "end": {**bad, "year_inferred": False}}))
            self.assertEqual((item["status"], item["value"]), ("needs_check", None), bad)
        item = one_field("apply", field({"start": None, "end": {"date": "2026-10-23", "time": "24:00", "year_inferred": False}}))
        self.assertEqual(item["value"], {"start": None, "end": "2026-10-23", "time_given": False}, "틀린 시각은 버리고 날짜만")
        self.assertEqual(item["status"], "needs_check")
        item = one_field("apply", field({"start": {"date": "2026-10-25", "time": None, "year_inferred": False},
                                         "end": {"date": "2026-10-23", "time": None, "year_inferred": False}}))
        self.assertEqual(item["status"], "needs_check", "마감이 시작보다 빠름")

    def test_value_shapes(self):
        self.assertEqual(one_field("campus", field({"any": True, "allowed": []}))["value"], "any")
        item = one_field("grades", field({"any": False, "allowed": [True, 2]}))
        self.assertEqual((item["value"], item["status"]), (None, "needs_check"), "학년에 참·거짓은 받지 않는다")
        stage = lambda name, day, time=None, label="": {"name": name, "label": label, "start": None,
                                                       "end": {"date": day, "time": time, "year_inferred": False}}
        item = one_field("stages", field([stage("신청", "2026-10-21", "15:00"), stage("잡담", "2026-10-22")]))
        self.assertEqual(item["value"], [{"name": "신청", "end": "2026-10-21 15:00", "time_given": True}])
        self.assertEqual(item["status"], "needs_check", "모르는 단계 이름이 있었다")
        item = one_field("stages", field([stage("신청", None), stage("기타", "2026-11-01", label="오리엔테이션")]))
        self.assertEqual(item["status"], "ai", "날짜가 아예 없는 단계(상시 접수)는 정상")
        self.assertIn("오리엔테이션", item["note"], "기타 단계는 원문 표현을 남긴다")

    def test_single_date_stages(self):
        def stage(name, start=None, end=None, time=None):
            day = lambda value: {"date": value, "time": time, "year_inferred": False}
            return {"name": name, "label": name, "start": day(start), "end": day(end)}
        item = one_field("stages", field([stage("신청", "2026-10-01", "2026-10-21"), stage("본선", start="2026-11-26", time="13:30"),
                                          stage("발표", end="2026-11-30")]))
        self.assertEqual(item["value"], [{"name": "신청", "end": "2026-10-21", "time_given": False},
                                         {"name": "본선", "end": "2026-11-26 13:30", "time_given": True},
                                         {"name": "발표", "end": "2026-11-30", "time_given": False}])
        self.assertEqual(item["status"], "ai", "날짜 하나인 일정 단계는 그 날짜로 쓴다 (값을 바꾼 것이 아님)")
        self.assertIn("날짜가 하나인 본선 일정", item["note"])
        item = one_field("stages", field([stage("신청", start="2026-10-01"), stage("시상", start="2026-12-01")]))
        self.assertEqual(item["value"], [{"name": "시상", "end": "2026-12-01", "time_given": False}],
                         "신청 시작일을 마감으로 쓰면 D-day가 틀린다")
        self.assertEqual(item["status"], "needs_check")
        self.assertIn("마감일 없는 신청 단계를 뺌", item["note"])

    def test_broken_stage_end_is_not_replaced_by_start(self):
        stage = {"name": "본선", "label": "본선", "start": {"date": "2026-11-26", "time": None, "year_inferred": False},
                 "end": {"date": "2026-02-30", "time": None, "year_inferred": False}}
        item = one_field("stages", field([stage]))
        self.assertIsNone(item["value"], "끝 날짜가 틀리면 시작일로 대신하지 않는다 (기간이 하루로 보임)")
        self.assertEqual(item["status"], "needs_check")
        self.assertNotIn("날짜가 하나인", item.get("note", ""))

    def test_contact_email_needs_final_human_status(self):
        raw = field({"dept": "센터", "email": "ctl@mju.ac.kr", "phone": None})
        checked = {"status": "human_verified", "checked_value": copy.deepcopy(raw["value"])}
        for case, changes in {"근거 대조 실패": {"evidence_failed": True}, "근거 없음": {}}.items():
            given = copy.deepcopy(raw) if changes else dict(raw, evidence=[])
            item = one_field("contact", given, statuses={"contact": {**checked, **changes}})
            self.assertEqual(item["value"]["email"], "", case)
            self.assertNotEqual(item["status"], "human_verified", case)

    def test_email_inside_links_is_rejected_even_for_school(self):
        for url in ["https://docs.google.com/forms/d/x/viewform?entry.1=hong123@mju.ac.kr",
                    "https://forms.gle/x?e=hong123%40mju.ac.kr", "https://forms.gle/x?e=hong123%2540mju.ac.kr",
                    "https://forms.gle/x?e=hong123+%40+mju.ac.kr", "https://forms.gle/x?e=hong%2540gmail.com",
                    "https://forms.gle/x?p=010%252D1234%252D5678", "https://forms.gle/x?e=a%2525252540b.com"]:
            item = one_field("how_to_apply", field({"text": "신청", "url": url}))
            self.assertIsNone(item["value"]["url"], url)
        item = one_field("how_to_apply", field({"text": "신청 https://forms.gle/x?e=hong123%40mju.ac.kr", "url": None}))
        self.assertNotIn("hong123", item["value"]["text"])

    def test_silent_value_changes_lower_status(self):
        fields = example_fields()
        cases = {
            "team": field({"min": "3", "max": 5, "individual_ok": "예"}),
            "deliverables": field({"tags": ["영상", "모르는태그"], "text": "영상"}),
            "enrollment": field({"allowed": ["재학생", 3, "가" * 40], "excluded": []}),
            "mileage": field({"text": "창의", "area": 3, "points": -1}),
            "contact": field({"dept": "센터", "email": 5, "phone": None}),
            "benefits": field("상금 " * 200),
        }
        fields.update(cases)
        card = build_card(with_fields(fields, statuses=verified(fields, list(cases))))
        for name in cases:
            self.assertEqual(card["fields"][name]["status"], "needs_check", name)
            self.assertIn("내보내기 검사", card["fields"][name]["note"], name)
        self.assertEqual(card["fields"]["team"]["value"], {"min": None, "max": 5, "individual_ok": None})
        self.assertEqual(card["fields"]["deliverables"]["value"]["tags"], ["영상"])

    def test_exporter_reason_survives_long_ai_note(self):
        item = one_field("team", field({"min": 0, "max": 3, "individual_ok": True}, note="가" * 299))
        self.assertTrue(item["note"].startswith("내보내기 검사: 인원 수 형식"))
        self.assertLessEqual(len(item["note"]), MAX_TEXT_CHARS)

    def test_sources_latest_first_and_evidence_maps_notice_number(self):
        notices = [dict(NOTICE, published_at="2026-09-29"), {"source_id": "mju_general", "url": "https://www.mju.ac.kr/x", "title": "연장", "published_at": "2026-10-07"}]
        fields = example_fields()
        fields["apply"]["evidence"] = [{"quote": "연장", "notice": 2, "location": "본문", "file": "지어낸.pdf"},
                                       {"quote": "없는 공지", "notice": 9, "location": "본문", "file": None}]
        card = build_card(with_fields(fields, notices=notices, statuses=verified(fields, ["apply"])))
        self.assertEqual([source["posted_at"] for source in card["sources"]], ["2026-10-07", "2026-09-29"])
        self.assertEqual(card["fields"]["apply"]["evidence"], [{"quote": "연장", "location": "본문", "source": "https://www.mju.ac.kr/x"}],
                         "번호가 틀린 근거는 버리고, 공지에 없는 파일 이름은 뺀다")
        self.assertEqual(card["fields"]["apply"]["status"], "needs_check")
        with self.assertRaises(CardExportError, msg="입력 순서가 어긋나면 근거 번호를 믿을 수 없다"):
            build_card(with_fields(fields, notices=list(reversed(notices))))

    def test_html_escape_from_ai_input_is_undone(self):
        fields = example_fields()
        fields["title"]["value"] = "R&amp;D &lt;아이디어&gt; 공모전"
        fields["benefits"] = field("상금 &quot;100만 원&quot;", quote="상금 &quot;100만 원&quot;")
        card = build_card(with_fields(fields))
        self.assertEqual(card["title"], "R&D <아이디어> 공모전")
        self.assertEqual(card["fields"]["benefits"]["value"], '상금 "100만 원"')
        self.assertEqual(card["fields"]["benefits"]["evidence"][0]["quote"], '상금 "100만 원"')


class ExporterSafetyTests(unittest.TestCase):
    def test_only_contract_keys_are_exported(self):
        fields = example_fields()
        fields["apply"]["secret"] = "내부 값"
        fields["raw_body"] = {"value": "본문 전체", "flag": "found", "evidence": [], "note": None, "alternatives": []}
        card = build_card(bundle(extraction=extraction(fields, warnings=["경고"])))
        text = json.dumps(card, ensure_ascii=False)
        for leaked in ["내부 값", "raw_body", "alternatives", "경고", "year_inferred"]:
            self.assertNotIn(leaked, text)
        self.assertNotIn("kind", card["fields"])

    def test_body_cannot_be_rebuilt_from_many_quotes(self):
        body = "".join(f"본문문장{index:04d}번째입니다 " for index in range(600))  # 약 9천 자
        pieces = [body[start:start + MAX_QUOTE_CHARS] for start in range(0, len(body), MAX_QUOTE_CHARS)]
        fields = example_fields()
        for number, name in enumerate(CARD_FIELDS):
            fields[name]["evidence"] = [{"quote": piece, "notice": 1, "location": "본문", "file": None}
                                        for piece in pieces[number * MAX_EVIDENCE:(number + 1) * MAX_EVIDENCE]]
        fields["benefits"] = field(body, quote=body)
        card = build_card(with_fields(fields, notices=[dict(NOTICE, body_text=body)]))
        quotes = {item["quote"] for value in card["fields"].values() for item in value["evidence"]}
        self.assertLessEqual(sum(map(len, quotes)), MAX_CARD_QUOTE_CHARS)
        self.assertTrue(all(len(value["evidence"]) <= MAX_EVIDENCE for value in card["fields"].values()))
        self.assertLessEqual(len(card["fields"]["benefits"]["value"]), MAX_TEXT_CHARS)
        exported = json.dumps(card, ensure_ascii=False)
        shared = sum(len(piece) for piece in pieces if piece[:40] in exported)
        self.assertLess(shared, len(body) / 4, "본문 대부분이 결과에 실리지 않는다")
        self.assertNotIn("body_text", exported)

    def test_links_follow_site_allowlist(self):
        for url in ["javascript:alert(1)", "https://user:pw@forms.gle/x", "data:text/html,hi", "http://mju.ac.kr/x",
                    "https://evil.example/apply", "https://mju.ac.kr.evil.example/x", "https://forms.gle/" + "a" * 600]:
            item = one_field("how_to_apply", field({"text": "신청", "url": url}))
            self.assertIsNone(item["value"]["url"], url)
            self.assertEqual(item["status"], "needs_check", url)
        for url in ["https://forms.gle/abc", "https://docs.google.com/forms/d/x", "https://ctl.mju.ac.kr/a",
                    "https://docs.google.com/forms/d/x/viewform?usp=pp_url&entry.1=%EA%B3%B5%EB%AA%A8+%EC%A0%84"]:
            self.assertEqual(one_field("how_to_apply", field({"text": "신청", "url": url}))["value"]["url"], url)
        with self.assertRaises(CardExportError):
            build_card(bundle(notices=[dict(NOTICE, url="https://evil.example/notice")]))

    def test_urls_in_text_are_removed(self):
        item = one_field("how_to_apply", field({"text": "신청은 https://evil.example/x 또는 www.phish.kr 에서, 안내 https://ctl.mju.ac.kr/a", "url": None}))
        self.assertEqual(item["value"]["text"], "신청은 (주소 생략) 또는 (주소 생략) 에서, 안내 https://ctl.mju.ac.kr/a")
        self.assertEqual(item["status"], "needs_check")

    def test_personal_contacts_are_masked(self):
        item = one_field("contact", field({"dept": "센터", "email": "student@gmail.com", "phone": "010-1234-5678"}))
        self.assertEqual(item["value"], {"dept": "센터", "email": "", "phone": ""})
        self.assertEqual(item["status"], "needs_check")
        item = checked_contact(field({"dept": "센터", "email": "ctl@mju.ac.kr", "phone": "02-300-1234(내선 5)"}))
        self.assertEqual(item["value"], {"dept": "센터", "email": "ctl@mju.ac.kr", "phone": "02-300-1234(내선 5)"})
        quote = "문의: 홍길동 010 9876 5432, hong@naver.com, 학번 60201234 / 센터 ctl@mju.ac.kr"
        item = one_field("benefits", field("상금", quote=quote))
        exported = json.dumps(item, ensure_ascii=False)
        for leaked in ["9876", "naver", "60201234"]:
            self.assertNotIn(leaked, exported)
        self.assertNotIn("ctl@mju.ac.kr", exported, "학생도 학교 메일을 써서 글 속 학교 메일도 가린다")
        self.assertIn("(학교 이메일 생략)", exported)
        self.assertEqual(item["status"], "needs_check")
        item = one_field("deliverables", field({"tags": ["영상"], "text": "제출 문의 +82 10-1111-2222"}))
        self.assertNotIn("1111", item["value"]["text"])

    def test_invisible_and_control_characters_are_removed(self):
        fields = example_fields()
        fields["title"]["value"] = "대회‮명​\u0007\n이름"
        fields["benefits"] = field("상금⁦ 100 만 원", quote="근거‭ 문장")
        card = build_card(with_fields(fields))
        self.assertEqual(card["title"], "대회명 이름")
        self.assertEqual(card["fields"]["benefits"]["value"], "상금 100 만 원")
        self.assertEqual(card["fields"]["benefits"]["evidence"][0]["quote"], "근거 문장")

    def test_phone_number_variants_are_masked(self):
        dash = chr(0x2013)
        soft_hyphen = chr(0xAD)
        fullwidth = "".join(chr(ord(char) + 0xFEE0) if char.isdigit() else char for char in "010-1234-5678")
        variants = ["(010) 1234-5678", "010 - 1234 - 5678", "010~1234~5678", "010/1234/5678", fullwidth,
                    f"010{dash}1234{dash}5678", f"010{soft_hyphen}1234{soft_hyphen}5678", "+82-(0)10-1234-5678", "+82 10 1234 5678",
                    "01012345678"]
        for phone in variants:
            item = one_field("contact", field({"dept": "센터", "email": None, "phone": phone}))
            self.assertEqual(item["value"]["phone"], "", phone)
            self.assertEqual(item["status"], "needs_check", phone)
            item = one_field("benefits", field("상금", quote=f"문의 {phone} (조교)"))
            self.assertNotIn("1234", json.dumps(item, ensure_ascii=False), phone)
            self.assertNotIn(chr(0xFF11) + chr(0xFF12), json.dumps(item, ensure_ascii=False), phone)
        item = one_field("contact", field({"dept": "센터", "email": None, "phone": "0 10 1234 5678"}))
        self.assertEqual(item["value"]["phone"], "", "글 가림이 놓쳐도 숫자만 모아 휴대폰인지 본다")
        self.assertIn("휴대폰 번호라 뺌", item["note"])
        for phone in ["02-300-1234", "031-330-6114", "T. 02-300-1234", "02-300-1234 (평일 09:00~18:00)", "1588-1234 / 02-300-1234"]:
            self.assertEqual(one_field("contact", field({"dept": "센터", "email": None, "phone": phone}))["value"]["phone"], phone, phone)
        for text in ["2026.10.21~2026.11.01", "상금 1,000,000원", "10월 1일(수) 10:00", "2010.12.31"]:
            item = one_field("benefits", field(text))
            self.assertEqual((item["value"], item["status"]), (text, "ai"), "정상 값은 가리지 않는다")

    def test_numbers_split_by_other_spaces_are_masked(self):
        for space in [chr(0xA0), chr(0x2009), chr(0x202F), chr(0x3000), "&nbsp;"]:
            phone = f"010{space}1234{space}5678"
            fields = example_fields()
            fields["title"]["value"] = f"공모전 문의 {phone}"
            fields["benefits"] = field(f"상금, 문의 {phone}", quote=f"문의 {phone}")
            card = build_card(with_fields(fields))
            exported = json.dumps(card, ensure_ascii=False)
            self.assertNotIn("5678", exported, repr(space))
            self.assertEqual(card["fields"]["benefits"]["status"], "needs_check", repr(space))

    def test_very_long_ai_output_stays_fast(self):
        import time
        slow_domain = ("a-" * 3000 + ".") * 3
        slow_url = "https://" + "a" * 20000
        fields = example_fields()
        for name in fields:
            if isinstance(fields[name], dict):
                fields[name]["note"] = slow_url
                fields[name]["evidence"] = [{"quote": slow_domain, "notice": 1, "location": "본문", "file": None}] * 1000
        fields["benefits"] = field(slow_url + " " + slow_domain)
        started = time.perf_counter()
        card = build_card(with_fields(fields))
        self.assertLess(time.perf_counter() - started, 5, "비정상적으로 긴 AI 출력에도 카드 하나가 몇 초 안에 끝난다")
        self.assertLessEqual(len(card["fields"]["benefits"]["value"]), MAX_TEXT_CHARS)

    def test_more_separators_and_school_email_local_part(self):
        for dash in [chr(0x3161), chr(0x2500), chr(0x30FC)]:
            item = one_field("benefits", field("상금", quote=f"문의 010{dash}1234{dash}5678"))
            self.assertNotIn("5678", json.dumps(item, ensure_ascii=False), repr(dash))
        item = one_field("benefits", field("상금", quote=f"제출 hong@gmail{chr(0x3002)}com"))
        self.assertNotIn("hong", json.dumps(item, ensure_ascii=False))
        for email in ["01012345678@mju.ac.kr", "60201234@mju.ac.kr"]:
            item = one_field("contact", field({"dept": "센터", "email": email, "phone": None}))
            self.assertEqual((item["value"]["email"], item["status"]), ("", "needs_check"), email)

    def test_school_email_needs_human_check(self):
        raw = field({"dept": "센터", "email": "hong123@mju.ac.kr", "phone": None})
        item = one_field("contact", raw)
        self.assertEqual((item["value"]["email"], item["status"]), ("", "ai"), "검수 전에는 비우되 값이 틀린 것은 아니다")
        self.assertIn("검수하기 전이라 비움", item["note"])
        auto = one_field("contact", raw, statuses={"contact": {"status": "auto_verified", "checked_value": copy.deepcopy(raw["value"])}})
        self.assertEqual(auto["value"]["email"], "", "자동 검증도 학생 메일과 구분하지 못한다")
        self.assertEqual(checked_contact(raw)["value"]["email"], "hong123@mju.ac.kr")
        item = one_field("how_to_apply", field({"text": "팀장 hong123@mju.ac.kr 로 제출", "url": None}))
        self.assertEqual((item["value"]["text"], item["status"]), ("팀장 (학교 이메일 생략) 로 제출", "ai"))

    def test_email_variants_are_masked(self):
        fullwidth_at = chr(0xFF20)
        for email in [f"hong{fullwidth_at}gmail.com", "hong [at] gmail.com", "hong @ gmail.com", "홍길동@gmail.com", "hong@www.gmail.com"]:
            item = one_field("benefits", field("상금", quote=f"제출 {email} 로"))
            exported = json.dumps(item, ensure_ascii=False)
            self.assertNotIn("gmail", exported, email)
            self.assertNotIn("hong", exported, email)
        item = checked_contact(field({"dept": "센터", "email": f"ctl{fullwidth_at}mju.ac.kr", "phone": None}))
        self.assertEqual(item["value"]["email"], "ctl@mju.ac.kr")

    def test_more_invisible_characters_are_removed(self):
        hidden = "".join(chr(code) for code in [0xAD, 0x3164, 0x115F, 0xFE0F, 0x180E, 0xE0041, 0xE0042, 0xFFF9, 0x2065, 0xD800])
        fields = example_fields()
        fields["title"]["value"] = f"대회{hidden}명"
        fields["benefits"] = field(f"상금{hidden}", quote=f"근거{hidden}")
        card = build_card(with_fields(fields))
        self.assertEqual(card["title"], "대회명")
        self.assertEqual(card["fields"]["benefits"]["value"], "상금")
        self.assertEqual(card["fields"]["benefits"]["evidence"][0]["quote"], "근거")
        with tempfile.TemporaryDirectory() as folder:
            write_cards_json(Path(folder) / "cards.json", card)  # 짝 없는 서로게이트가 남으면 여기서 실패

    def test_bare_domains_and_url_edges(self):
        item = one_field("how_to_apply", field({"text": "오픈채팅 open.kakao.com/o/abc, bit.ly/x, naver.me/y 참고", "url": None}))
        self.assertEqual(item["value"]["text"], "오픈채팅 (주소 생략), (주소 생략), (주소 생략) 참고")
        text = "https://www.mju.ac.kr은 학교, http://ctl.mju.ac.kr/a 참고 (mju.ac.kr)."
        item = one_field("how_to_apply", field({"text": text, "url": None}))
        self.assertEqual(item["value"]["text"], text)
        self.assertEqual(item["status"], "ai", "학교 주소는 글에 남기고 원문 확인 필요로 내리지 않는다")
        backslash = chr(92)
        for url in [f"https://evil.example{backslash}.mju.ac.kr/login", "https://ctl.mju.ac.kr:99999/a", "https://ctl.mju.ac.kr/폼",
                    "https://docs.google.com/forms/d/x/viewform?entry.1=010-1234-5678", "https://forms.gle/x?e=hong%40gmail.com"]:
            self.assertIsNone(one_field("how_to_apply", field({"text": "신청", "url": url}))["value"]["url"], url)
        item = one_field("how_to_apply", field({"text": f"신청 https://evil.example{backslash}.mju.ac.kr/login", "url": None}))
        self.assertIn("(주소 생략)", item["value"]["text"])

    def test_ai_notes_have_card_budget(self):
        fields = example_fields()
        for name in CARD_FIELDS:
            fields[name]["note"] = f"{name} " + "가" * 250
        warnings = []
        card = build_card(with_fields(fields), warnings)
        self.assertLessEqual(sum(len(item.get("note", "")) for item in card["fields"].values()), 1000 + 300)
        self.assertTrue(any("note 합계" in message for message in warnings))

    def test_contact_evidence_only_after_human_check(self):
        fields = example_fields()
        fields["contact"] = field({"dept": "센터", "email": "ctl@mju.ac.kr", "phone": None}, quote="문의: 센터 홍길동 조교")
        card = build_card(with_fields(fields))
        self.assertEqual(card["fields"]["contact"]["evidence"], [], "검수 전 문의처 근거(담당자 이름)는 싣지 않는다")
        card = build_card(with_fields(fields, statuses=verified(fields, ["contact"])))
        self.assertEqual(card["fields"]["contact"]["status"], "human_verified")
        self.assertEqual(len(card["fields"]["contact"]["evidence"]), 1)

    def test_top_level_flags_files_and_stage_labels_are_reported(self):
        fields = example_fields()
        fields["organizer"]["flag"] = "conflict"
        fields["title"]["value"] = "공모전 문의 010-1234-5678"
        stage = lambda label, day: {"name": "예선", "label": label, "start": None, "end": {"date": day, "time": None, "year_inferred": False}}
        fields["stages"] = field([stage("예선 1차", "2026-10-01"), stage("예선 2차", "2026-10-08")])
        warnings = []
        card = build_card(with_fields(fields, notices=[dict(NOTICE, files=[])]), warnings)
        self.assertNotIn("5678", card["title"])
        self.assertTrue(any("organizer" in message for message in warnings))
        self.assertTrue(any("title에서" in message for message in warnings))
        self.assertTrue(any("파일 이름" in message for message in warnings))
        self.assertIn("예선 1차, 예선 2차", card["fields"]["stages"]["note"])

    def test_generated_at_and_source_id_follow_site_format(self):
        for bad in ["20261105T090000+09:00", "2026-11-05 09:00:00+09:00"]:
            with self.assertRaises(ValueError, msg=bad):
                build_cards_json([bundle()], [], bad)
        build_cards_json([bundle()], [], "2026-11-05T00:00:00Z")
        for source_id in ["MJU", "mju ctl", "x" * 41, "mju\nctl"]:
            with self.assertRaises(CardExportError, msg=source_id):
                build_card(bundle(notices=[dict(NOTICE, source_id=source_id)]))

    def test_last_seen_is_preserved_and_public_source_ids_are_hyphenated(self):
        last_seen = "2026-11-05T12:00:00+00:00"
        card = build_card(bundle(notices=[dict(NOTICE, last_seen_at=last_seen)]))
        self.assertEqual(card["sources"][0]["last_seen_at"], last_seen)
        self.assertEqual(card["sources"][0]["board_id"], "mju-ctl")
        for invalid in ("2026-11-05", "2026-11-05T12:00:00", "not-a-time", 2026):
            with self.subTest(invalid=invalid), self.assertRaises(CardExportError):
                build_card(bundle(notices=[dict(NOTICE, last_seen_at=invalid)]))
        sources = [type("Source", (), {"id": source_id, "name": "시험", "list_url": ""})
                   for source_id in ("mju_ctl", "mju-ctl")]
        with self.assertRaisesRegex(ValueError, "중복"):
            boards_from_sources(sources)

    def test_source_order_uses_original_ids_before_public_normalization(self):
        originals = [dict(NOTICE, source_id="mju-b"), dict(NOTICE, source_id="mju_a", url=NOTICE["url"] + "?second=1")]
        card = build_card(bundle(notices=originals))
        self.assertEqual([source["board_id"] for source in card["sources"]], ["mju-a", "mju-b"])
        with self.assertRaisesRegex(CardExportError, "순서"):
            build_card(bundle(notices=list(reversed(originals))))

    def test_sources_are_sorted_and_published_by_korean_date(self):
        originals = [dict(NOTICE, source_id="mju_z", published_at="2026-11-01T22:00:00+09:00"),
                     dict(NOTICE, source_id="mju_a", published_at="2026-11-01T17:00:00+00:00",
                          url=NOTICE["url"] + "?utc=1")]
        card = build_card(bundle(notices=originals))
        self.assertEqual([(source["board_id"], source["posted_at"]) for source in card["sources"]],
                         [("mju-a", "2026-11-02"), ("mju-z", "2026-11-01")])
        with self.assertRaisesRegex(CardExportError, "순서"):
            build_card(bundle(notices=list(reversed(originals))))

    def test_naive_source_time_is_already_korean_date(self):
        for posted in ("2026-11-01", "2026-11-01T22:00:00", "2026-11-01T22:00:00+09:00"):
            with self.subTest(posted=posted):
                card = build_card(bundle(notices=[dict(NOTICE, published_at=posted)]))
                self.assertEqual(card["sources"][0]["posted_at"], "2026-11-01")

    def test_broken_bundles_are_rejected_one_by_one(self):
        no_title = example_fields()
        no_title["title"]["value"] = ""
        bad_scope = example_fields()
        bad_scope["scope"]["value"] = "모름"
        string_scope = example_fields()
        string_scope["scope"] = "교내"
        bundles = [bundle(), bundle(card_id="Bad Id"), with_fields(no_title, card_id="no-title"),
                   with_fields(bad_scope, card_id="bad-scope"), bundle(card_id="no-date", notices=[dict(NOTICE, published_at=None)]),
                   with_fields(string_scope, card_id="string-scope"), bundle(card_id="none-notice", notices=[None]),
                   bundle(card_id="list-status", statuses=[]), bundle(card_id="none-meta", meta=None), bundle()]
        data, report = build_cards_json(bundles, [], GENERATED_AT)
        self.assertEqual([card["id"] for card in data["cards"]], ["ai-literacy-2026", "none-meta"])
        self.assertEqual([item["id"] for item in report["rejected"]],
                         ["'Bad Id'", "no-title", "bad-scope", "no-date", "string-scope", "none-notice", "list-status", "ai-literacy-2026"])
        self.assertIn("두 번", report["rejected"][-1]["reason"])
        self.assertEqual(data["cards"][1]["meta"], dict.fromkeys(META))

    def test_unexpected_error_rejects_only_that_card(self):
        real_field = __import__("exporter.cards", fromlist=["_field"])._field

        def broken_for_one(name, raw, notices, *rest):
            if notices[0]["url"].endswith("/broken"):
                raise TypeError("예상 못 한 모양")
            return real_field(name, raw, notices, *rest)

        with mock.patch("exporter.cards._field", side_effect=broken_for_one):
            data, report = build_cards_json([bundle(card_id="broken", notices=[dict(NOTICE, url="https://ctl.mju.ac.kr/broken")]), bundle()],
                                            [], GENERATED_AT)
        self.assertEqual([card["id"] for card in data["cards"]], ["ai-literacy-2026"])
        self.assertEqual(report["rejected"], [{"id": "broken", "reason": "처리 중 오류 (TypeError)"}], "AI 값은 이유에 넣지 않는다")

    def test_blocked_cards_are_left_out(self):
        bundles = [bundle(), bundle(card_id="keep-me"), bundle(card_id="keep-me"), bundle(card_id="block-me"), bundle(card_id="block-me")]
        data, report = build_cards_json(bundles, [], GENERATED_AT, blocked={"ai-literacy-2026", "block-me", "gone-card"})
        self.assertEqual([card["id"] for card in data["cards"]], ["keep-me"], "차단한 카드는 만들지 않는다")
        self.assertEqual([item["id"] for item in report["blocked"]], ["ai-literacy-2026", "block-me"], "같은 id가 두 번 와도 한 번")
        self.assertEqual([item["id"] for item in report["warnings"]], ["gone-card"],
                         "목록에 있는데 이번 묶음에 없는 id는 조용히 넘어가지 않는다")
        self.assertEqual([item["id"] for item in report["rejected"]], ["keep-me"], "차단과 상관없는 중복은 그대로 뺀다")
        self.assertNotIn("block-me", json.dumps(data, ensure_ascii=False), "차단한 카드는 cards.json 어디에도 없다")
        data, report = build_cards_json([bundle()], [], GENERATED_AT)
        self.assertEqual((len(data["cards"]), report["blocked"]), (1, []), "목록이 없으면 그대로")
        not_contest = bundle(card_id="not-contest", extraction={"is_contest": {"value": False, "reason": ""}})
        data, report = build_cards_json([not_contest, bundle()], [], GENERATED_AT,
                                        blocked={"not-contest"})
        self.assertEqual(([item["id"] for item in report["blocked"]], report["skipped"]), (["not-contest"], []),
                         "건너뛸 묶음이었어도 차단으로만 남는다")

    def test_non_contest_and_result_notices_are_skipped(self):
        results = example_fields()
        results["kind"]["value"] = "결과"
        bundles = [bundle(card_id="not-contest", extraction={"is_contest": {"value": False, "reason": "장학"}, "warnings": []}),
                   with_fields(results, card_id="result-notice"), bundle()]
        data, report = build_cards_json(bundles, [], GENERATED_AT)
        self.assertEqual([card["id"] for card in data["cards"]], ["ai-literacy-2026"])
        self.assertEqual([item["id"] for item in report["skipped"]], ["not-contest", "result-notice"])
        self.assertEqual(report["rejected"], [])
        with self.assertRaises(CardSkipped):
            build_card(bundles[0])

    def test_broken_deadline_history_and_meta_are_dropped_with_warning(self):
        history = [{"field": "apply_end", "old": None, "new": "11월 20일", "checked_at": "2026-11-05"},
                   {"field": "apply_end", "old": "2026-10-21 15:00", "new": "2026-10-23", "old_source": "https://evil.example/x",
                    "new_source": NOTICE["url"], "checked_at": "2026-11-05"},
                   {"field": "title", "old": "a", "new": "b", "checked_at": "2026-11-05"}]
        data, report = build_cards_json([bundle(deadline_history=history, meta=dict(META, checked_at="어제", extract_model=3))], [], GENERATED_AT)
        card = data["cards"][0]
        self.assertEqual(card["deadline_history"], [{"field": "apply_end", "old": "2026-10-21 15:00", "new": "2026-10-23", "old_source": None,
                                                     "new_source": NOTICE["url"], "checked_at": "2026-11-05"}])
        self.assertEqual((card["meta"]["checked_at"], card["meta"]["extract_model"]), (None, None))
        self.assertEqual(len(report["warnings"]), 4)

    def test_generated_at_must_be_iso_with_timezone(self):
        for bad in ["어제", "2026-11-05", None, "2026-13-01T00:00:00+09:00"]:
            with self.assertRaises(ValueError, msg=bad):
                build_cards_json([bundle()], [], bad)

    def test_boards_from_sources_and_atomic_write(self):
        sources = [Source(id="mju_general", name="일반공지", adapter="k2web", rss_url="https://www.mju.ac.kr/rss", allowed_hosts=[],
                          title_selector="", body_selector="", attachment_selector=""),
                   Source(id="mju_innovation", name="혁신", adapter="k2web", rss_url="", allowed_hosts=[], title_selector="",
                          body_selector="", attachment_selector="", list_url="https://innov.mju.ac.kr/list")]
        self.assertEqual(boards_from_sources(sources), [{"id": "mju-general", "name": "일반공지", "list_url": None},
                                                        {"id": "mju-innovation", "name": "혁신", "list_url": "https://innov.mju.ac.kr/list"}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "site" / "data" / "cards.json"
            write_cards_json(path, {"cards": []})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"cards": []})
            with self.assertRaises(ValueError):
                write_cards_json(path, {"points": float("inf")})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"cards": []}, "실패하면 기존 파일 그대로")
            self.assertEqual(list(path.parent.glob("*.tmp")), [], "임시 파일이 남지 않는다")

    def test_infinite_points_are_rejected(self):
        item = one_field("mileage", field({"text": "창의", "area": "창의", "points": float("inf")}))
        self.assertIsNone(item["value"]["points"])
        self.assertEqual(item["status"], "needs_check")


class ExporterCliTests(unittest.TestCase):
    def run_cli(self, bundles_text, out_path, extra=()):
        with tempfile.TemporaryDirectory() as folder:
            bundles_path = Path(folder) / "bundles.json"
            bundles_path.write_text(bundles_text, encoding="utf-8")
            if "--blocked" not in extra:  # 저장소의 실제 차단 목록이 바뀌어도 시험이 흔들리지 않게 빈 목록으로
                empty = Path(folder) / "blocked.toml"
                empty.write_text("blocked = []\n", encoding="utf-8")
                extra = [*extra, "--blocked", str(empty)]
            argv = ["exporter", "--bundles", str(bundles_path), "--out", str(out_path), *extra]
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                code = cli_main()
            return code, stderr.getvalue()

    def bundle_json(self, items, generated_at=GENERATED_AT, stamped=True):
        # CLI는 검증기가 표시를 붙인 묶음만 받는다. 표시 규칙은 아래 test_cli_requires_verifier_stamp에서 본다
        raw = [asdict(item) for item in items]
        return json.dumps({"generated_at": generated_at, "bundles": [stamp_bundle(item) for item in raw] if stamped else raw},
                          ensure_ascii=False)

    def test_cli_requires_verifier_stamp(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            out.write_text('{"keep": true}', encoding="utf-8")
            # 검증기를 빠뜨린 실행: 하나도 표시가 없으면 --allow-empty여도 비우지 않는다
            for extra in ((), ("--allow-empty",)):
                code, errors = self.run_cli(self.bundle_json(fixture_bundles(), stamped=False), out, list(extra))
                self.assertEqual(code, 2, extra)
                self.assertIn("검증기를 거친 묶음이 하나도 없어", errors)
                self.assertEqual(out.read_text(encoding="utf-8"), '{"keep": true}')
            # 검증 뒤에 바뀐 묶음만 빼고 나머지는 내보낸다 (statuses를 손으로 고침, 병합으로 내용이 바뀜, 표시 위조)
            first, second = fixture_bundles()[:2]
            tampered_cases = {
                "statuses": lambda item: item["statuses"].update(apply={"status": "human_verified"}),
                "extraction": lambda item: item["extraction"].update(warnings=["병합으로 바뀜"]),
                "stamp_by": lambda item: item["verified"].update(by="someone"),
                "stamp_missing": lambda item: item.pop("verified"),
                "stamp_not_object": lambda item: item.update(verified="ok"),
            }
            for name, tamper in tampered_cases.items():
                raw = json.loads(self.bundle_json([first, second]))
                tamper(raw["bundles"][1])
                code, errors = self.run_cli(json.dumps(raw, ensure_ascii=False), out)
                self.assertEqual(code, 1, name)
                self.assertIn(f"[뺌] {second.card_id}: 검증기를 거치지 않았거나", errors, name)
                self.assertEqual([card["id"] for card in json.loads(out.read_text(encoding="utf-8"))["cards"]], [first.card_id], name)
            # 형식이 틀린 id는 로그에 그대로 찍지 않는다
            raw = json.loads(self.bundle_json([first]))
            raw["bundles"].append({"card_id": "가짜\n[내보냄] 카드 99장", "notices": []})
            code, errors = self.run_cli(json.dumps(raw, ensure_ascii=False), out)
            self.assertEqual(code, 1)
            self.assertIn("[뺌] (형식이 틀린 id, 2번째 묶음)", errors)
            self.assertNotIn("카드 99장", errors)
            # 키 순서·들여쓰기가 달라도 내용이 같으면 통과
            pretty = json.dumps(json.loads(self.bundle_json([first])), ensure_ascii=False, indent=4, sort_keys=True)
            self.assertEqual(self.run_cli(pretty, out)[0], 0)

    def test_cli_writes_cards_and_reports_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            code, _ = self.run_cli(self.bundle_json(fixture_bundles()), out)
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(data["cards"]), 2)
            self.assertTrue(all(board["id"] and board["name"] for board in data["boards"]), "게시판은 sources.toml에서")
            code, errors = self.run_cli(self.bundle_json([*fixture_bundles(), bundle(card_id="Bad Id")]), out)
            self.assertEqual(code, 1)
            self.assertIn("Bad Id", errors)
            self.assertEqual(len(json.loads(out.read_text(encoding="utf-8"))["cards"]), 2, "나머지 카드는 내보낸다")
            not_contest = bundle(card_id="not-contest", extraction={"is_contest": {"value": False, "reason": ""}})
            code, errors = self.run_cli(self.bundle_json([*fixture_bundles(), not_contest]), out)
            self.assertEqual(code, 0, "대회가 아닌 결과를 건너뛴 것은 문제가 아님")
            self.assertIn("[건너뜀] not-contest", errors)

    def test_cli_applies_the_blocklist(self):
        with tempfile.TemporaryDirectory() as folder:
            out, blocked = Path(folder) / "cards.json", Path(folder) / "blocked.toml"
            card_ids = [item.card_id for item in fixture_bundles()]
            blocked.write_text(f'[[blocked]]\ncard_id = "{card_ids[0]}"\nreason = "비밀 이유 문구"\nsince = 2026-11-06\n', encoding="utf-8")
            code, errors = self.run_cli(self.bundle_json(fixture_bundles()), out, ["--blocked", str(blocked)])
            self.assertEqual(code, 0, "차단은 의도한 것이라 경고가 아니다")
            self.assertEqual([card["id"] for card in json.loads(out.read_text(encoding="utf-8"))["cards"]], card_ids[1:])
            self.assertIn(f"[차단] {card_ids[0]}", errors)
            self.assertIn("차단 1", errors)
            self.assertNotIn("비밀 이유 문구", errors + out.read_text(encoding="utf-8"), "이유는 기록·cards.json 어디에도 없다")
            blocked.write_text('[[blocked]]\ncard_id = "gone-card"\nreason = "사라진 카드"\nsince = 2026-11-06\n', encoding="utf-8")
            code, errors = self.run_cli(self.bundle_json(fixture_bundles()), out, ["--blocked", str(blocked)])
            self.assertEqual(code, 1, "목록에만 있는 id는 확인할 일")
            self.assertIn("[확인] gone-card", errors)
            before = out.read_text(encoding="utf-8")
            blocked.write_text("".join(f'[[blocked]]\ncard_id = "{card_id}"\nreason = "모두"\nsince = 2026-11-06\n'
                                       for card_id in card_ids), encoding="utf-8")
            code, errors = self.run_cli(self.bundle_json(fixture_bundles()), out, ["--blocked", str(blocked)])
            self.assertEqual(code, 2, "모두 차단하면 0장이라 기존 규칙대로 멈춘다 (README에 적음)")
            self.assertEqual(out.read_text(encoding="utf-8"), before)

    def test_cli_stops_when_the_blocklist_is_unreadable(self):
        with tempfile.TemporaryDirectory() as folder:
            out, blocked = Path(folder) / "cards.json", Path(folder) / "blocked.toml"
            out.write_text('{"keep": true}', encoding="utf-8")
            for content in ("blocked = [", '[[blocked]]\ncard_id = "Bad Id"\nreason = "x"\nsince = 2026-11-06\n', None):
                if content is None:
                    blocked.unlink()
                else:
                    blocked.write_text(content, encoding="utf-8")
                code, errors = self.run_cli(self.bundle_json(fixture_bundles()), out, ["--blocked", str(blocked)])
                self.assertEqual(code, 2, f"차단 없이 내보내지 않는다: {content!r}")
                self.assertIn("수동 차단 목록을 읽지 못해", errors)
                self.assertEqual(out.read_text(encoding="utf-8"), '{"keep": true}')

    def test_cli_default_blocklist_is_the_repository_file(self):
        with tempfile.TemporaryDirectory() as folder:
            bundles_path, out = Path(folder) / "bundles.json", Path(folder) / "cards.json"
            bundles_path.write_text(self.bundle_json(fixture_bundles()), encoding="utf-8")
            argv = ["exporter", "--bundles", str(bundles_path), "--out", str(out)]
            with mock.patch.object(sys, "argv", argv), mock.patch("exporter.__main__.load_blocked", return_value={}) as loaded, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                cli_main()
            self.assertEqual(loaded.call_args.args[0].resolve(), (ROOT / "pipeline" / "config" / "blocked_cards.toml").resolve(),
                             "기본값이 저장소의 차단 목록 (매일 공개는 --blocked를 넘기지 않는다)")

    def test_cli_null_keys_use_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            raw = json.loads(self.bundle_json([bundle()], stamped=False))
            raw["bundles"][0].update(statuses=None, meta=None, deadline_history=None)
            raw["bundles"][0] = stamp_bundle(raw["bundles"][0])
            code, _ = self.run_cli(json.dumps(raw, ensure_ascii=False), out)
            self.assertEqual(code, 0)

    def test_cli_keeps_existing_file_when_nothing_safe_to_write(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            out.write_text('{"keep": true}', encoding="utf-8")
            cases = ["{깨진 JSON", '{"generated_at": "x"}', '{"generated_at": "x", "bundles": [1]}',
                     self.bundle_json([bundle()], generated_at="어제"), self.bundle_json([]), self.bundle_json([bundle(card_id="Bad Id")])]
            for broken in cases:
                code, errors = self.run_cli(broken, out)
                self.assertEqual(code, 2, broken[:60])
                self.assertIn("바꾸지 않음", errors)
                self.assertEqual(out.read_text(encoding="utf-8"), '{"keep": true}', broken[:60])
            code, _ = self.run_cli(self.bundle_json([]), out, ["--allow-empty"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["cards"], [])

    def test_cli_unexpected_error_is_exit_2(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            with mock.patch("exporter.__main__.boards_from_sources", side_effect=RecursionError("깊음")):
                code, errors = self.run_cli(self.bundle_json([bundle()]), out)
            self.assertEqual(code, 2, "예상 못 한 오류가 1(배포 가능)이 되지 않는다")
            self.assertFalse(out.exists())

    def test_cli_write_failure_is_exit_3(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cards.json"
            with mock.patch("exporter.__main__.write_cards_json", side_effect=PermissionError("사용 중")):
                code, errors = self.run_cli(self.bundle_json([bundle()]), out)
            self.assertEqual(code, 3)
            self.assertIn("쓰기 실패", errors)


class ExporterFixtureTests(unittest.TestCase):
    def test_fixture_matches_exporter_output(self):
        data, report = build_cards_json(fixture_bundles(), FIXTURE_BOARDS, GENERATED_AT)
        self.assertEqual(report, {"rejected": [], "skipped": [], "warnings": [], "blocked": []})
        if os.environ.get("UPDATE_FIXTURES") == "1":
            write_cards_json(FIXTURE, data)
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), data, "결과가 바뀌었으면 UPDATE_FIXTURES=1로 다시 만든다")


if __name__ == "__main__":
    unittest.main()
