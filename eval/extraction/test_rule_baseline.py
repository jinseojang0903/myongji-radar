"""규칙 기준선 변환기(#9) 시험. 실제 규칙 분석기(pipeline/collector/rules.py)로 만든 결과를 변환해 채점한다.

    python -m unittest discover -s eval/extraction
"""

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "pipeline"))
import rule_baseline  # noqa: E402
import score  # noqa: E402
from collector.rules import analyze_notice  # noqa: E402
from test_score import write_answer_key  # noqa: E402

RULES = tomllib.loads((ROOT / "pipeline/config/rules.toml").read_text(encoding="utf-8"))
CONTEST_BODY = "1. 참가자격: 자연캠퍼스 재학생 2. 문의: ctl@mju.ac.kr 3. 신청기간: 2026.10.01 ~ 2026.10.21"
# 이 시험이 기대는 정답 (test_score.EXACT와 따로 둔다)
ANSWERS = {
    "kind": ("모집", "found"),
    "apply": ("2026-10-01 ~ 2026-10-21 15:00", "found"),  # 규칙은 시각을 못 읽은 공지 → 값 틀림
    "stages": ("신청: 2026-10-01 ~ 2026-10-21 15:00", "found"),
    "campus": ("자연", "found"),
    "grades": ("any", "found"),
    "enrollment": ("허용: 재학 / 제외: 휴학", "found"),
    "team": ("~3, 개인 가능", "found"),
    "title": ("AI 경진대회", "found"),
    "contact": ("교수학습센터 ‖ ctl@mju.ac.kr ‖", "found"),
}


def notice(url, title, body, images=()):
    raw = {"source_id": "ctl", "external_id": url.rsplit("/", 1)[-1], "version_id": "v1", "url": url,
           "title": title, "body_text": body, "images": list(images), "attachments": []}
    return analyze_notice(raw, RULES)


def url(number):
    return f"https://ctl.mju.ac.kr/{number}"


class RuleBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.answers = self.folder / "answers"
        self.run = self.folder / "runs" / "rules"

    def tearDown(self):
        self.temp.cleanup()

    def convert(self, notices, sources, answer_ids=None):
        """sources: [(contest_id, 공지 번호, URL 번호)]"""
        ids = answer_ids if answer_ids is not None else {contest_id for contest_id, _, _ in sources}
        write_answer_key(self.answers, {contest_id: ANSWERS for contest_id in ids})
        (self.answers / "sources.csv").write_text(
            "contest_id,notice,board,posted,url,title\n" + "".join(f"{c},{n},보드,2026-10-01,{url(u)},제목\n" for c, n, u in sources),
            encoding="utf-8-sig")
        analysis = self.folder / "analysis.json"
        analysis.write_text(json.dumps({"rules_version": RULES["version"], "notices": notices}, ensure_ascii=False), encoding="utf-8")
        return rule_baseline.convert(analysis, self.run, self.answers)

    def fields(self, contest_id):
        return json.loads((self.run / "outputs" / f"{contest_id}.json").read_text(encoding="utf-8"))["fields"]

    def test_rule_values_are_moved_without_adding_ability(self):
        warnings = self.convert([notice(url(1), "AI 경진대회 모집 안내", CONTEST_BODY)], [("c", 1, 1)])
        self.assertEqual(warnings, [])
        fields = self.fields("c")
        self.assertEqual(fields["kind"]["value"], "모집")
        self.assertEqual(fields["apply"]["flag"], "found")
        self.assertEqual(fields["apply"]["value"]["end"], {"date": "2026-10-21", "time": None, "year_inferred": False})
        self.assertEqual(fields["apply"]["evidence"][0]["location"], "본문")
        self.assertEqual((fields["campus"]["flag"], fields["campus"]["value"]), ("needs_check", None),
                         "규칙은 조건 언급만 찾으므로 값을 정하지 않는다")
        self.assertIn("자연캠퍼스", fields["campus"]["note"])
        self.assertEqual(fields["grades"]["flag"], "missing")
        self.assertEqual(set(fields), set(rule_baseline.SCORED_ITEMS), "규칙이 다루지 않는 항목(단계·문의처 등)은 내지 않는다")

    def test_scoring_uses_only_rule_items(self):
        self.convert([notice(url(1), "AI 경진대회 모집 안내", CONTEST_BODY)], [("c", 1, 1)])
        answer_key = score.read_answer_key(self.answers)
        run_score = score.score_run(self.run, answer_key, {})
        by_item = {result.item: result.category for result in run_score.results}
        self.assertEqual(by_item, {"kind": "correct", "apply": "wrong", "campus": "over_hold", "grades": "missed",
                                   "enrollment": "over_hold", "team": "missed"},
                         "규칙 항목만 채점 (정답표의 stages·title·contact는 빠진다)")
        report = score.render_report([run_score], answer_key)
        self.assertIn("규칙 분석기 (rules-v1.0)은 kind, apply, campus", report)

    def test_later_update_notice_wins(self):
        self.convert([notice(url(1), "공모전 모집 안내", "신청기간: 2026.10.01 ~ 2026.10.21"),
                      notice(url(2), "공모전 연장 안내", "신청기간: 2026.10.01 ~ 2026.10.28")], [("c", 1, 1), ("c", 2, 2)])
        apply = self.fields("c")["apply"]
        self.assertEqual((apply["flag"], apply["value"]["end"]["date"]), ("found", "2026-10-28"), "system.md 4절 (가)")
        self.assertEqual(apply["alternatives"], [{"text": "2026-10-21", "notice": 1}])
        self.assertEqual(self.fields("c")["kind"]["value"], "연장")

    def test_same_time_differences_and_unsettled_notices(self):
        self.convert(
            [notice(url(1), "공모전 모집 안내", "신청기간: 2026.10.01 ~ 2026.10.21"),
             notice(url(2), "공모전 모집 안내", "신청기간: 2026.10.01 ~ 2026.10.25"),
             notice(url(3), "공모전 연장 안내", "신청기간: 10.28까지"),
             notice(url(4), "공모전 모집 안내", "신청기간: 2026.10.01 ~ 2026.10.21 접수마감: 2026.10.25까지"),
             notice(url(5), "취업 특강 안내", "신청기간: 2026.10.01 ~ 2026.10.21")],
            [("conflict", 1, 1), ("conflict", 2, 2), ("unsettled", 1, 1), ("unsettled", 2, 3),
             ("candidates", 1, 4), ("lecture", 1, 5)])
        self.assertEqual(self.fields("conflict")["apply"]["flag"], "conflict", "같은 성격 공지끼리 다르면 고르지 않는다")
        unsettled = self.fields("unsettled")["apply"]
        self.assertEqual((unsettled["flag"], unsettled["value"]), ("needs_check", None), "연도 없는 연장 공지를 무시하지 않는다")
        self.assertEqual(self.fields("candidates")["apply"]["flag"], "needs_check", "복수 일정")
        output = json.loads((self.run / "outputs" / "lecture.json").read_text(encoding="utf-8"))
        self.assertIs(output["is_contest"]["value"], False)

    def test_image_notice_is_not_missing(self):
        self.convert([notice(url(1), "공모전 모집 안내", "", images=[{"url": url(9), "alt": ""}])], [("c", 1, 1)])
        fields = self.fields("c")
        self.assertEqual((fields["apply"]["flag"], fields["campus"]["flag"]), ("needs_check", "needs_check"),
                         "이미지에 있을 수 있어 '공지에 없음'으로 단정하지 않는다")

    def test_incomplete_contests_are_not_converted(self):
        body_notice = notice(url(1), "공모전 모집 안내", CONTEST_BODY)
        warnings = self.convert([body_notice, notice(url(2), "공모전 모집 안내", CONTEST_BODY), notice(url(2), "공모전 모집 안내", CONTEST_BODY)],
                                [("partial", 1, 1), ("partial", 2, 9), ("dup", 1, 2)], answer_ids={"partial", "dup", "no-source"})
        self.assertEqual(list((self.run / "outputs").glob("*.json")), [], "공지 일부나 어느 판인지 모르는 공지로 만들지 않는다")
        self.assertTrue(any("partial" in w and "없는 공지 1개" in w for w in warnings))
        self.assertTrue(any("dup" in w and "두 번 있는 공지 1개" in w for w in warnings))
        self.assertTrue(any("no-source" in w and "sources.csv" in w for w in warnings))
        failed = score.score_run(self.run, score.read_answer_key(self.answers), {}).failed_contests
        self.assertEqual(sorted(failed), ["dup", "no-source", "partial"])

    def test_rerun_removes_old_outputs_and_refuses_other_runs(self):
        self.convert([notice(url(1), "공모전 모집 안내", CONTEST_BODY)], [("c", 1, 1)])
        self.assertTrue((self.run / "outputs" / "c.json").exists())
        self.convert([], [("c", 1, 1)])
        self.assertFalse((self.run / "outputs" / "c.json").exists(), "빠진 대회가 옛 값으로 채점되지 않게")
        ai_run = self.folder / "runs" / "ai"
        (ai_run / "outputs").mkdir(parents=True)
        (ai_run / "meta.json").write_text('{"model": "claude"}', encoding="utf-8")
        (ai_run / "outputs" / "c.json").write_text("{}", encoding="utf-8")
        self.run = ai_run
        with self.assertRaises(ValueError):
            self.convert([], [("c", 1, 1)])
        self.assertTrue((ai_run / "outputs" / "c.json").exists(), "AI 실행 폴더는 건드리지 않는다")

    def test_bad_scored_items(self):
        warnings = []
        self.assertEqual(score._scored_items({}, warnings), score.ITEMS, "키가 없으면 (AI 실행) 모든 항목")
        self.assertEqual(score._scored_items({"scored_items": "apply"}, warnings), score.ITEMS)
        self.assertEqual(score._scored_items({"scored_items": ["apply", "없는 항목"]}, warnings), ["apply"])
        self.assertEqual(score._scored_items({"scored_items": []}, warnings), [])
        self.assertEqual(len(warnings), 3)


if __name__ == "__main__":
    unittest.main()
