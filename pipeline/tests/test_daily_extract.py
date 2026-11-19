"""매일 추출(#14, `python -m extractor daily`) 시험. 실제 claude는 부르지 않는다.
실제 규칙 분석기로 만든 analysis.json → 매일 추출 → 검증기(#15) → 내보내기(#17)까지 이어 본다.
(unittest discover -s tests로 돌린다: 시험 도우미를 test_extractor에서 가져온다)"""
import base64
import contextlib
import hashlib
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
from collector.models import Notice
from collector.repository import Repository
from exporter.__main__ import read_verified_bundles
from exporter.cards import boards_from_sources, build_cards_json
from collector.asset_text import attachment_texts
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
PNG = claude_cli.PROBE_IMAGE.data


def store(folder: Path, data: bytes, **extra) -> dict:
    """수집기(collector.assets)가 남기는 이미지 기록과 파일."""
    digest = hashlib.sha256(data).hexdigest()
    target = folder / "assets" / f"{digest}.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"url": f"https://ctl.mju.ac.kr/{digest[:8]}.png", "alt": "포스터", "sha256": digest,
            "local_path": f"assets/{digest}.bin", "size_bytes": len(data), "download_status": "downloaded",
            "text_status": "needs_review", "text_reason": "image_requires_visual_check"} | extra


