"""추출 채점기(#9) 시험. 직접 만든 작은 정답표와 AI 출력(prompts/extract_v0.2/example_output.json 변형)으로 확인한다.

    python -m unittest discover -s eval/extraction
"""

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "prompts/extract_v0.2/example_output.json").read_text(encoding="utf-8"))
HEADER = ["contest_id", "field", "value", "flag", "quote", "notice", "location", "file", "note", "checker", "checked_at"]

EXACT = {  # 예시 출력과 같은 정답
    "title": ("AI 리터러시 경진대회", "found"),
    "organizer": ("교수학습센터", "found"),
    "scope": ("교내", "found"),
    "kind": ("모집", "found"),
    "apply": ("2026-10-01 ~ 2026-10-21 15:00", "found"),
    "stages": ("신청: 2026-10-01 ~ 2026-10-21 15:00", "found"),
    "campus": ("any", "found"),
    "grades": ("any", "found"),
    "enrollment": ("허용: 재학 / 제외: 휴학, 수료, 초과학기, 졸업유예, 대학원생", "found"),
    "team": ("~3, 개인 가능", "found"),
    "deliverables": ("기획서·제안서, 발표 ‖ 예선: 신청서, 제안서(최대 3장) / 결선: 현장 발표용 PPT, 최종 결과물, 프롬프트", "found"),
    "benefits": ("대상 1팀 총장상·100만원, 최우수 1팀 60만원", "found"),
    "mileage": ("창의 ‖ 10 ‖ 예선 참가자 전원 창의 10점 (단순 MYiCap+ 신청은 해당 없음)", "found"),
    "how_to_apply": ("제출 파일을 zip으로 압축해 MYiCap+ 비교과 프로그램 신청 시 업로드", "found"),
    "contact": ("교수학습센터 ‖ ctl@mju.ac.kr ‖", "found"),
}
TRAP = dict(EXACT) | {  # 함정: 각 줄 끝의 분류가 나와야 한다
    "title": ("다른 이름 공모전", "found"),                    # 판정 전
    "scope": ("교내", "found"),                               # AI는 needs_check → 과잉 보류
    "kind": ("변경", "needs_check"),                          # AI는 found 모집 → 과신
    "apply": ("2026-10-01 ~ 2026-10-20", "found"),            # AI 마감이 더 늦음 → 값 틀림, 위험
    "stages": ("신청: 2026-10-01 ~ 2026-10-20 ‖ 발표: ~ 2026-11-01", "found"),  # 값 틀림
    "campus": ("인문,자연", "found"),                          # any와 같음 → 정답
    "grades": ("3,4", "found"),                               # AI any → 값 틀림, 위험
    "enrollment": ("", "missing"),                            # AI가 값을 냄 → 지어냄, 위험
    "team": ("2~5, 개인 불가", "found"),                       # AI ~3 개인 가능 → 값 틀림, 위험
    "deliverables": ("영상 ‖ 영상 1편", "found"),               # 태그 틀림, 요약 판정 전
    "mileage": ("", "missing"),                               # 지어냄 (점수·요약)
    "contact": ("센터 ‖ ‖ 02-300-1234", "found"),             # 정답에 이메일 없음 → AI 이메일은 지어냄
}


def write_answer_key(folder: Path, contests: dict, extra_rows=()):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "answers.csv", "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(HEADER)
        for name in score.FIELDS:
            writer.writerow(["example-id", name] + [""] * 9)
        for contest_id, rows in contests.items():
            for name, (value, flag) in rows.items():
                writer.writerow([contest_id, name, value, flag, "", "", "", "", "", "시험", "2026-11-05"])
        for row in extra_rows:
            writer.writerow(row)
    (folder / "sources.csv").write_text("contest_id,notice,board,posted,url,title\n", encoding="utf-8-sig")


def trap_output():
    output = copy.deepcopy(EXAMPLE)
    output["fields"]["scope"]["flag"] = "needs_check"
    return output


