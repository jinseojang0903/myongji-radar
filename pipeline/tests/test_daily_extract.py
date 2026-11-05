"""매일 추출(#14, `python -m extractor daily`) 시험. 실제 claude는 부르지 않는다.
실제 규칙 분석기로 만든 analysis.json → 매일 추출 → 검증기(#15) → 내보내기(#17)까지 이어 본다.
(unittest discover -s tests로 돌린다: 시험 도우미를 test_extractor에서 가져온다)"""
import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from collector.config import load_sources
from collector.rules import analyze_notice
from exporter.__main__ import read_bundles
from exporter.cards import boards_from_sources, build_cards_json
from extractor import claude as claude_cli
from extractor import daily
from extractor.__main__ import main as cli_main
from extractor.daily import card_id_of
from test_extractor import EXAMPLE, INIT, FakeRun, stream
from verifier.__main__ import read_originals, verify_bundles

PIPELINE = Path(__file__).resolve().parents[1]
RULES = tomllib.loads((PIPELINE / "config/rules.toml").read_text(encoding="utf-8"))
NAMES_BODY = "수상자 홍길동 60123456"
CTL = "mju-ctl-ctl-792-"


def collected(number, title, body, source_id="mju_ctl", version_id=1, published_at="2026-10-01T10:00:00+09:00"):
    """수집기 export 모양 (글 번호는 실제처럼 "ctl/792/<번호>")."""
    return {"source_id": source_id, "external_id": f"ctl/792/{number}", "version_id": version_id,
            "url": f"https://ctl.mju.ac.kr/bbs/ctl/792/{number}/artclView.do", "title": title,
            "published_at": published_at, "body_text": body, "images": [], "attachments": []}


class DailyExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.notices = [
            collected(100, "AI 리터러시 경진대회 모집 안내", "신청기간: 2026.10.01 ~ 2026.10.21 공모전 참가자 모집"),
            collected(101, "취업 특강 안내", "특강 일정"),
            collected(102, "AI 리터러시 경진대회 수상자 발표", NAMES_BODY),
            collected(103, "창업 아이디어 공모전 참가 신청 안내", "공모전 신청 안내", source_id="mju_startup"),
        ]
        self.write_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def write_inputs(self, analysis_notices=None):
        (self.folder / "notices.json").write_text(json.dumps({"notices": self.notices}, ensure_ascii=False), encoding="utf-8")
        analysis = {"notices": [analyze_notice(notice, RULES) for notice in (analysis_notices or self.notices)]}
        (self.folder / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, fake, *extra):
        args = ["daily", "--model", "sonnet", "--notices", str(self.folder / "notices.json"),
                "--analysis", str(self.folder / "analysis.json"), "--out", str(self.folder / "card_bundles.json"),
                "--cache", str(self.folder / "extractions"), "--ledger", str(self.folder / "calls.json"), *extra]
        clean_env = {key: value for key, value in os.environ.items() if key not in claude_cli.ACCOUNT_ENV}
        stderr = io.StringIO()
        with mock.patch.object(claude_cli.subprocess, "run", fake), \
                mock.patch.object(claude_cli, "resolve_claude", return_value="claude.exe"), \
                mock.patch.dict(os.environ, clean_env, clear=True), contextlib.redirect_stderr(stderr):
            code = cli_main(args)
        return code, stderr.getvalue()

    def bundles(self):
        return json.loads((self.folder / "card_bundles.json").read_text(encoding="utf-8"))["bundles"]

    def card_ids(self):
        return [bundle["card_id"] for bundle in self.bundles()]

    def test_candidates_are_extracted_once_into_bundles(self):
        fake = FakeRun()
        code, log = self.run_cli(fake)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.calls), 3, "확인 호출 1번 + 대회 후보 2건 (특강·수상자 발표는 부르지 않는다)")
        sent = "".join(call["input"].decode("utf-8") for call in fake.calls)
        self.assertNotIn("홍길동", sent, "결과 공지는 AI로 가지 않는다")
        self.assertIn('board="명지대학교 교수학습센터"', sent, "게시판 이름은 출처 설정에서")
        self.assertEqual(self.card_ids(), [CTL + "100", "mju-startup-ctl-792-103"], "실제 글 번호 모양으로 날마다 같은 id")
        first = self.bundles()[0]
        self.assertEqual(first["notices"][0]["url"], self.notices[0]["url"])
        self.assertEqual((first["extraction"], first["statuses"], first["meta"]["extract_model"]),
                         (EXAMPLE, {}, "claude-haiku-test"), "실제 모델 ID를 남기고, 사람 검수 기록은 비운다")
        fake = FakeRun()
        code, log = self.run_cli(fake)
        self.assertEqual((code, fake.calls), (0, []), "같은 판을 이미 뽑았으면 확인 호출도 하지 않는다")
        self.assertEqual(len(self.bundles()), 2, "뽑아 둔 결과로 묶음을 다시 만든다")
        runs = (self.folder / "extractions" / "_runs.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("모집", runs, "실행 기록에 공지 내용이 없다")
        self.assertEqual(json.loads(runs.splitlines()[0])["totals"]["calls"], 2)

    def test_cards_stay_while_waiting_for_re_extraction(self):
        self.run_cli(FakeRun())
        self.notices[0]["version_id"] = 2
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake, "--model", "opus", "--max-new", "1")
        self.assertEqual((code, len(fake.calls)), (0, 2), "모델이 바뀌어 둘 다 다시 뽑을 대상이지만 이번엔 1건만")
        self.assertEqual(self.card_ids(), [CTL + "100", "mju-startup-ctl-792-103"],
                         "모델만 바뀌어 다시 못 뽑은 카드(103)는 옛 결과로 남고, 새 판(100)은 이번에 다시 뽑았다")
        runs = [json.loads(line) for line in (self.folder / "extractions" / "_runs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(runs[-1]["stale_bundles"], 1)
        with mock.patch.object(daily, "prompt_fingerprint", return_value="다른 프롬프트"):
            fake = FakeRun()
            self.run_cli(fake, "--model", "opus")
        self.assertEqual(len(fake.calls), 3, "프롬프트 파일이 바뀌면 다시 뽑는다")

    def test_broken_failure_records_are_ignored(self):
        cache = self.folder / "extractions"
        cache.mkdir()
        (cache / "_failures.json").write_text(json.dumps({CTL + "100": "문자열", "mju-startup-ctl-792-103": {"count": "많이"}}),
                                              encoding="utf-8")
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (0, 3), "모양이 다른 실패 기록은 버리고 다시 시도한다")

    def test_force_and_prompt_changes_retry_given_up_notices(self):
        for _ in range(3):
            self.run_cli(FakeRun(stream(), stream(output=None), stream()))
        fake = FakeRun()
        self.run_cli(fake)
        self.assertEqual(fake.calls, [], "3번 실패해 포기")
        fake = FakeRun()
        self.run_cli(fake, "--force")
        self.assertEqual(len(fake.calls), 3, "--force면 포기한 공지도 다시")
        for _ in range(3):
            self.run_cli(FakeRun(stream(), stream(output=None)), "--model", "opus")
        with mock.patch.object(daily, "prompt_fingerprint", return_value="고친 프롬프트"):
            fake = FakeRun()
            self.run_cli(fake, "--model", "opus")
        self.assertEqual(len(fake.calls), 3, "프롬프트를 고치면 포기한 공지도 다시")

    def test_failures_stops_and_give_up(self):
        fake = FakeRun(stream(), stream(output=None), stream())
        code, log = self.run_cli(fake)
        self.assertEqual(code, 0, "모델이 형식에 못 맞춘 공지는 경고만 (매일 공개를 막지 않는다)")
        self.assertEqual(self.card_ids(), ["mju-startup-ctl-792-103"])
        for _ in range(2):
            self.run_cli(FakeRun(stream(), stream(output=None)))
        fake = FakeRun()
        code, log = self.run_cli(fake)
        self.assertEqual((code, fake.calls), (0, []), "같은 판을 3번 실패하면 더 부르지 않는다")
        self.assertIn("[포기]", log)
        before = '{"generated_at": "어제", "bundles": []}'  # 어제 묶음 표시 (다시 쓰면 바뀐다)
        (self.folder / "card_bundles.json").write_text(before, encoding="utf-8")
        self.notices[0]["version_id"] = 2
        self.write_inputs()
        fake = FakeRun(stream(init={**INIT, "tools": ["Bash"]}))
        code, log = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (2, 1), "판이 바뀌면 다시 부르되, 격리가 어긋나면 멈춘다")
        self.assertEqual((self.folder / "card_bundles.json").read_text(encoding="utf-8"), before, "멈추면 묶음을 덮어쓰지 않는다")
        self.assertEqual(self.run_cli(FakeRun(), "--max-new", "0")[0], 2)
        code, _ = self.run_cli(FakeRun(stream(), stream(output=None, result=json.dumps(EXAMPLE, ensure_ascii=False))))
        self.assertEqual(code, 0)
        cache = self.folder / "extractions" / (CTL + "100.json")
        self.assertFalse(cache.exists(), "스키마 강제를 거치지 않은 모델 글은 받지 않는다")

    def test_daily_limit_is_not_a_stop(self):
        self.run_cli(FakeRun(), "--max-calls", "3")
        self.notices[0]["version_id"] = 2
        self.write_inputs()
        fake = FakeRun()
        code, log = self.run_cli(fake, "--max-calls", "3")
        self.assertEqual((code, fake.calls), (0, []), "오늘 상한에 닿았으면 부르지 않고 뽑아 둔 결과로 묶는다")
        self.assertIn("[남음]", log)
        self.assertEqual(self.card_ids(), ["mju-startup-ctl-792-103"],
                         "공지가 바뀌었는데 아직 다시 못 뽑은 카드는 내린다 (옛 판의 값을 공개하지 않게)")
        self.assertIn("새 판을 기다려 내린 것 1건", log)

    def test_what_is_left_out(self):
        cancelled = collected(104, "AI 리터러시 경진대회 개최 취소 안내", "취소")
        dateless = collected(105, "데이터 공모전 모집 안내", "공모전 모집", published_at=None)
        newer = dict(self.notices[3], version_id=9)
        self.notices += [cancelled, dateless]
        self.write_inputs(analysis_notices=self.notices)
        self.notices[3] = newer
        (self.folder / "notices.json").write_text(json.dumps({"notices": self.notices}, ensure_ascii=False), encoding="utf-8")
        fake = FakeRun()
        code, log = self.run_cli(fake)
        self.assertEqual(self.card_ids(), [CTL + "100"])
        self.assertIn("[취소 공지] mju_ctl/ctl/792/104", log, "취소 공지는 원래 카드를 사람이 확인하도록 알린다")
        self.assertIn("게시일이 없어 뺌", log)
        self.assertIn("판이 달라 뺌", log, "분석과 원문의 판이 다르면 부르지 않는다")
        self.assertEqual(len(fake.calls), 2)

    def test_card_ids(self):
        self.assertEqual(card_id_of("mju_ctl", "ctl/792/235972"), "mju-ctl-ctl-792-235972")
        self.assertEqual(card_id_of("Mju.Notice", "a/b?c"), "mju-notice-a-b-c")
        self.assertIsNone(card_id_of("", "---"))
        self.assertEqual(len(card_id_of("x" * 40, "y" * 40)), 64)

    def test_bundles_flow_through_the_verifier_and_exporter(self):
        self.run_cli(FakeRun())
        data = json.loads((self.folder / "card_bundles.json").read_text(encoding="utf-8"))
        verified, report = verify_bundles(data, read_originals(self.folder / "notices.json"))
        self.assertTrue(any(item["result"] == "fail" for item in report), "예시 출력의 근거는 이 공지에 없어 검증에서 걸린다")
        verified_path = self.folder / "verified.json"
        verified_path.write_text(json.dumps(verified, ensure_ascii=False), encoding="utf-8")
        bundles, generated_at = read_bundles(verified_path)
        boards = boards_from_sources(load_sources(PIPELINE / "config/sources.toml"))
        cards, _ = build_cards_json(bundles, boards, generated_at)
        self.assertEqual(sorted(card["id"] for card in cards["cards"]), [CTL + "100", "mju-startup-ctl-792-103"],
                         "매일 추출 묶음이 내보내기를 통과한다")
        team = next(card for card in cards["cards"] if card["id"] == CTL + "100")["fields"]["team"]
        self.assertEqual((team["status"], team["evidence"]), ("needs_check", []), "근거 대조에 실패한 근거는 공개되지 않는다")


if __name__ == "__main__":
    unittest.main()
