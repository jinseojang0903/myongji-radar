"""매일 공개(#19 뒷단, `python -m publisher`) 시험. 실제 claude는 부르지 않는다.
실제 수집기 규칙 분석(analyze_file)으로 만든 analysis.json에서 cards.json까지 돌린다.
(unittest discover -s tests로 돌린다: 시험 도우미를 test_extractor에서 가져온다)"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector.analysis import analyze_file
from extractor import claude as claude_cli
from publisher import __main__ as publisher
from test_extractor import INIT, FakeRun, stream

PIPELINE = Path(__file__).resolve().parents[1]
SECRET = "비밀 본문 010-1234-5678"


def collected(number, title, body, source_id="mju_ctl"):
    return {"source_id": source_id, "external_id": f"ctl/792/{number}", "version_id": number,
            "url": f"https://ctl.mju.ac.kr/bbs/ctl/792/{number}/artclView.do", "title": title,
            "published_at": "2026-10-01T10:00:00+09:00", "body_text": body, "images": [], "attachments": []}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()
        self.cards = Path(self.temp.name) / "site" / "cards.json"
        self.write_notices([collected(100, "AI 리터러시 경진대회 모집 안내", "공모전 참가자 모집 " + SECRET),
                            collected(103, "창업 아이디어 공모전 참가 신청 안내", "공모전 신청 안내", source_id="mju_startup")])

    def tearDown(self):
        self.temp.cleanup()

    def write_notices(self, notices, analyze=True):
        (self.data / "notices.json").write_text(json.dumps({"schema_version": 1, "notices": notices}, ensure_ascii=False),
                                                encoding="utf-8")
        if analyze:
            with contextlib.redirect_stdout(io.StringIO()):
                analyze_file(self.data / "notices.json", PIPELINE / "config/rules.toml", self.data)

    def run_cli(self, fake, *extra):
        clean_env = {key: value for key, value in os.environ.items() if key not in claude_cli.ACCOUNT_ENV}
        stderr = io.StringIO()
        with mock.patch.object(claude_cli.subprocess, "run", fake), \
                mock.patch.object(claude_cli, "resolve_claude", return_value="claude.exe"), \
                mock.patch.dict(os.environ, clean_env, clear=True), contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(io.StringIO()):
            code = publisher.main(["--model", "sonnet", "--data-dir", str(self.data), "--out", str(self.cards), *extra])
        return code, stderr.getvalue()

    def logs(self):
        return [json.loads(line) for path in sorted((self.data / "publish_logs").glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines()]

    def test_all_stages_write_cards(self):
        code, log = self.run_cli(FakeRun())
        self.assertEqual(code, 1, "예시 출력의 이미지 근거 파일이 공지에 없어 내보내기가 경고(1)를 낸다. 1도 정상 종료")
        self.assertIn("[공개 준비]", log)
        self.assertIn("내보내기 경고 있음", log)
        cards = json.loads(self.cards.read_text(encoding="utf-8"))["cards"]
        self.assertEqual(sorted(card["id"] for card in cards), ["mju-ctl-ctl-792-100", "mju-startup-ctl-792-103"])
        entry = self.logs()[-1]
        self.assertEqual([stage["stage"] for stage in entry["stages"]], ["check", "extract", "verify", "export"])
        self.assertEqual((entry["cards"], entry["bundles"], entry["stopped"]), (2, 2, None))
        self.assertEqual((entry["extract"]["candidates"], entry["extract"]["calls"], entry["extract"]["calls_failed"]), (2, 2, 0),
                         "매일 추출의 건수를 단계 기록에 함께 남긴다")
        self.run_cli(FakeRun())
        self.assertEqual(len(self.logs()), 2, "같은 날 다시 돌리면 이어 쓴다")
        self.assertNotIn(SECRET, json.dumps(self.logs(), ensure_ascii=False), "단계 기록에 공지 내용이 없다")

    def test_stale_analysis_stops_before_calling(self):
        self.write_notices([collected(100, "AI 리터러시 경진대회 모집 안내", "바뀐 본문")], analyze=False)
        fake = FakeRun()
        code, log = self.run_cli(fake)
        self.assertEqual((code, fake.calls), (2, []), "규칙 분석이 지금 원문으로 만든 것이 아니면 부르지 않는다")
        self.assertIn("collector analyze를 다시", log)
        self.assertFalse(self.cards.exists())
        (self.data / "analysis.json").unlink()
        self.assertEqual(self.run_cli(FakeRun())[0], 2)

    def test_a_failed_stage_keeps_yesterdays_cards(self):
        self.cards.parent.mkdir(parents=True)
        self.cards.write_text('{"어제": true}', encoding="utf-8")
        fake = FakeRun(stream(init={**INIT, "tools": ["Bash"]}))
        code, log = self.run_cli(fake)
        self.assertEqual(code, 2, "추출이 멈추면 다음 단계를 돌리지 않는다")
        self.assertEqual(self.cards.read_text(encoding="utf-8"), '{"어제": true}')
        self.assertEqual([stage["stage"] for stage in self.logs()[-1]["stages"]], ["check", "extract"])
        with mock.patch.object(publisher, "verify_main", return_value=2):
            code, _ = self.run_cli(FakeRun())
        self.assertEqual(code, 2, "근거 대조가 실패하면 내보내지 않는다")
        self.assertEqual(self.cards.read_text(encoding="utf-8"), '{"어제": true}')
        with mock.patch.object(publisher, "verify_main", side_effect=RuntimeError(SECRET)):
            code, log = self.run_cli(FakeRun())
        self.assertEqual(code, 2)
        self.assertNotIn(SECRET, log, "예상 못 한 오류의 메시지를 찍지 않는다")
        self.assertEqual(self.logs()[-1]["stages"][-1], {"stage": "verify", "code": None, "error": "RuntimeError",
                                                         "seconds": self.logs()[-1]["stages"][-1]["seconds"]})

    def test_guards(self):
        site = Path(self.temp.name) / "site"  # 진짜 site/를 건드리지 않게 임시 폴더를 공개 폴더로 본다
        site_data = site / "data" / "publisher-test"
        with mock.patch.object(publisher, "SITE", site):
            code, log = self.run_cli(FakeRun(), "--data-dir", str(site_data))
        self.assertEqual(code, 2, "데이터 폴더(정리 전 인용·연락처)를 공개 폴더 안에 두지 않는다")
        self.assertFalse(site_data.exists())
        self.assertEqual(publisher.SITE, PIPELINE.parent / "site", "실제 공개 폴더 위치")

        def changed_during_extract(argv):
            (self.data / "notices.json").write_text('{"notices": []}', encoding="utf-8")
            return 0

        with mock.patch.object(publisher, "extract_main", side_effect=changed_during_extract):
            code, log = self.run_cli(FakeRun())
        self.assertEqual(code, 2, "추출하는 동안 원문이 바뀌면 근거 대조 전에 멈춘다")
        self.assertEqual([stage["stage"] for stage in self.logs()[-1]["stages"]], ["check", "extract"])

    def test_interruptions_and_log_failures(self):
        with mock.patch.object(publisher, "extract_main", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli(FakeRun())
        self.assertEqual(self.logs()[-1]["stopped"], "중단됨 (예외·Ctrl+C)", "중단돼도 기록을 남긴다")
        with mock.patch.object(publisher, "_write_log", side_effect=OSError("디스크")):
            code, log = self.run_cli(FakeRun())
        self.assertEqual(code, 1, "기록을 못 써도 cards.json을 쓴 결과는 그대로 알린다")
        self.assertIn("단계 기록을 쓰지 못함", log)
        self.assertNotIn("디스크", log)
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(FakeRun(), "--max-new", "x")
        self.assertEqual(caught.exception.code, 2, "인자 오류는 argparse의 2")

    def test_no_cards_is_a_stop(self):
        self.write_notices([collected(101, "취업 특강 안내", "특강")])
        code, log = self.run_cli(FakeRun())
        self.assertEqual(code, 2, "내보낼 카드가 0장이면 내보내기가 2로 멈춘다 (상류 장애로 본다)")
        self.assertEqual(self.logs()[-1]["stages"][-1]["stage"], "export")
        self.assertFalse(self.cards.exists())


if __name__ == "__main__":
    unittest.main()
