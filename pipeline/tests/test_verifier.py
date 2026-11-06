"""검증기(#15) 시험: 근거 대조, 상식 검사, statuses 병합, 실행 진입점, 내보내기와 이어 붙이기."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from exporter.cards import CardBundle, build_card
from verifier import __main__ as verifier_main
from verifier.__main__ import main as cli_main
from verifier.__main__ import read_originals, verify_bundles
from verifier.checks import (NEGATION_PREFIXES, TRAILING_NEGATIONS, check_extraction, contains_quote, evidence_fingerprint,
                             merge_statuses, normalize)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "prompts/extract_v0.2/example_output.json").read_text(encoding="utf-8"))
NBSP, ZERO_WIDTH, HANGUL_DOT, HANGUL_FILLER = chr(0x00A0), chr(0x200B), chr(0x318D), chr(0x3164)
URL = "https://ctl.mju.ac.kr/bbs/ctl/792/235972/artclView.do"
URL2 = "https://ctl.mju.ac.kr/bbs/ctl/792/236000/artclView.do"
FILES = sorted({item["file"] for value in EXAMPLE["fields"].values() for item in value["evidence"] if item.get("file")})
NOTICE = {"source_id": "mju_ctl", "url": URL, "title": "2026학년도 AI 리터러시 경진대회 개최 안내",
          "published_at": "2026-09-29T10:00:00+09:00", "files": FILES}


def body_quotes(fields):
    return [item["quote"] for value in fields.values() for item in value["evidence"] if item["location"] == "본문"]


def original(attachments=(), url=URL, title=NOTICE["title"], extra=""):
    """예시 출력의 본문 근거를 모두 담은 원문. 줄바꿈·NBSP를 섞어 실제 공지처럼."""
    body = f"\n{NBSP} ".join(body_quotes(EXAMPLE["fields"]))
    return {"url": url, "title": title, "body_text": "안내\n" + body + "\n" + extra + "\n끝", "attachments": list(attachments)}


def check(fields, originals=None, notices=None):
    extraction = {"is_contest": {"value": True, "reason": ""}, "fields": fields, "warnings": []}
    return check_extraction(extraction, notices or [NOTICE], originals if originals is not None else [original()])


def fields_copy():
    return copy.deepcopy(EXAMPLE["fields"])


def failed(checks):
    return {name for name, item in checks.items() if item.failures}


class NormalizeTests(unittest.TestCase):
    def test_only_spelling_variants_are_folded(self):
        self.assertEqual(normalize("기획서" + HANGUL_DOT + "제안서 ∼ 10.21"), normalize("기획서·제안서~10.21"))
        self.assertEqual(normalize("ＡＩ" + NBSP + "리터러시" + ZERO_WIDTH + HANGUL_FILLER + " &amp; 대회"), normalize("ai리터러시&대회"))
        self.assertEqual(normalize("“최대 3명”"), normalize('"최대3명"'))
        self.assertNotEqual(normalize("최대 3명"), normalize("최대 4명"))

    def test_numbers_on_separate_lines_do_not_join(self):
        source = normalize("마감: 10월 2\n1. 참가 자격")
        self.assertFalse(contains_quote(source, normalize("마감: 10월 21")))
        self.assertTrue(contains_quote(source, normalize("마감 : 10월 2")))

    def test_number_boundaries(self):
        self.assertFalse(contains_quote(normalize("팀당 13명까지"), normalize("3명까지")), "3명이 13명에 맞으면 안 된다")
        self.assertFalse(contains_quote(normalize("10.21까지"), normalize("10.2")))
        self.assertTrue(contains_quote(normalize("팀당 13명, 또는 3명까지"), normalize("3명까지")), "뒤에 맞는 곳이 있으면 통과")
        self.assertFalse(contains_quote(normalize("평점평균 3.5 이상"), normalize("평점평균 3")), "소수점 뒤 숫자")
        self.assertFalse(contains_quote(normalize("상금 100,000원"), normalize("상금 100")), "자릿점 뒤 숫자")
        self.assertFalse(contains_quote(normalize("1.5명 기준"), normalize("5명 기준")), "소수점 앞 숫자")
        self.assertFalse(contains_quote(normalize("상금 100, 200, 300"), normalize("상금 100")),
                         "정규화가 쉼표 뒤 공백을 지워 자릿점과 구분하지 못한다: 실패 쪽으로 틀린다 (안전)")

    def test_quotes_do_not_start_inside_a_negated_word(self):
        self.assertFalse(contains_quote(normalize("개인 참가는 불가능합니다."), normalize("가능합니다.")))
        self.assertFalse(contains_quote(normalize("신청서 미제출 시"), normalize("제출 시")))
        self.assertTrue(contains_quote(normalize("불가능합니다. 팀은 가능합니다."), normalize("가능합니다.")), "다른 곳에 맞으면 통과")
        self.assertFalse(contains_quote(normalize("비대상자는 신청할 수 없음"), normalize("대상자는 신청할 수 없음")))
        for source, quote in (("비전공자 대상", "전공자 대상"), ("미등록자 제외", "등록자 제외"), ("요건 미충족 시 선정 취소", "충족 시 선정 취소"),
                              ("미이수자는 참가 불가", "이수자는 참가 불가"), ("비대면 진행", "대면 진행"), ("참가 횟수 무제한", "제한"),
                              ("부적격자 제외", "적격자 제외"), ("미성년자 참가 불가", "성년자 참가 불가"),
                              ("중복지원불가능", "가능"), ("개인참가불가능", "가능합니다"), ("서류미제출시 탈락", "제출시 탈락")):
            self.assertFalse(contains_quote(normalize(source), normalize(quote)), f"{source} / {quote}: 부정 글자 바로 뒤 (#49)")
        self.assertFalse(contains_quote("개인참가불가능합니다", "가능합니다"), "낱말 경계를 모르는 글은 모든 한글 인용을 본다")
        for source, quote in (("참가비 지원 대상은 전 학년", "지원 대상은 전 학년"), ("업무 협약 기관 소속", "협약 기관 소속"),
                              ("의미 있는 활동을 한 학생", "있는 활동을 한 학생"), ("등록비 납부 후 신청", "납부 후 신청"),
                              ("참가비\n접수 기간: 10.21~10.25", "접수 기간: 10.21~10.25"), ("참가비 신청 방법", "신청 방법"),
                              ("신청비 등록 방법", "등록 방법")):
            self.assertTrue(contains_quote(normalize(source), normalize(quote)), f"{quote}: 앞 낱말 끝이 불·비·미·무여도 정상 인용 (#49)")

    def test_negation_right_after_the_quote(self):
        for source, quote in (("개인 참가 불가", "개인 참가"), ("제출 서류 없음", "제출 서류"), ("장학금은 지급하지 않음", "장학금은 지급하지"),
                              ("휴학생은 신청 대상 아님", "휴학생은 신청 대상"), ("재학생 참여 제외", "재학생 참여"),
                              ("증빙 서류 미제출 시 탈락", "증빙 서류"), ("대리 신청은 안 됨", "대리 신청은"), ("온라인 접수 불가능", "온라인 접수"),
                              ("중복 신청 금지", "중복 신청"), ("추가 모집은 하지 않습니다", "추가 모집은 하지"), ("현장 접수불가", "현장 접수")):
            self.assertFalse(contains_quote(normalize(source), normalize(quote)), f"{source} / {quote}: 인용 뒤 부정 (#49)")
        for source, quote in (("개인 참가 불가. 팀 참가 가능", "팀 참가 가능"), ("신청 기간: 10.21~10.25", "신청 기간"),
                              ("참가 가능합니다. 불가 사유는 아래", "참가 가능합니다."), ("온라인 신청 안내문", "온라인 신청"),
                              ("서류 제출 기한 엄수", "서류 제출"), ("상금 100만원 지급", "상금 100만원")):
            self.assertTrue(contains_quote(normalize(source), normalize(quote)), f"{source} / {quote}: 뒤가 부정이 아님")

    def test_every_trailing_negation_and_particle(self):
        for negation in TRAILING_NEGATIONS:
            for between in (" ", "", "는 ", "이 "):
                source = f"개인 참가{between}{negation}"
                self.assertFalse(contains_quote(normalize(source), normalize("개인 참가")), f"{source}: 목록의 모든 부정 (#49)")
        for prefix in NEGATION_PREFIXES:
            self.assertFalse(contains_quote(normalize(f"증빙 서류 {prefix}대상"), normalize("증빙 서류")), f"{prefix}대상")
        for source, quote in (("부득이한 사유로 불가피한 경우 연장", "부득이한 사유로"),
                              ("교육과 연구는 불가분의 관계", "교육과 연구는"), ("TOEIC 700 불가", "TOEIC 700")):
            self.assertTrue(contains_quote(normalize(source), normalize(quote)), f"{source}: 부정이 아님 / 한글로 끝나지 않음")

    def test_trailing_negation_stays_on_the_same_line(self):
        for source, quote in (("대상: 재학생\n제외 대상: 휴학생", "대상: 재학생"), ("참가 자격\r\n없음 처리 기준은 아래", "참가 자격"),
                              ("신청 방법\n\n  불가 사유", "신청 방법")):
            self.assertTrue(contains_quote(normalize(source), normalize(quote)), f"{source!r}: 다음 줄은 인용의 뒷말이 아니다")
        self.assertFalse(contains_quote(normalize("대상: 재학생 제외\n휴학생"), normalize("대상: 재학생")), "같은 줄이면 막는다")
        self.assertFalse(contains_quote("대상:재학생제외대상", "대상:재학생"), "줄 정보를 모르는 글은 막는 쪽")

    def test_trailing_negation_tries_the_next_match(self):
        self.assertTrue(contains_quote(normalize("개인 참가 불가. 팀은 개인 참가 가능"), normalize("개인 참가")), "두 번째 위치가 정상")
        self.assertFalse(contains_quote(normalize("개인 참가 불가. 팀은 개인 참가 없음"), normalize("개인 참가")))

    def test_fingerprint_sorts_mixed_file_values(self):
        evidence = [{"quote": "본문 문장입니다", "notice": 1, "location": "본문", "file": None},
                    {"quote": "포스터 문장입니다", "notice": 1, "location": "본문", "file": "poster.png"}]
        self.assertEqual(len(evidence_fingerprint(evidence)), 2, "file이 None과 문자열로 섞여도 정렬된다 (#49)")
        self.assertEqual(evidence_fingerprint(evidence), evidence_fingerprint(list(reversed(evidence))), "순서와 상관없이 같다")


class EvidenceTests(unittest.TestCase):
    def test_example_passes_and_images_need_a_person(self):
        checks = check(fields_copy())
        self.assertEqual(failed(checks), set())
        self.assertEqual(checks["apply"].result, "unverifiable", "이미지 근거는 코드로 대조하지 못한다")
        self.assertEqual(checks["deliverables"].result, "unverifiable", "글자를 뽑지 않은 첨부")
        self.assertEqual(checks["campus"].result, "pass")

    def test_fabricated_or_changed_quote_fails(self):
        fields = fields_copy()
        fields["team"]["evidence"][0]["quote"] = "팀은 최대 5명까지 구성할 수 있습니다"
        fields["campus"]["evidence"][1]["quote"] = fields["campus"]["evidence"][1]["quote"] + " (자연캠퍼스 제외)"
        fields["grades"]["evidence"][0]["quote"] = "x" * 501
        checks = check(fields)
        self.assertEqual(failed(checks), {"team", "campus", "grades"})
        self.assertEqual(checks["grades"].evidence_failures, ["근거가 500자를 넘음"])
        self.assertEqual(checks["scope"].result, "pass", "다른 항목은 그대로")

    def test_short_quotes_need_a_person_unless_absent(self):
        fields = fields_copy()
        fields["grades"]["evidence"] = [{"quote": "안 내", "notice": 1, "location": "본문", "file": None}]
        fields["team"]["evidence"] = [{"quote": "없음", "notice": 1, "location": "본문", "file": None}]
        checks = check(fields)
        self.assertEqual(checks["grades"].result, "unverifiable", "원문에 있는 짧은 인용은 지어낸 것이 아니라 사람이 본다")
        self.assertEqual(checks["team"].result, "fail", "원문에 없으면 짧아도 실패")

    def test_hold_flags_with_evidence_are_checked_too(self):
        fields = fields_copy()
        fields["team"]["flag"] = "needs_check"
        fields["team"]["evidence"][0]["quote"] = "공지에 없는 문장입니다"
        fields["benefits"]["flag"] = "conflict"
        fields["benefits"]["value"] = None
        fields["mileage"] = {"value": None, "flag": "missing", "evidence": [], "note": None, "alternatives": []}
        fields["how_to_apply"]["flag"] = "needs_check"
        fields["how_to_apply"]["evidence"] = []
        checks = check(fields)
        self.assertEqual(checks["team"].result, "fail", "확인 필요 항목도 내보내기가 근거를 공개하므로 대조한다")
        self.assertEqual(checks["benefits"].result, "pass", "값 없는 충돌 항목도 근거는 대조")
        self.assertNotIn("mileage", checks)
        self.assertNotIn("how_to_apply", checks, "근거 없는 확인 필요 항목은 내보내기가 그대로 보여 준다")

    def test_evidence_shape_problems_fail(self):
        fields = fields_copy()
        fields["grades"]["evidence"] = []
        fields["team"]["evidence"][0]["notice"] = 2
        fields["scope"]["evidence"][0]["location"] = "어딘가"
        fields["kind"]["evidence"] = [dict(fields["kind"]["evidence"][0])] * 51
        checks = check(fields)
        self.assertEqual(checks["grades"].failures, ["근거 없음"])
        self.assertEqual(checks["team"].failures, ["근거의 공지 번호가 틀림"])
        self.assertEqual(checks["scope"].failures, ["모르는 근거 위치"])
        self.assertEqual(checks["kind"].failures, ["근거가 50개를 넘음"])
        fields = fields_copy()
        fields["campus"]["evidence"][0]["file"] = ["해시할 수 없는 값"]
        self.assertEqual(check(fields)["campus"].result, "pass", "본문 근거의 이상한 file 값 때문에 카드 전체가 멈추지 않는다")

    def test_image_or_attachment_location_cannot_skip_the_check(self):
        fields = fields_copy()
        fields["team"]["evidence"] = [{"quote": "지어낸 문장", "notice": 1, "location": "이미지", "file": "없는 포스터.png"}]
        fields["campus"]["evidence"] = [{"quote": "지어낸 문장", "notice": 1, "location": "첨부", "file": None}]
        checks = check(fields)
        for name in ("team", "campus"):
            self.assertEqual(checks[name].evidence_failures, ["근거 파일 이름이 공지 1의 파일 목록에 없음"], name)

    def test_missing_original_is_not_a_pass(self):
        checks = check(fields_copy(), originals=[None])
        self.assertEqual(checks["campus"].failures, ["공지 1의 원문을 찾지 못해 대조 못 함"],
                         "원문이 없으면 대조하지 못한 것이고, 대조 못 한 근거는 공개하지 않는다")
        self.assertEqual(checks["apply"].result, "unverifiable", "이미지 근거는 원문 없이도 파일 이름만 본다")

    def test_attachment_text_is_checked_when_extracted(self):
        fields = fields_copy()
        name = next(item["file"] for item in fields["deliverables"]["evidence"] if item["location"] == "첨부")
        quote = next(item["quote"] for item in fields["deliverables"]["evidence"] if item["location"] == "첨부")
        self.assertEqual(check(fields, [original(attachments=[{"name": name, "text": "붙임\n" + quote}])])["deliverables"].result, "pass")
        self.assertEqual(check(fields, [original(attachments=[{"name": name, "text": "다른 내용"}])])["deliverables"].result, "fail")

    def test_second_notice_and_title_location(self):
        fields = fields_copy()
        fields["title"]["evidence"] = [{"quote": "AI 리터러시 경진대회 연장", "notice": 2, "location": "제목", "file": None}]
        notices = [NOTICE, dict(NOTICE, url=URL2, published_at="2026-10-20T10:00:00+09:00")]
        originals = [original(), original(url=URL2, title="2026 AI 리터러시 경진대회 연장 안내")]
        self.assertEqual(check(fields, originals, notices)["title"].result, "pass")
        originals[1] = original(url=URL2, title="다른 제목")
        self.assertEqual(check(fields, originals, notices)["title"].result, "fail")


class DateTests(unittest.TestCase):
    def with_apply_end(self, day, stage_day=None, kind=None, published_at=None):
        fields = fields_copy()
        fields["apply"]["value"]["end"]["date"] = day
        for stage in fields["stages"]["value"]:
            if stage["name"] == "신청":
                stage["end"]["date"] = stage_day or day
        if kind is not None:
            fields["kind"]["value"] = kind
        notice = dict(NOTICE, published_at=published_at) if published_at is not None else NOTICE
        return check(fields, notices=[notice])

    def test_dates_that_do_not_fit_the_notice(self):
        self.assertEqual(self.with_apply_end("2027-01-10")["apply"].failures, [], "이듬해 일정은 허용")
        self.assertIn("2028년은 게시 연도(2026년)와 동떨어짐", self.with_apply_end("2028-10-21")["apply"].failures)
        self.assertIn("신청 마감이 첫 공지 게시일보다 이름", self.with_apply_end("2026-09-01")["apply"].failures)
        checks = self.with_apply_end("2026-10-21", stage_day="2026-10-28")
        for name in ("apply", "stages"):
            self.assertIn('신청 기간 마감과 "신청" 단계 마감이 다름', checks[name].failures)

    def test_posting_time_zone_and_missing_dates(self):
        self.assertEqual(self.with_apply_end("2026-09-29", published_at="2026-09-28T23:00:00Z")["apply"].failures, [],
                         "UTC 28일 23시는 한국 29일")
        self.assertIn("신청 마감이 첫 공지 게시일보다 이름",
                      self.with_apply_end("2026-09-28", published_at="2026-09-28T16:00:00Z")["apply"].failures)
        checks = self.with_apply_end("2028-10-21", published_at="")
        self.assertEqual(checks["apply"].failures, [])
        self.assertIn("공지 게시일을 읽지 못해 날짜 검사를 못 함", checks["apply"].unverifiable, "조용히 건너뛰지 않는다")

    def test_result_notices_skip_date_checks_and_odd_kinds_do_not_crash(self):
        self.assertEqual(self.with_apply_end("2026-09-01", kind="결과")["apply"].failures, [])
        self.assertIn("신청 마감이 첫 공지 게시일보다 이름", self.with_apply_end("2026-09-01", kind=["결과"])["apply"].failures)


class MergeTests(unittest.TestCase):
    def merged(self, fields, existing):
        return merge_statuses(existing, {"fields": fields}, check(fields))

    def test_evidence_failure_overrides_people_but_sanity_does_not(self):
        fields = fields_copy()
        fields["team"]["evidence"][0]["quote"] = "지어낸 문장입니다"
        fields["apply"]["value"]["end"]["date"] = fields["apply"]["value"]["start"]["date"] = "2028-10-21"
        human = {name: {"status": "human_verified", "checked_value": copy.deepcopy(fields[name]["value"]),
                        "checked_evidence": evidence_fingerprint(fields[name]["evidence"])} for name in ("team", "apply")}
        statuses = self.merged(fields, human)
        self.assertEqual((statuses["team"]["status"], statuses["team"]["evidence_failed"]), ("needs_check", True),
                         "사람이 본 것은 그때의 근거다. 근거가 바뀌었으면 근거를 공개하지 않는다")
        self.assertEqual(statuses["apply"]["status"], "human_verified", "상식 검사만 걸리면 사람이 본 값을 믿는다")

    def test_people_are_trusted_only_for_what_they_saw(self):
        fields = fields_copy()
        record = {name: {"status": "human_verified", "checked_value": copy.deepcopy(fields[name]["value"])}
                  for name in ("apply", "contact", "campus")}
        statuses = self.merged(fields, record)
        self.assertEqual(statuses["campus"]["status"], "human_verified", "근거를 모두 코드로 대조했으면 기록이 없어도 믿는다")
        for name in ("apply", "contact"):
            self.assertEqual((statuses[name]["status"], statuses[name]["evidence_failed"]), ("needs_check", True),
                             "이미지 근거·문의처는 사람이 본 근거 기록이 있어야 한다")
            self.assertEqual(statuses[name]["previous"], record[name])
        seen = {"status": "human_verified", "checked_value": copy.deepcopy(fields["apply"]["value"]),
                "checked_evidence": evidence_fingerprint(fields["apply"]["evidence"])}
        self.assertEqual(self.merged(fields, {"apply": seen})["apply"], seen, "본 근거가 같으면 믿는다")
        changed = copy.deepcopy(fields)
        changed["apply"]["evidence"][0]["quote"] = "포스터에서 읽었다는 다른 문장"
        self.assertEqual(self.merged(changed, {"apply": seen})["apply"]["note"], "사람이 검수한 근거와 지금 근거가 다름")

    def test_rerun_restores_people_after_a_fix(self):
        fields = fields_copy()
        seen = {"status": "human_verified", "checked_value": copy.deepcopy(fields["team"]["value"]),
                "checked_evidence": evidence_fingerprint(fields["team"]["evidence"])}
        broken = copy.deepcopy(fields)
        broken["team"]["evidence"][0]["quote"] = "지어낸 문장입니다"
        first = self.merged(broken, {"team": seen})
        self.assertEqual(first["team"]["status"], "needs_check")
        self.assertEqual(self.merged(fields, first), {"team": seen}, "출력을 다시 넣어도 사람 검수가 사라지지 않는다")

    def test_failures_go_down_and_nothing_goes_up(self):
        fields = fields_copy()
        fields["grades"]["evidence"][0]["quote"] = "지어낸 문장입니다"
        existing = {"grades": {"status": "human_verified", "checked_value": {"any": False, "allowed": [1]}}, "campus": "needs_check"}
        statuses = self.merged(fields, existing)
        self.assertEqual(statuses["grades"]["checked_value"], fields["grades"]["value"])
        self.assertEqual(statuses["campus"], "needs_check", "통과한 항목은 기존 값을 올리지 않는다")
        self.assertNotIn("scope", statuses, "통과해도 auto_verified로 올리지 않는다 (교차 검증 전)")

    def test_rerun_clears_its_own_old_results(self):
        fields = fields_copy()
        broken = copy.deepcopy(fields)
        broken["team"]["evidence"][0]["quote"] = "지어낸 문장입니다"
        first = self.merged(broken, {"campus": "needs_check"})
        self.assertEqual(first["team"]["by"], "verifier")
        self.assertEqual(self.merged(fields, first), {"campus": "needs_check"}, "근거를 고치면 전에 내린 표시가 사라진다")
        self.assertEqual(self.merged(broken, first), first, "같은 입력이면 같은 결과")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.folder / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def bundles(self, fields, card_id="ai-literacy-2026"):
        return {"generated_at": "2026-11-05T09:00:00+09:00",
                "bundles": [{"card_id": card_id, "notices": [NOTICE], "statuses": {}, "meta": {"keep": 1},
                             "extraction": {"is_contest": {"value": True, "reason": ""}, "fields": fields, "warnings": []}}]}

    def card(self, data):
        item = data["bundles"][0]
        warnings = []
        card = build_card(CardBundle(**{key: item[key] for key in ("card_id", "notices", "extraction", "statuses", "meta")}), warnings)
        return card, warnings

    def test_failed_evidence_never_reaches_the_card(self):
        fields = fields_copy()
        fields["team"]["evidence"][0]["quote"] = "개인 참가 불가, 5명 팀만"
        fields["campus"]["flag"] = "needs_check"
        fields["campus"]["evidence"][0]["quote"] = "지어낸 캠퍼스 문장"
        fields["title"]["evidence"][0]["quote"] = "지어낸 대회 이름"
        data, report = verify_bundles(self.bundles(fields), {URL: original()})
        self.assertEqual(data["bundles"][0]["meta"], {"keep": 1}, "다른 키는 그대로 옮긴다")
        self.assertIn({"card_id": "ai-literacy-2026", "field": "team", "result": "fail",
                       "reasons": ["근거 문장을 공지 1 본문에서 찾지 못함"]}, report)
        card, warnings = self.card(data)
        for name in ("team", "campus"):
            self.assertEqual((card["fields"][name]["status"], card["fields"][name]["evidence"]), ("needs_check", []), name)
        self.assertIn("근거 문장을 원문에서 찾지 못함", card["fields"]["team"]["note"])
        self.assertIn("title의 근거를 원문에서 찾지 못했는데 확정 값처럼 공개됨", warnings)

    def test_malformed_notices_keep_their_numbers(self):
        data = self.bundles(fields_copy())
        data["bundles"][0]["notices"] = ["깨진 공지", NOTICE]
        for value in data["bundles"][0]["extraction"]["fields"].values():
            for item in value["evidence"]:
                item["notice"] = 2
        _, report = verify_bundles(data, {URL: original()})
        self.assertEqual([item for item in report if item["result"] == "fail"], [], "번호 2는 여전히 두 번째 공지를 가리킨다")

    def test_one_broken_card_does_not_stop_the_rest(self):
        data = self.bundles(fields_copy())
        data["bundles"].append(copy.deepcopy(data["bundles"][0]) | {"card_id": "second-2026"})
        real = verifier_main.check_extraction
        with mock.patch.object(verifier_main, "check_extraction",
                               side_effect=[RuntimeError("비밀 내용"), real(data["bundles"][1]["extraction"], [NOTICE], [original()])]):
            verified, report = verify_bundles(data, {URL: original()})
        self.assertEqual(report[0], {"card_id": "ai-literacy-2026", "field": "*", "result": "fail", "reasons": ["검증 중 오류 (RuntimeError)"]})
        self.assertTrue(all(entry["evidence_failed"] for entry in verified["bundles"][0]["statuses"].values()), "검증 없이 통과하지 않는다")
        self.assertEqual(len(verified["bundles"][0]["statuses"]), len(EXAMPLE["fields"]))
        self.assertEqual(verified["bundles"][1]["statuses"], {}, "다른 카드는 정상 검증")

    def run_cli(self, *args):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = cli_main([str(arg) for arg in args])
        return code, stderr.getvalue()

    def test_cli(self):
        fields = fields_copy()
        fabricated = "공지에 없는 비밀 문장 010-1234-5678"
        fields["team"]["evidence"][0]["quote"] = fabricated
        bundles = self.write("bundles.json", self.bundles(fields))
        notices = self.write("notices.json", {"notices": [original()]})
        out = self.folder / "verified.json"
        code, log = self.run_cli("--bundles", bundles, "--notices", notices, "--out", out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["bundles"][0]["statuses"]["team"]["status"], "needs_check")
        self.assertIn("[확인 필요] ai-literacy-2026 team", log)
        self.assertNotIn(fabricated, log, "근거 문장을 로그에 남기지 않는다")
        out.unlink()
        code, _ = self.run_cli("--bundles", self.write("broken.json", {"cards": []}), "--notices", notices, "--out", out)
        self.assertEqual(code, 2)
        self.assertFalse(out.exists(), "읽지 못하면 출력하지 않는다")

    def test_cli_guards(self):
        notices = self.write("notices.json", {"notices": [original()]})
        bad_id = self.write("bad_id.json", self.bundles(fields_copy() | {"grades": dict(fields_copy()["grades"], evidence=[])},
                                                    card_id="가짜\n[검증] 통과"))
        code, log = self.run_cli("--bundles", bad_id, "--notices", notices, "--out", self.folder / "out.json")
        self.assertEqual(code, 0)
        self.assertNotIn("가짜", log, "형식이 틀린 id로 로그를 꾸미지 못한다")
        self.assertIn("(형식이 틀린 id, 1번째 묶음) grades", log)
        site = self.folder / "site"  # 진짜 site/를 건드리지 않게 임시 폴더를 공개 폴더로 본다
        with mock.patch.object(verifier_main, "SITE", site):
            code, log = self.run_cli("--bundles", bad_id, "--notices", notices, "--out", site / "data" / "bundles.json")
        self.assertEqual(code, 2)
        self.assertFalse((site / "data" / "bundles.json").exists(), "공개 폴더에 쓰지 않는다")
        self.assertEqual(verifier_main.SITE, ROOT / "site", "실제 공개 폴더 위치")
        with mock.patch.object(verifier_main, "write_json", side_effect=OSError("디스크")):
            code, log = self.run_cli("--bundles", bad_id, "--notices", notices, "--out", self.folder / "out2.json")
        self.assertEqual(code, 3)
        self.assertIn("grades", log, "쓰기에 실패해도 걸린 항목은 남긴다")

    def test_originals_without_a_single_url_are_not_used(self):
        path = self.write("notices.json", {"notices": [original(), original(), original(url=URL2), {"url": 3}, "글"]})
        self.assertEqual(list(read_originals(path)), [URL2], "어느 판으로 추출했는지 모르면 대조하지 않는다")


if __name__ == "__main__":
    unittest.main()
