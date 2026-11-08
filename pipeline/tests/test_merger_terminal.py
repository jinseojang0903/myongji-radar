"""학교나 AI 호출 없이 종료 후보의 원문 문맥을 검증한다."""

import unittest

from merger.terminal import terminal_notice_is_grounded


class TerminalNoticeTests(unittest.TestCase):
    def test_explicit_event_cancellation(self):
        examples = [
            ("제5회 공모전 취소 안내", "안내드립니다. 공모전은 취소되었습니다."),
            ("경진대회 개최 취소 안내", "부득이한 사정으로 경진대회 개최를 취소하였습니다."),
            ("프로그램 취소 안내", "프로그램은 부득이하게 취소되었습니다."),
        ]
        for title, body in examples:
            with self.subTest(title=title):
                self.assertTrue(terminal_notice_is_grounded({"title": title, "body_text": body}, "cancellation"))

    def test_explicit_final_result(self):
        examples = [
            ("공모전 최종 결과 발표", "공모전 최종 결과를 발표합니다."),
            ("경진대회 최종 수상자 발표", "최종 수상자 명단을 다음과 같이 발표합니다."),
            ("최종 선정 결과 안내", "최종 선정 결과를 아래와 같이 공지합니다."),
        ]
        for title, body in examples:
            with self.subTest(title=title):
                self.assertTrue(terminal_notice_is_grounded({"title": title, "body_text": body}, "results"))

    def test_result_timing_and_preliminary_result_are_not_terminal(self):
        examples = [
            ("공모전 결과 발표 일정 변경 안내", "결과 발표 일정이 변경되었습니다."),
            ("공모전 결과 발표 안내", "최종 결과를 발표할 예정입니다."),
            ("예선 결과 발표 연기 안내", "예선 결과 발표가 연기되었습니다."),
            ("예선 결과 발표", "예선 결과를 아래와 같이 발표합니다."),
            ("공모전 결과 발표 안내", "결과 발표일은 추후 공지합니다."),
            ("최종 결과 발표 안내", "최종 결과를 발표합니다. 발표는 연기되었습니다."),
            ("최종 결과 발표", "최종 결과를 발표합니다. 시상식은 추후 공지합니다."),
            ("최종 결과 발표", "최종 결과를 발표합니다. 위 발표는 취소되었습니다."),
            ("최종 결과 발표", "최종 결과를 발표합니다. 위 결과를 철회합니다."),
            ("최종 결과 발표 번복", "최종 결과를 발표합니다."),
        ]
        for title, body in examples:
            with self.subTest(title=title, body=body):
                self.assertFalse(terminal_notice_is_grounded({"title": title, "body_text": body}, "results"))

    def test_individual_or_negated_cancellation_is_not_event_cancellation(self):
        examples = [
            ("공모전 참가 신청 취소 안내", "공모전 신청을 취소하였습니다."),
            ("모집 취소 안내", "참가자의 접수 취소 방법을 안내합니다."),
            ("대회 취소 여부 안내", "대회 취소 여부를 검토합니다."),
            ("행사 취소 안내", "행사는 취소되지 않았습니다."),
            ("행사 취소 안내", "행사가 취소되었습니다라는 소문은 사실이 아닙니다."),
            ("행사 취소 안내", "행사가 취소되었습니다. 취소 사실은 아닙니다."),
            ("공모전 취소 안내", "공모전은 취소되었습니다. 취소 철회 가능성을 검토합니다."),
            ("공모전 취소 안내", "공모전은 취소되었습니다. 위 안내를 철회합니다."),
            ("공모전 취소 번복 안내", "공모전은 취소되었습니다."),
            ("대회 취소 안내", "대회는 취소되었습니다. 대회를 정상 진행합니다."),
            ("대회 취소 안내", "대회는 취소되었습니다. 행사를 재개합니다."),
            ("대회 취소 안내", "대회는 취소되었습니다. 그 취소가 취소되었습니다."),
            ("대회 정상적으로 개최 안내", "대회는 취소되었습니다."),
            ("대회 취소 안내", "대회는 취소합니다. 그러나 이번 대회는 다시 진행합니다."),
            ("대회 취소 안내", "대회는 취소합니다. 대회를 계속해서 운영합니다."),
            ("대회 취소 안내", "대회는 취소합니다. 행사는 그대로 개최합니다."),
            ("대회 취소 안내", "대회는 취소합니다. 대회 진행을 유지합니다."),
        ]
        for title, body in examples:
            with self.subTest(title=title, body=body):
                self.assertFalse(terminal_notice_is_grounded({"title": title, "body_text": body}, "cancellation"))

    def test_missing_source_wrong_stage_and_title_only_are_not_terminal(self):
        self.assertFalse(terminal_notice_is_grounded(None, "results"))
        self.assertFalse(terminal_notice_is_grounded({}, "results"))
        self.assertFalse(terminal_notice_is_grounded({"title": "대회 취소 안내", "body_text": ""}, "cancellation"))
        self.assertFalse(terminal_notice_is_grounded({"title": "대회 취소 안내", "body_text": "첨부 파일 확인"}, "cancellation"))
        notice = {"title": "공모전 취소 안내", "body_text": "공모전은 취소되었습니다."}
        for stage in (None, "unknown", "result_delayed", "results", "extension"):
            with self.subTest(stage=stage):
                self.assertFalse(terminal_notice_is_grounded(notice, stage))

    def test_title_and_body_are_both_checked_even_if_body_has_terminal_phrase(self):
        notice = {"title": "최종 결과 발표 연기 안내", "body_text": "최종 결과를 발표합니다."}
        self.assertFalse(terminal_notice_is_grounded(notice, "results"))
        notice = {"title": "대회 취소 예정 안내", "body_text": "대회는 취소되었습니다."}
        self.assertFalse(terminal_notice_is_grounded(notice, "cancellation"))


if __name__ == "__main__":
    unittest.main()