def stdin_message(call: dict) -> dict:
    """이미지를 넣은 호출의 표준 입력 (stream-json 사용자 메시지 한 줄)."""
    lines = call["input"].decode("utf-8").rstrip("\n").split("\n")
    assert len(lines) == 1, "메시지 한 줄"
    return json.loads(lines[0])


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

    def add_attachment(self, notice, name, text, relative=None):
        relative = relative or f"asset_text/{hashlib.sha256((notice['external_id'] + name + text).encode()).hexdigest()}.txt"
        target = self.folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        notice["attachments"].append({"url": "https://ctl.mju.ac.kr/file.hwp", "name": name, "sha256": "0" * 64,
                                      "text_status": "extracted", "text_path": relative})

    def test_attachment_text_goes_to_ai_and_its_name_to_the_bundle(self):
        self.add_attachment(self.notices[0], "공고문.hwp", "첨부에만 있는 조건: 졸업유예생 제외")
        self.add_attachment(self.notices[0], "공고문.hwp", "두 번째 같은 이름")
        self.add_attachment(self.notices[0], "", "이름 없는 첨부")
        self.add_attachment(self.notices[0], "밖.hwp", "data 밖", relative="../outside.txt")
        self.notices[0]["attachments"].append({"url": "https://ctl.mju.ac.kr/poster.pdf", "name": "포스터.pdf",
                                               "text_status": "needs_review", "text_reason": "no_text_or_image_only"})
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual(code, 0)
        sent = "".join(call["input"].decode("utf-8") for call in fake.calls)
        self.assertIn('<attachment name="공고문.hwp">\n첨부에만 있는 조건: 졸업유예생 제외\n</attachment>', sent)
        self.assertIn('<attachment name="공고문.hwp (2)">', sent, "같은 이름은 구분해 근거가 한 첨부만 가리키게")
        self.assertIn('<attachment name="첨부 3">', sent)
        self.assertNotIn("data 밖", sent, "data 폴더 밖 경로는 읽지 않는다")
        self.assertNotIn("포스터.pdf", sent, "글자를 뽑지 못한 첨부는 넣지 않는다")
        self.assertEqual(self.bundles()[0]["notices"][0]["files"], ["공고문.hwp", "공고문.hwp (2)", "첨부 3"])
        # 검증기는 같은 이름·같은 글자로 대조한다
        original = read_originals(self.folder / "notices.json")[self.notices[0]["url"]]
        self.assertEqual(original["attachments"][0], {"name": "공고문.hwp", "text": "첨부에만 있는 조건: 졸업유예생 제외"})

    def test_attachment_names_do_not_shift_when_another_attachment_is_converted_later(self):
        self.notices[0]["attachments"].append({"url": "https://ctl.mju.ac.kr/a.hwp", "name": "R&D <안내>.hwp",
                                               "text_status": "needs_review"})
        self.add_attachment(self.notices[0], "R&D <안내>.hwp", "두 번째 파일")
        self.add_attachment(self.notices[0], "본선진출팀 명단.hwp", "김철수 010-0000-0000")
        names = [name for name, _ in attachment_texts(self.notices[0], self.folder)]
        self.assertEqual(names[0], "R＆D ＜안내＞.hwp (2)",
                         "글자를 못 뽑은 첫 파일도 이름 순서에 들어가, 나중에 변환돼도 두 번째 파일의 이름이 바뀌지 않는다")
        self.write_inputs()
        fake = FakeRun()
        self.run_cli(fake)
        sent = "".join(call["input"].decode("utf-8") for call in fake.calls)
        self.assertNotIn("김철수", sent)
        self.assertEqual(self.bundles()[0]["notices"][0]["files"], ["R＆D ＜안내＞.hwp (2)"], "AI에 넣은 첨부 이름만 남긴다")

    def test_notices_without_cards_go_before_stale_cards(self):
        self.run_cli(FakeRun())  # 100·103 카드가 생김
        for card_id in ("100", "103"):
            cache = self.folder / "extractions" / f"{CTL if card_id == '100' else 'mju-startup-ctl-792-'}{card_id}.json"
            entry = json.loads(cache.read_text(encoding="utf-8"))
            entry.pop("inputs")  # 첨부를 넣기 전 결과 (옛 결과로 공개 중)
            cache.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        self.notices.append(collected(104, "새 공모전 참가자 모집 안내", "공모전 모집", published_at="2026-09-01T10:00:00+09:00"))
        self.write_inputs()
        fake = FakeRun()
        self.run_cli(fake, "--max-new", "1")
        self.assertIn("새 공모전", fake.calls[1]["input"].decode("utf-8"), "게시일이 이르더라도 카드가 없는 공지가 먼저")

    def test_body_only_results_are_re_extracted_with_attachments(self):
        self.run_cli(FakeRun())
        cache = self.folder / "extractions" / f"{CTL}100.json"
        entry = json.loads(cache.read_text(encoding="utf-8"))
        entry.pop("inputs")  # 첨부를 넣기 전(본문만)에 뽑은 결과
        cache.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (0, 2), "확인 호출 + 본문만으로 뽑은 1건을 다시")
        self.assertIn(CTL + "100", self.card_ids(), "다시 뽑는 동안에도 카드는 남는다")

    def test_images_flag_sends_posters_and_records_their_names(self):
        """포스터 이미지(#14): 켜면 포스터가 있는 공지만 다시 뽑고, 이름을 묶음의 files에 남긴다."""
        self.notices[0]["images"].append(store(self.folder, PNG))
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (0, 3))
        self.assertTrue(all("--input-format" not in call["command"] for call in fake.calls), "끄면(기본) 글만 넣는다")

        fake = FakeRun()
        code, _ = self.run_cli(fake, "--images")
        self.assertEqual((code, len(fake.calls)), (0, 2), "켜면 확인 호출(시험 이미지) + 포스터가 있는 공지만 다시")
        content = stdin_message(fake.calls[1])["message"]["content"]
        self.assertIn('<image name="이미지 1.png" />', content[0]["text"], "입력 글에도 이름")
        self.assertEqual(base64.b64decode(content[2]["source"]["data"]), PNG)
        first = next(bundle for bundle in self.bundles() if bundle["card_id"] == CTL + "100")
        self.assertEqual(first["notices"][0]["files"], ["이미지 1.png"], "검증기가 근거의 이미지 이름을 이 목록으로 확인한다")

        fake = FakeRun()
        self.assertEqual(self.run_cli(fake, "--images")[0], 0)
        self.assertEqual(fake.calls, [], "같은 입력이면 다시 부르지 않는다")
        fake = FakeRun()
        self.run_cli(fake)
        self.assertEqual(len(fake.calls), 2, "다시 끄면 이미지로 뽑은 결과를 이미지 없이 다시 뽑는다")

    def test_expected_inputs_leave_out_result_notice_images(self):
        """결과 공지는 후보에서 이미 빠지지만(select_candidates), 입력 종류 판정도 결과 공지 이미지를 넣지 않는다."""
        notice = {"title": "AI 경진대회 수상자 발표", "images": [store(self.folder, PNG)]}
        self.assertNotIn("images-v1", daily.expected_inputs(notice, True), "명단 사진일 수 있다")
        self.assertIn("images-v1", daily.expected_inputs({"title": "AI 경진대회 모집", "images": notice["images"]}, True))
        self.assertNotIn("images-v1", daily.expected_inputs({"title": "AI 경진대회 모집", "images": notice["images"]}, False))

    def test_budget_skip_preserves_same_version_image_result_but_not_a_changed_image(self):
        image = {"url": "https://test.mju.ac.kr/poster.jpg", "sha256": "a" * 64,
                 "download_status": "skipped", "download_reason": "run_bytes"}
        notice = {"version_id": 1, "title": "공모전 모집", "body_text": "", "attachments": [], "images": [image]}
        entry = {"version_id": 1, "model": "opus", "prompt_version": daily.PROMPT_VERSION,
                 "prompt_fingerprint": "same", "input_hash": daily.content_hash(notice),
                 "inputs": daily.INPUTS + [daily.IMAGES_INPUT],
                 "attachment_input_hash": daily.EMPTY_ATTACHMENT_HASH}
        def current(use_images=True, attachment_hash=daily.EMPTY_ATTACHMENT_HASH):
            return daily.is_current(entry, notice, "opus", "same", use_images, attachment_hash=attachment_hash)
        self.assertTrue(current())
        self.assertFalse(current(use_images=False))
        self.assertFalse(current(attachment_hash="changed"))
        image["download_reason"] = "url_policy"
        self.assertFalse(current(), "영구 차단은 이번 실행 예산으로 놓친 경우와 다르다")
        image.update(download_reason="run_bytes", sha256="b" * 64)
        self.assertFalse(current(), "파일이 달라진 경우 옛 값을 현재 값으로 취급하지 않는다")

    def test_missing_image_file_is_reported_and_not_retried_every_day(self):
        record = store(self.folder, PNG)
        (self.folder / record["local_path"]).unlink()  # 기록은 있는데 파일이 없다
        self.notices[0]["images"].append(record)
        self.write_inputs()
        fake = FakeRun()
        code, log = self.run_cli(fake, "--images")
        self.assertEqual(code, 0)
        self.assertIn(f"[이미지 없음] {CTL}100", log)
        self.assertTrue(all("--input-format" not in call["command"] for call in fake.calls[1:]), "글만 넣는다")
        first = next(bundle for bundle in self.bundles() if bundle["card_id"] == CTL + "100")
        self.assertEqual(first["notices"][0]["files"], [], "넣지 못한 이미지는 근거 파일이 될 수 없다")
        run = json.loads((self.folder / "extractions" / "_runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((run["images"], run["image_calls"], run["images_missing"]), (True, 0, 1))
        fake = FakeRun()
        self.run_cli(fake, "--images")
        self.assertEqual(fake.calls, [], "매일 다시 부르지 않는다 (파일이 바뀌면 내용 해시가 바뀌어 다시 뽑는다)")

    def test_images_with_cross_model_send_the_same_input_to_both_models(self):
        self.notices[0]["images"].append(store(self.folder, PNG))
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake, "--images", "--cross-model", "haiku")
        self.assertEqual(code, 0)
        with_image = [call for call in fake.calls if "--input-format" in call["command"]]
        models = [call["command"][call["command"].index("--model") + 1] for call in with_image]
        self.assertEqual(sorted(models), ["haiku", "haiku", "sonnet", "sonnet"], "주·교차 모두 확인 호출 + 포스터 공지 1건")
        first = next(bundle for bundle in self.bundles() if bundle["card_id"] == CTL + "100")
        self.assertIsNotNone(first["cross_extraction"], "주·교차 결과의 입력 종류가 같아 교차 결과를 쓴다")

    def test_failures_are_counted_per_input_kind(self):
        """이미지 없이 3번 실패해 포기한 공지도 이미지를 켜면 다시 부른다 (입력이 다르다)."""
        self.notices[0]["images"].append(store(self.folder, PNG))
        self.write_inputs()
        notice = self.notices[0]
        failures = {CTL + "100": {"version_id": notice["version_id"], "model": "sonnet",
                                  "prompt_fingerprint": daily.prompt_fingerprint(), "input_hash": daily.content_hash(notice),
                                  "count": daily.MAX_FAILURES, "last_error": "형식"}}
        (self.folder / "extractions").mkdir()
        (self.folder / "extractions" / daily.FAILURES_FILE).write_text(json.dumps(failures), encoding="utf-8")
        fake = FakeRun()
        _, log = self.run_cli(fake)
        self.assertIn(f"[포기] {CTL}100", log, "입력 종류가 없는 옛 기록은 글만 넣던 때 것")
        fake = FakeRun()
        _, log = self.run_cli(fake, "--images")
        self.assertNotIn(f"[포기] {CTL}100", log)
        self.assertTrue(any("--input-format" in call["command"] for call in fake.calls[1:]), "이미지와 함께 다시 부른다")

    def test_parser_only_change_keeps_the_card_and_re_extracts(self):
        """파서를 고쳐 같은 판(번호)의 본문 글자만 바뀌면 카드를 내리지 않고 다시 뽑는다. 원문이 바뀐 새 판은 내린다."""
        self.run_cli(FakeRun())
        self.notices[0]["body_text"] = self.notices[0]["body_text"].replace("~", " ~ ")  # 같은 판, 파서만 다름
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake, "--max-new", "1")
        self.assertEqual((code, len(fake.calls)), (0, 2), "확인 호출 + 바뀐 1건을 다시")
        self.assertIn(CTL + "100", self.card_ids())
        self.notices[0]["version_id"] = 9  # 원문이 바뀐 새 판
        self.notices[3]["body_text"] += " 바뀜"
        self.notices[3]["version_id"] = 9
        self.write_inputs()
        self.run_cli(FakeRun(stream(), stream(output=None), stream(output=None)), "--max-new", "1")
        self.assertNotIn("mju-startup-ctl-792-103", self.card_ids(), "새 판을 다시 뽑지 못하면 카드를 내린다")

    def test_newest_notices_are_extracted_first(self):
        self.notices[3]["published_at"] = "2026-11-10T10:00:00+09:00"  # 창업 공모전이 더 최신
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake, "--max-new", "1")
        self.assertEqual((code, len(fake.calls)), (0, 2))
        self.assertIn("창업 아이디어 공모전", fake.calls[1]["input"].decode("utf-8"), "상한에 걸리면 최신 공지부터")

    def test_cross_model_extracts_again_after_the_main_model(self):
        """교차 검증(#15): 주 모델 다음에 같은 입력을 다른 모델로 한 번 더. 묶음에 결과와 모델을 남긴다."""
        fake = FakeRun()
        code, _ = self.run_cli(fake, "--cross-model", "haiku")
        self.assertEqual(code, 0)
        models = [call["command"][call["command"].index("--model") + 1] for call in fake.calls]
        self.assertEqual(models, ["sonnet"] * 3 + ["haiku"] * 3, "주 모델 확인 호출 + 2건, 교차 모델 확인 호출 + 2건")
        first = self.bundles()[0]
        self.assertEqual(first["cross_extraction"], EXAMPLE)
        self.assertEqual(first["meta"]["cross_model"], "claude-haiku-test")
        fake = FakeRun()
        self.assertEqual((self.run_cli(fake, "--cross-model", "haiku")[0], fake.calls), (0, []), "둘 다 뽑아 뒀으면 부르지 않는다")
        self.run_cli(FakeRun())
        self.assertIsNone(self.bundles()[0]["cross_extraction"], "교차 모델을 주지 않은 실행의 묶음에는 교차 결과가 없다")

    def test_cross_model_waits_when_the_daily_limit_is_used_by_the_main_model(self):
        fake = FakeRun()
        code, log = self.run_cli(fake, "--cross-model", "haiku", "--max-calls", "3")
        self.assertEqual((code, len(fake.calls)), (0, 3), "주 모델(확인 + 2건)이 상한을 다 쓰면 교차 검증은 다음 실행에서")
        self.assertIn("교차 검증 2건은 오늘 호출 상한", log)
        self.assertIsNone(self.bundles()[0]["cross_extraction"])

    def test_cross_failures_are_given_up_and_cross_stop_does_not_block_bundles(self):
        failing = lambda: FakeRun(stream(), stream(), stream(), stream(), stream(output=None), stream(output=None))
        for _ in range(3):
            code, _ = self.run_cli(failing(), "--cross-model", "haiku")
            self.assertEqual(code, 0, "교차 호출이 형식에 못 맞춰도 주 결과로 공개한다")
        fake = FakeRun()
        self.run_cli(fake, "--cross-model", "haiku")
        self.assertEqual(fake.calls, [], "같은 판을 교차 모델로 3번 실패한 카드는 더 부르지 않는다 (매일 상한을 먹지 않게)")
        # 교차 모델의 확인 호출이 멈춰도(격리 어긋남 등) 주 결과로 묶음은 만든다
        self.notices[0]["version_id"] = 2
        self.write_inputs()
        open_tools = stream(init={**INIT, "tools": ["Bash"]})
        code, log = self.run_cli(FakeRun(stream(), stream(), open_tools), "--cross-model", "opus")
        self.assertEqual(code, 0)
        self.assertIn("[교차 검증 멈춤]", log)
        self.assertEqual(len(self.bundles()), 2)
        self.assertTrue(all(bundle["cross_extraction"] is None for bundle in self.bundles()))

    def test_cross_model_must_differ_from_the_main_model(self):
        fake = FakeRun()
        code, log = self.run_cli(fake, "--cross-model", "sonnet")
        self.assertEqual((code, fake.calls), (2, []), "같은 모델끼리는 교차 검증이 아니다: 부르기 전에 멈춘다")
        self.assertIn("--cross-model은 --model과 다른 모델", log)

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

    def test_daily_persists_models_before_cache_is_overwritten(self):
        repo = Repository(self.folder)
        try:
            for original in self.notices:
                repo.save(Notice(**{key: original[key] for key in
                                   ("source_id", "external_id", "url", "title", "published_at", "body_text", "images", "attachments")}),
                          b"original")
            repo.export(self.folder / "notices.json")
            self.notices = json.loads((self.folder / "notices.json").read_text(encoding="utf-8"))["notices"]
            self.write_inputs()
            self.assertEqual(self.run_cli(FakeRun())[0], 0)
            self.assertEqual(self.run_cli(FakeRun(), "--model", "opus")[0], 0)
            fake = FakeRun()
            self.assertEqual(self.run_cli(fake, "--model", "opus")[0], 0)
            self.assertEqual(fake.calls, [])
            self.assertEqual(repo.db.execute("SELECT model, COUNT(*) FROM analyses GROUP BY model ORDER BY model").fetchall(),
                             [("opus", 2), ("sonnet", 2)])
            self.assertEqual({row[0] for row in repo.db.execute("SELECT DISTINCT model_id FROM analyses")}, {"claude-haiku-test"})
            exported = self.bundles()[0]["notices"][0]
            self.assertIsNotNone(exported["last_seen_at"])
        finally:
            repo.close()

    def test_same_version_with_changed_parser_input_is_extracted_again(self):
        self.run_cli(FakeRun())
        self.notices[0]["body_text"] += " 신청마감 변경"
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (0, 2), "원문 판 번호가 같아도 파서 입력이 바뀌면 확인 호출+재추출")

    def test_parser_input_changes_reset_previous_failures(self):
        for _ in range(3):
            self.run_cli(FakeRun(stream(), stream(output=None), stream()))
        fake = FakeRun()
        self.run_cli(fake)
        self.assertEqual(fake.calls, [], "같은 입력을 세 번 실패하면 더 부르지 않는다")
        self.notices[0]["body_text"] += " 파서가 새로 복원한 신청 조건"
        self.write_inputs()
        fake = FakeRun()
        code, _ = self.run_cli(fake)
        self.assertEqual((code, len(fake.calls)), (0, 2), "판 번호가 같아도 입력이 바뀌면 이전 실패를 넘기지 않는다")
        failures = json.loads((self.folder / "extractions" / daily.FAILURES_FILE).read_text(encoding="utf-8"))
        self.assertNotIn(CTL + "100", failures, "새 입력의 추출 성공은 예전 실패 기록을 지운다")

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
        bundles, generated_at, unverified = read_verified_bundles(verified_path)
        self.assertEqual(unverified, [], "검증기 출력은 모두 검증 표시가 맞는다")
        boards = boards_from_sources(load_sources(PIPELINE / "config/sources.toml"))
        cards, _ = build_cards_json(bundles, boards, generated_at)
        self.assertEqual(sorted(card["id"] for card in cards["cards"]), [CTL + "100", "mju-startup-ctl-792-103"],
                         "매일 추출 묶음이 내보내기를 통과한다")
        team = next(card for card in cards["cards"] if card["id"] == CTL + "100")["fields"]["team"]
        self.assertEqual((team["status"], team["evidence"]), ("needs_check", []), "근거 대조에 실패한 근거는 공개되지 않는다")


if __name__ == "__main__":
    unittest.main()