def write_run(folder: Path, outputs: dict, model="시험 모델"):
    (folder / "outputs").mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"model": model, "prompt_version": "v0.2"}, ensure_ascii=False), encoding="utf-8")
    for contest_id, output in outputs.items():
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        (folder / "outputs" / f"{contest_id}.json").write_text(text, encoding="utf-8")
    return folder


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        write_answer_key(self.folder / "answers", {"exact-2026": EXACT, "trap-2026": TRAP, "no-output-2026": EXACT},
                         extra_rows=[["broken-2026", "team", "세 명", "found"] + [""] * 7])
        self.answer_key = score.read_answer_key(self.folder / "answers")
        self.run = write_run(self.folder / "runs" / "sonnet-v0.2", {"exact-2026": EXAMPLE, "trap-2026": trap_output(), "extra-2026": EXAMPLE})

    def results(self, judgments=None):
        run_score = score.score_run(self.run, self.answer_key, judgments or {})
        return run_score, {(result.contest_id, result.item): result for result in run_score.results}

    def test_answer_key_skips_example_and_reports_broken_cells(self):
        self.assertNotIn("example-id", self.answer_key.contests)
        self.assertTrue(any("broken-2026 team 값 형식" in problem for problem in self.answer_key.problems))
        self.assertTrue(any("broken-2026: 항목 줄 없음" in problem for problem in self.answer_key.problems))
        self.assertEqual(self.answer_key.contests["broken-2026"], {}, "깨진 칸은 채점에서 뺀다")

    def test_exact_output_scores_all_auto_items_correct(self):
        _, by_key = self.results()
        for item in score.AUTO_ITEMS:
            self.assertEqual(by_key[("exact-2026", item)].category, "correct", item)
        self.assertEqual(by_key[("exact-2026", "benefits")].category, "needs_human", "표기가 다르면 사람이 판정한다")
        for item in ["title", "organizer", "deliverables.text", "mileage.text", "how_to_apply"]:
            self.assertEqual(by_key[("exact-2026", item)].category, "correct", "표기가 같으면 판정 없이 정답")

    def test_trap_categories_and_risky_errors(self):
        _, by_key = self.results()
        expected = {
            "title": ("needs_human", False), "scope": ("over_hold", False), "kind": ("overconfident", False),
            "apply": ("wrong", True), "stages": ("wrong", False), "campus": ("correct", False), "grades": ("wrong", True),
            "enrollment": ("fabricated", True), "team": ("wrong", True), "deliverables.tags": ("wrong", False),
            "deliverables.text": ("needs_human", False), "mileage.points": ("fabricated", False), "mileage.text": ("fabricated", False),
            "contact.email": ("fabricated", False), "organizer": ("correct", False),
        }
        for item, (category, is_risky) in expected.items():
            result = by_key[("trap-2026", item)]
            self.assertEqual((result.category, result.is_risky), (category, is_risky), item)

    def test_missing_output_is_failure(self):
        run_score, by_key = self.results()
        self.assertEqual(run_score.failed_contests, ["no-output-2026"])
        self.assertIn("no-output-2026: 출력 실패 (출력 파일 없음)", run_score.warnings)
        self.assertTrue(all(result.category == "failed" for (contest_id, _), result in by_key.items() if contest_id == "no-output-2026"))
        self.assertEqual(run_score.extra_outputs, ["extra-2026"])
        for broken, reason in [("{깨진", "읽지 못함"), (json.dumps({"is_contest": {"value": False}, "fields": {}}), "대회가 아니라고"),
                               ("[1]", "객체가 아님")]:
            run = write_run(self.folder / "runs" / "broken", {"exact-2026": broken})
            run_score = score.score_run(run, self.answer_key, {})
            self.assertIn("exact-2026", run_score.failed_contests)
            self.assertTrue(any(reason in warning for warning in run_score.warnings), reason)

    def test_judgments_fill_human_items(self):
        benefits_ai = score.display(score.convert_output("benefits", EXAMPLE["fields"]["benefits"]["value"])["benefits"], limit=None)
        _, by_key = self.results({("exact-2026", "benefits"): ("정답", benefits_ai), ("trap-2026", "title"): ("오답", "AI 리터러시 경진대회")})
        self.assertEqual(by_key[("exact-2026", "benefits")].category, "correct")
        self.assertEqual(by_key[("trap-2026", "title")].category, "wrong")
        run_score, by_key = self.results({("exact-2026", "benefits"): ("정답", "판정할 때와 다른 AI 값")})
        self.assertEqual(by_key[("exact-2026", "benefits")].category, "needs_human", "AI 값이 바뀌면 옛 판정을 쓰지 않는다")
        self.assertTrue(any("옛 판정" in warning for warning in run_score.warnings))

    def test_judgment_file_rules(self):
        judgments_dir = self.folder / "judgments"
        judgments_dir.mkdir()
        (judgments_dir / "sonnet-v0.2.csv").write_text(
            "run,contest_id,item,answer,ai,judgment\nsonnet-v0.2,exact-2026,benefits,a,b,맞음\n", encoding="utf-8-sig")
        warnings = []
        self.assertEqual(score.read_judgments("sonnet-v0.2", judgments_dir, warnings), {})
        self.assertTrue(any("정답·오답이 아니라" in warning for warning in warnings))
        run_score = score.score_run(self.run, self.answer_key, {})
        score.write_judgment_todo(run_score, self.run)
        todo = self.run / "judgments_todo.csv"
        with open(todo, encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        rows[1][-1] = "정답"
        with open(todo, "w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(rows)
        score.write_judgment_todo(run_score, self.run)
        self.assertIn("정답", todo.read_text(encoding="utf-8-sig"), "채운 판정이 있으면 덮어쓰지 않는다")
        self.assertTrue((self.run / "judgments_todo.new.csv").exists(), "새 할 일은 다른 파일에")
        self.assertTrue(any("judgments_todo.new.csv" in warning for warning in run_score.warnings))
        (judgments_dir / "dup.csv").write_text("run,contest_id,item,answer,ai,judgment\nx,a,title,,v,정답\nx,a,title,,v,오답\n",
                                               encoding="utf-8-sig")
        warnings = []
        self.assertEqual(score.read_judgments("dup", judgments_dir, warnings), {("a", "title"): ("오답", "v")})
        self.assertTrue(any("두 번" in warning for warning in warnings))

    def test_hold_flags_and_missing(self):
        truth_hold = score.Truth("conflict", ("2026-10-01", "2026-10-21"))
        self.assertEqual(score.score_item(truth_hold, score.Predicted("needs_check", None), "apply")[0], "correct",
                         "needs_check와 conflict는 같은 확인 필요")
        self.assertEqual(score.score_item(truth_hold, score.Predicted("missing", None), "apply")[0], "missed")
        truth_missing = score.Truth("missing", None)
        self.assertEqual(score.score_item(truth_missing, score.Predicted("missing", None), "apply")[0], "correct")
        self.assertEqual(score.score_item(truth_missing, score.Predicted("found", None), "apply")[0], "correct", "값이 없으면 지어낸 것이 아님")
        truth_found = score.Truth("found", "교내")
        self.assertEqual(score.score_item(truth_found, score.Predicted("found", None), "scope")[0], "missed")

    def test_permissive_rules(self):
        permissive = score.is_more_permissive
        self.assertTrue(permissive("apply", ("2026-10-01", None), ("2026-10-01", "2026-10-21")), "마감을 비우면 위험")
        self.assertFalse(permissive("apply", (None, "2026-10-21"), (None, None)), "정답에 마감이 없으면 AI 마감은 더 엄격")
        self.assertTrue(permissive("team", (None, None, False, None), (2, 3, False, None)), "상한·하한을 없앰")
        self.assertTrue(permissive("team", (2, None, False, None), (2, 3, False, None)), "상한만 없앰")
        self.assertTrue(permissive("campus", score.FORMAT_ERROR, frozenset({"인문"})), "형식 오류는 위험으로 본다")
        self.assertTrue(permissive("apply", (None, "2026-10-21"), (None, "2026-10-21 15:00")), "날짜만 있으면 그날 끝으로 본다")
        self.assertFalse(permissive("apply", (None, "2026-10-20"), (None, "2026-10-21")), "마감이 이르면 위험하지 않다")
        self.assertTrue(permissive("campus", frozenset({"자연"}), frozenset({"인문"})), "다른 캠퍼스를 허용")
        self.assertFalse(permissive("grades", frozenset({3}), frozenset({3, 4})), "더 좁으면 위험하지 않다")
        allowed, excluded = frozenset({"재학"}), frozenset({"휴학"})
        self.assertTrue(permissive("enrollment", (allowed, frozenset()), (allowed, excluded)), "제외 대상을 빠뜨림")
        self.assertTrue(permissive("enrollment", (frozenset(), excluded), (allowed, excluded)), "허용 목록을 비우면 모두 허용")
        self.assertFalse(permissive("enrollment", (allowed, excluded | {"수료"}), (allowed, excluded)))
        self.assertTrue(permissive("team", (None, None, True, None), (None, 3, False, None)), "제한 없음으로 냄")
        self.assertTrue(permissive("team", (1, 3, False, None), (2, 3, False, None)), "최소 인원을 낮춤")
        self.assertFalse(permissive("team", (2, 3, False, False), (None, 5, False, None)))

    def test_parse_answer_formats(self):
        parse = score.parse_answer
        self.assertEqual(parse("apply", "~ 2026-10-23"), {"apply": (None, "2026-10-23")})
        self.assertEqual(parse("stages", "발표: ~ 2026-11-23 ‖ 신청: 2026-09-14 ~ 2026-10-23"),
                         {"stages": (("신청", "2026-09-14", "2026-10-23"), ("발표", None, "2026-11-23"))}, "날짜 순으로 정렬해 비교")
        self.assertEqual(parse("grades", "1,2,3,4"), {"grades": "any"})
        self.assertEqual(parse("team", "제한 없음, 개인 모름"), {"team": (None, None, True, None)})
        self.assertEqual(parse("team", "3, 개인 불가"), {"team": (3, 3, False, False)})
        self.assertEqual(parse("enrollment", "허용: / 제외:"), {"enrollment": (frozenset(), frozenset())})
        self.assertEqual(parse("contact", "센터 ‖ CTL@MJU.AC.KR ‖ 02"), {"contact.email": "ctl@mju.ac.kr"})
        self.assertEqual(parse("how_to_apply", "MYiCap+로 신청 ‖ "), {"how_to_apply": "MYiCap+로 신청"}, "링크 칸을 비운 표기")
        self.assertEqual(parse("team", "~, 개인 가능"), {"team": (None, None, False, True)})
        self.assertEqual(parse("mileage", "창의 ‖ 10점 ‖ 요약")["mileage.points"], 10.0)
        self.assertEqual(parse("mileage", " ‖ 2.5 ‖ 요약"), {"mileage.points": 2.5, "mileage.text": "요약"})
        self.assertEqual(parse("enrollment", "허용： 재학\n/ 제외: 휴학"), {"enrollment": (frozenset({"재학"}), frozenset({"휴학"}))},
                         "셀 안 줄바꿈·전각 콜론")
        self.assertEqual(parse("stages", "예선: ~ 2026-10-01 ‖ 예선: ~ 2026-10-08"),
                         {"stages": (("예선", None, "2026-10-01"), ("예선", None, "2026-10-08"))}, "같은 이름 단계 두 개")
        for name, text in [("apply", "10월 21일"), ("team", "3명"), ("campus", "서울"), ("stages", "2026-10-01 ~ 2026-10-02"),
                           ("mileage", "창의 ‖ 열 ‖ 요약"), ("apply", "~ 2026-10-21 9:00")]:
            with self.assertRaises(score.AnswerFormatError, msg=(name, text)):
                parse(name, text)

    def test_single_date_stage_in_start_does_not_match_end_convention(self):
        output = copy.deepcopy(EXAMPLE)
        output["fields"]["stages"]["value"].append({"name": "발표", "label": "발표", "start": {"date": "2026-11-23", "time": None},
                                                    "end": {"date": None, "time": None}})
        converted = score.convert_output("stages", output["fields"]["stages"]["value"])["stages"]
        self.assertIn(("발표", "2026-11-23", None), converted, "프롬프트 규칙(날짜 하나면 end)을 어기면 정답표와 다르게 나온다")

    def test_ai_format_errors_and_empty_lists_are_not_hidden(self):
        output = copy.deepcopy(EXAMPLE)
        output["fields"]["campus"]["value"] = {"any": False, "allowed": ["서울"]}
        output["fields"]["stages"]["value"] = []
        output["fields"]["title"]["value"] = 3
        output["fields"]["how_to_apply"]["value"] = "글자"
        answers = dict(EXACT) | {"campus": ("", "missing")}
        write_answer_key(self.folder / "answers2", {"format-2026": answers})
        key = score.read_answer_key(self.folder / "answers2")
        run = write_run(self.folder / "runs" / "format", {"format-2026": output})
        (run / "meta.json").write_text("{깨진", encoding="utf-8")
        run_score = score.score_run(run, key, {})
        by_key = {result.item: result for result in run_score.results}
        self.assertEqual(by_key["campus"].category, "fabricated", "형식 오류를 값 없음으로 숨기지 않는다")
        self.assertEqual(by_key["stages"].category, "missed", "빈 단계 목록은 값 없음")
        self.assertEqual(by_key["title"].category, "wrong", "글이 아닌 대회명은 형식 오류")
        self.assertEqual(by_key["how_to_apply"].category, "wrong", "형식 오류는 사람에게 판정을 맡기지 않는다")
        self.assertTrue(any("형식 오류" in warning for warning in run_score.warnings))
        self.assertTrue(any("meta.json" in warning for warning in run_score.warnings))

    def test_hold_ratio_excludes_failed_contests(self):
        run_score, _ = self.results()
        summary = score.score_json(run_score)["overall_auto"]
        found = [r for r in run_score.results if r.item in score.AUTO_ITEMS and r.truth_flag == "found" and r.category != "failed"]
        self.assertEqual(summary["hold_ratio"], sum(r.category == "over_hold" for r in found) / len(found))
        report = score.render_report([run_score], self.answer_key)
        self.assertIn("| 출력 실패 (대회 수) | 1 |", report)

    def test_judgment_todo_keeps_long_values(self):
        long_text = "가" * 150
        output = copy.deepcopy(EXAMPLE)
        output["fields"]["benefits"]["value"] = long_text
        write_answer_key(self.folder / "answers3", {"long-2026": EXACT})
        run = write_run(self.folder / "runs" / "long", {"long-2026": output})
        run_score = score.score_run(run, score.read_answer_key(self.folder / "answers3"), {})
        score.write_judgment_todo(run_score, run)
        self.assertIn(long_text, (run / "judgments_todo.csv").read_text(encoding="utf-8-sig"), "판정자가 전체 글을 본다")

    def test_report_never_invents_numbers(self):
        empty_report = score.render_report([], self.answer_key)
        self.assertIn("측정 중", empty_report)
        self.assertNotIn("%", empty_report)
        run_score, _ = self.results()
        report = score.render_report([run_score], self.answer_key)
        self.assertIn("위험 오류", report)
        self.assertIn("**위험**", report)
        self.assertIn("판정 전", report)
        self.assertIn("broken-2026", report, "정답표 형식 문제를 보고한다")

    def test_cli_writes_score_and_judgment_todo(self):
        report_path = self.folder / "reports" / "compare.md"
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = score.main([str(self.run), "--answers", str(self.folder / "answers"), "--report", str(report_path)])
        self.assertEqual(code, 0)
        self.assertTrue(report_path.exists())
        saved = json.loads((self.run / "score.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["failed_contests"], ["no-output-2026"])
        with open(self.run / "judgments_todo.csv", encoding="utf-8-sig") as file:
            todo = list(csv.DictReader(file))
        self.assertEqual({(row["contest_id"], row["item"]) for row in todo},
                         {("exact-2026", "benefits"), ("trap-2026", "benefits"), ("trap-2026", "title"), ("trap-2026", "deliverables.text")})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(score.main([str(self.folder / "없는 폴더"), "--answers", str(self.folder / "answers")]), 2)


if __name__ == "__main__":
    unittest.main()
