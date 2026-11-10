"""#10: 그럴듯한 마감을 확정하지 않고 원문과 미해석 시각을 보존하는 회귀 시험."""
import tomllib
import unittest
from pathlib import Path

from collector.rules import TextView, analyze_notice, extract_dates

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "config/rules.toml").read_text(encoding="utf-8"))


def application(text):
    return extract_dates(TextView(text))["application_period"]


class DateRuleRegressions(unittest.TestCase):
    def test_normal_deadlines_ignore_following_prose(self):
        for raw, time in (
            ("신청마감: 2026.11.4.(수) 17:00까지 (총 3시간 교육)", "17:00"),
            ("접수기간: 2026.11.1.~11.4. 17:00 ※ 행사는 14:00 시작", "17:00"),
            ("신청마감: 2026.11.4.(수)까지 한시적 운영", None),
            ("신청마감: 2026.11.4.(수)까지 오전 중 회신", None),
            *[("신청마감: 2026.11.4. 17:00 " + tail, "17:00")
              for tail in ("반드시 제출", "분야별 1명", "초과 제출 불가", "amazing 프로그램")],
            *[("신청기간: 2026.11.1.~11.4. 17:00\n" + tail, "17:00")
              for tail in ("신청기간 내 제출한 서류만 인정", "접수기간 이후 제출 불가", "모집마감 후 안내")],
        ):
            with self.subTest(raw=raw):
                result = application(raw)
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["value"]["end"]["time"], time)
                for e in result["evidence"]:
                    self.assertEqual(raw[e["start"]:e["end"]], e["quote"])

    def test_clock_tails_still_require_review(self):
        for clock in ("3시반까지", "17:00pm", "17:00 pm", "17:00반", "13:30~17:00pm"):
            with self.subTest(clock=clock):
                self.assertEqual(application("신청마감: 2026.11.4. " + clock)["status"], "needs_review")

    def test_recruitment_with_acceptance_schedule_is_not_results(self):
        for title in ("[모집] 공모전 참가자 모집 (합격자 발표 일정 안내)", "공모전 합격자 대상 참가자 모집"):
            result = analyze_notice({"source_id": "test", "external_id": "1", "version_id": 1,
                "url": "https://www.mju.ac.kr/1", "title": title, "body_text": ""}, CONFIG)
            self.assertEqual(result["classification"]["notice_kind"]["value"], "recruitment")

    def test_year_sharing_preserves_invalid_and_unread_time(self):
        for clock in ("25:00", "17:60", "오후 3시", "정오", "자정", "N시"):
            with self.subTest(clock=clock):
                raw = "접수기간: 2026.11.1.~11.4. " + clock
                result = application(raw)
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["contract_status"], "needs_check")
                self.assertEqual(result["value"]["end"]["date"], "2026-11-04")
                self.assertTrue(result["value"]["end"]["issue"])
                self.assertIn(clock, result["evidence"][0]["quote"])

    def test_missing_year_alone_is_resolved(self):
        result = application("접수기간: 2026.11.1.~11.4. 17:00")
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["value"]["end"]["time"], "17:00")
        self.assertIsNone(result["value"]["end"]["issue"])
        self.assertEqual(result["value"]["end"]["year_basis"], "same_explicit_range")

    def test_unparsed_clock_is_not_silently_dropped(self):
        for clock in ("오전 9시", "오후 3:30", "정오", "자정", "N시", "오후", "17:0", "100시", "3시반", "(오후3시)", "마감시간오후3시"):
            with self.subTest(clock=clock):
                result = application("신청마감: 2026.11.4.(수) " + clock)
                self.assertEqual(result["status"], "needs_review")
                self.assertTrue(result["value"]["end"]["issue"])
                self.assertIn(clock.replace(" ", ""), result["evidence"][0]["quote"].replace(" ", ""))

    def test_known_24_hour_and_date_only_stay_supported(self):
        for clock, expected in (("", None), ("까지", None), ("09:30", "09:30"), ("17시", "17:00"), ("17시30분", "17:30")):
            with self.subTest(clock=clock):
                result = application("신청마감: 2026.11.4. " + clock)
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["value"]["end"]["time"], expected)

    def test_no_colon_label_is_pending_and_does_not_invent_dates(self):
        for label, key in (("신청마감", "application_period"), ("제출기간", "submission_period"), ("행사일시", "event_period")):
            raw = label + " 2026.11.4.(수) 오후3시"
            result = extract_dates(TextView(raw))[key]
            self.assertEqual(result["status"], "needs_review")
            self.assertIsNone(result["value"])
            self.assertTrue(result["evidence"])

    def test_unread_label_value_is_pending_but_absent_label_is_missing(self):
        self.assertEqual(application("신청마감: 추후 공지")["status"], "needs_review")
        self.assertEqual(application("포스터를 참고하세요")["status"], "not_stated")

    def test_multiple_labels_keep_unparsed_candidate(self):
        result = application("신청마감 2026.11.4. 신청마감: 2026.11.8.")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["value"]["candidates"]), 2)
        pending = result["value"]["candidates"][0]
        self.assertIsNone(pending["value"])
        self.assertEqual(pending["contract_status"], "needs_check")
        self.assertTrue(pending["evidence"])

    def test_unread_or_invalid_time_range_end_is_pending(self):
        for clocks in ("13:30~오후5시", "13:30~17시", "13:30~25:00", "13:30~12:00", "13:30~17:00pm", "13:30~17:00반"):
            with self.subTest(clocks=clocks):
                result = extract_dates(TextView("행사일시: 2026.11.4. " + clocks))["event_period"]
                self.assertEqual(result["status"], "needs_review")
                self.assertIn(clocks, result["evidence"][0]["quote"])

    def test_evidence_quotes_are_exact_even_with_fragmented_time(self):
        raw = "신청\n마감: 2026. 11. 4.(수) 오\n후 3시"
        result = application(raw)
        self.assertEqual(result["status"], "needs_review")
        for evidence in result["evidence"]:
            self.assertEqual(raw[evidence["start"]:evidence["end"]], evidence["quote"])
            self.assertIn("오\n후 3시", evidence["quote"])

    def test_same_day_range_with_reversed_times_is_pending(self):
        result = application("신청기간:2026.11.4.18:00~11.4.17:00")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("역전", result["value"]["end"]["issue"])
        self.assertEqual(result["value"]["start"]["time"], "18:00")
        self.assertEqual(result["value"]["end"]["time"], "17:00")

    def test_result_titles_do_not_become_recruitment(self):
        for title in ("공모전 모집결과 안내", "공모전 심사결과 공지", "경진대회 최종 합격자 발표", "대회 선발결과", "공모전 합격발표"):
            with self.subTest(title=title):
                notice = {"source_id": "test", "external_id": "1", "version_id": 1, "url": "https://www.mju.ac.kr/1",
                          "title": title, "body_text": "다음 대회 모집 안내는 이전 공지를 참고"}
                result = analyze_notice(notice, CONFIG)
                self.assertEqual(result["classification"]["notice_kind"]["value"], "results")
                self.assertIn("follow_up", [item["code"] for item in result["notices"]])
                self.assertIsNone(result["classification"]["open_for_application"])

    def test_cancellation_delay_and_recruitment_order_is_preserved(self):
        for title, expected in (("대회 결과 발표 연기", "result_delayed"), ("대회 개최 취소", "cancellation"),
                                ("공모전 참가자 모집", "recruitment")):
            result = analyze_notice({"source_id": "test", "external_id": "1", "version_id": 1,
                                     "url": "https://www.mju.ac.kr/1", "title": title, "body_text": ""}, CONFIG)
            self.assertEqual(result["classification"]["notice_kind"]["value"], expected)


if __name__ == "__main__":
    unittest.main()
