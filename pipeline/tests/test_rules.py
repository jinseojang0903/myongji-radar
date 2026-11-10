import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from collector.analysis import analyze_file
from collector.rules import TextView, analyze_notice, extract_dates

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config/rules.toml").read_text(encoding="utf-8"))


def notice(body, title="아이디어 공모전 참가자 모집", **extra):
    return {"source_id": "test", "external_id": "1", "version_id": 5, "title": title,
            "url": "https://example.com/1", "body_text": body, "images": [], "attachments": [], **extra}


class RuleTests(unittest.TestCase):
    def test_fragmented_range_and_no_invented_time(self):
        result = extract_dates(TextView("접수기간\n:\n2026.\n10.\n28.(수) ~ 2026. 11. 8.(일)\n나. 신청방법: 이메일"))["application_period"]
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["value"]["start"]["date"], "2026-10-28")
        self.assertEqual(result["value"]["end"]["date"], "2026-11-08")
        self.assertIsNone(result["value"]["end"]["time"])

    def test_explicit_deadline_and_submission_are_separate(self):
        result = extract_dates(TextView("신청마감: 2026.11.4.(수) 17:00 제출마감: 2026.11.9."))
        self.assertEqual(result["application_period"]["value"]["end"]["time"], "17:00")
        self.assertEqual(result["submission_period"]["value"]["end"]["date"], "2026-11-09")

    def test_missing_year_and_invalid_dates_stay_pending(self):
        for text in ("신청마감: 11월 4일", "신청마감: 2026.2.30.", "신청마감: 2026.11.4. 25:00"):
            with self.subTest(text=text):
                self.assertEqual(extract_dates(TextView(text))["application_period"]["status"], "needs_review")

    def test_date_role_not_guessed(self):
        result = extract_dates(TextView("접수: 2026.11.4."))["application_period"]
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["value"]["end"])

    def test_repeated_deadlines_are_not_overwritten(self):
        result = extract_dates(TextView("신청마감: 2026.11.4. 신청마감: 2026.11.8."))["application_period"]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["value"]["candidates"]), 2)

    def test_time_range_is_not_a_second_calendar_date(self):
        result = extract_dates(TextView("행사일시: 2026.11.26.(목) 13:30-17:00"))["event_period"]
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(len(result["value"]["dates"]), 1)
        self.assertEqual(result["value"]["dates"][0]["end_time"], "17:00")
        self.assertIn("17:00", result["evidence"][0]["quote"])

    def test_table_heading_does_not_capture_next_event_date(self):
        result = extract_dates(TextView("2026.10.28. 17:00 참가 신청서 접수 마감 2026.11.5. 18:00 개회식"))["application_period"]
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["value"], "표 뒤의 다음 행사 날짜를 접수 마감으로 추정하지 않는다")

    def test_academic_year_and_percentage_are_not_grade_or_team_size(self):
        n = analyze_notice(notice("1. 참가자격: 2026학년도 재학생. 팀 구성원의 60% 이상이 공과대학 재학생 2. 접수기간: 2026.11.1.~2026.11.4."), CONFIG)
        self.assertIsNone(n["fields"]["grades"]["value"])
        self.assertIsNone(n["fields"]["team_size"]["value"])

    def test_venue_and_contact_do_not_imply_eligibility(self):
        n = analyze_notice(notice("1. 참가자격: 본교 재학생 2. 행사장소: 명지대학교 자연캠퍼스 3. 문의: 인문캠퍼스 02-000-0000"), CONFIG)
        self.assertEqual(n["fields"]["eligible_campuses"]["status"], "not_stated")
        self.assertEqual(n["fields"]["venue_campuses"]["value"]["mentions"], ["자연캠퍼스"])

    def test_explicit_grade_campus_and_exceptions_keep_evidence(self):
        raw = "1. 참가자격: 인문캠퍼스 2~4학년 재학생. ※ 휴학생 신청 불가. 2. 접수기간: 2026.11.1.~2026.11.4."
        n = analyze_notice(notice(raw), CONFIG)
        self.assertIn("2~4학년", n["fields"]["grades"]["value"]["mentions"])
        self.assertIn("인문캠퍼스", n["fields"]["eligible_campuses"]["value"]["mentions"])
        self.assertEqual(n["fields"]["student_status"]["status"], "needs_review")
        self.assertIn("휴학생신청불가", "".join(n["eligibility_evidence"][0]["quote"].split()))

    def test_result_notice_is_not_recruitment(self):
        n = analyze_notice(notice("참가자 모집 안내는 이전 공지를 참고", title="창업 경진대회 본선 진출팀 안내"), CONFIG)
        self.assertEqual(n["classification"]["notice_kind"]["value"], "results")
        self.assertIsNone(n["classification"]["open_for_application"])

    def test_staff_recruitment_ignores_incidental_competition(self):
        n = analyze_notice(notice("담당업무: 경진대회 운영", title="신규 직원 채용 공고"), CONFIG)
        self.assertEqual(n["classification"]["activity"]["value"], "non_activity")

    def test_image_only_and_missing_conditions_are_not_unrestricted(self):
        n = analyze_notice(notice("", images=[{"url": "https://example.com/a.png"}]), CONFIG)
        self.assertIn("image_only", [w["code"] for w in n["notices"]])
        self.assertIsNone(n["fields"]["grades"]["value"])
        self.assertEqual(n["eligibility_decision"], "not_evaluated")

    def test_evidence_offsets_reference_exact_original(self):
        raw = "1. 참가\n자격: 인문\n캠퍼스 재학생 2. 접수기간: 2026.11.1.~2026.11.4."
        n = analyze_notice(notice(raw), CONFIG)
        for value in n["fields"].values():
            for evidence in value["evidence"]:
                self.assertEqual(raw[evidence["start"]:evidence["end"]], evidence["quote"])

    def test_analysis_does_not_change_input_or_require_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "notices.json"
            path.write_text(json.dumps({"notices": [notice("신청마감: 2026.11.4.")]}), encoding="utf-8")
            before = path.read_bytes()
            summary = analyze_file(path, ROOT / "config/rules.toml", Path(temp))
            self.assertEqual(summary["total"], 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads((Path(temp) / "analysis.json").read_text(encoding="utf-8"))["notices"][0]["source_version_id"], 5)


if __name__ == "__main__":
    unittest.main()
