"""#16 통합: 실제 조사표의 출처/날짜를 사용한 합성 추출 fixture. 학교·AI 호출 없음."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from collector.analysis import analyze_file
from collector.locking import CollectorLock
from collector.models import Notice
from collector.repository import Repository, content_hash
from exporter.__main__ import main as export_main
from extractor.daily import bundle_of, card_id_of
from merger import __main__ as merger
from merger.evidence import deadline_is_grounded
from merger.groups import load_groups
from publisher import __main__ as publisher
from verifier.__main__ import main as verify_main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT.parent / "prompts/extract_v0.2/example_output.json").read_text(encoding="utf-8"))
with (ROOT.parent / "eval/answer_key/sources.csv").open(encoding="utf-8-sig") as source_file:
    SOURCES = list(csv.DictReader(source_file))
STAMP = "2026-11-07T21:00:00+09:00"


def fixture(contest, number, source_id, end, *, kind="모집", version=1):
    source = next(s for s in SOURCES if s["contest_id"] == contest and s["notice"] == str(number))
    # 아래 본문은 시험용이며 원문 전문이나 실제 AI 출력이 아니다.
    body = "역사 영상 공모전. 대학혁신지원사업단 교내 모집 신청기한 연장. 참가 신청서 제출 기한: " + end
    notice = {"source_id": source_id, "external_id": source["url"].split("/bbs/", 1)[1].split("/artclView.do", 1)[0],
              "url": source["url"], "title": source["title"], "published_at": source["posted"], "version_id": version,
              "version_number": 1, "body_text": body, "images": [], "attachments": []}
    extraction = deepcopy(EXAMPLE)
    for value in extraction["fields"].values():
        value.update(value=None, flag="missing", evidence=[], note=None, alternatives=[])
    def found(value, quote):
        return {"value": value, "flag": "found", "evidence": [{"quote": quote, "notice": 1, "location": "본문", "file": None}],
                "note": None, "alternatives": []}
    for name, value in (("title", "역사 영상 공모전"), ("organizer", "대학혁신지원사업단"), ("scope", "교내"), ("kind", kind)):
        quote = "신청기한 연장" if kind == "연장" and name == "kind" else "교내 모집" if name == "scope" else value
        extraction["fields"][name] = found(value, quote)
    extraction["fields"]["apply"] = found({"start": None, "end": {"date": end, "time": None, "year_inferred": False}},
                                             "참가 신청서 제출 기한: " + end)
    entry = {"output": extraction, "model": "sonnet", "model_id": "claude-sonnet-fixture", "prompt_version": "v0.2",
             "prompt_fingerprint": "fixture", "input_hash": content_hash(notice)}
    return notice, bundle_of(card_id_of(source_id, notice["external_id"]), notice, entry)


def refresh_notice(fixture_pair):
    """시험에서 원문을 수정한 뒤 단일 추출 묶음의 버전 정보도 맞춘다."""
    notice, bundle = fixture_pair
    bundle["notices"][0].update(title=notice["title"], published_at=notice["published_at"], input_hash=content_hash(notice))


class MergerPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / "data"
        self.data.mkdir()
        self.groups = self.data.parent / "groups.toml"
        self.out = self.data / "merged.json"

    def write_inputs(self, fixtures, group_id="history-video-5"):
        notices, bundles = zip(*fixtures)
        (self.data / "notices.json").write_text(json.dumps({"notices": notices}, ensure_ascii=False), encoding="utf-8")
        (self.data / "bundles.json").write_text(json.dumps({"generated_at": STAMP, "bundles": bundles}, ensure_ascii=False), encoding="utf-8")
        analyze_file(self.data / "notices.json", ROOT / "config/rules.toml", self.data)
        members = ",\n".join('{source_id=' + json.dumps(n["source_id"]) + ',external_id=' + json.dumps(n["external_id"]) + '}' for n in notices)
        self.groups.write_text('version=1\n[[groups]]\nid=' + json.dumps(group_id) + '\nmembers=[' + members + ']\n', encoding="utf-8")

    def run_merge(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return merger.main(["--bundles", str(self.data / "bundles.json"), "--notices", str(self.data / "notices.json"),
                                "--analysis", str(self.data / "analysis.json"), "--groups", str(self.groups), "--out", str(self.out), *extra])

    def test_history_video_source_case_roundtrip_and_no_private_state_export(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30"),
                           fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)])
        self.assertEqual(self.run_merge(), 0)
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(len(merged["bundles"]), 1)
        bundle = merged["bundles"][0]
        self.assertEqual(bundle["card_id"], "history-video-5")
        self.assertEqual([(e["old"], e["new"]) for e in bundle["deadline_history"]], [("2026-09-30", "2026-10-23")])
        self.assertEqual(self.run_merge(), 0)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]["deadline_history"], bundle["deadline_history"])
        verified, cards = self.data / "verified.json", self.data.parent / "cards.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"), "--out", str(verified)]), 0)
            self.assertIn(export_main(["--bundles", str(verified), "--out", str(cards)]), (0, 1))
        public = cards.read_text(encoding="utf-8")
        self.assertEqual(len(json.loads(public)["cards"]), 1)
        for private in ("merge_state", "input_hash", "version_number", "merge_prompt_fingerprint"):
            self.assertNotIn(private, public)

    def test_ai_literacy_same_url_versions_and_reextract_do_not_fake_change(self):
        old_notice, old_bundle = fixture("ai-literacy-2026", 1, "mju_ctl", "2026-10-21")
        self.write_inputs([(old_notice, old_bundle)], "ai-literacy-2026")
        self.assertEqual(self.run_merge(), 0)
        # 합성 원문 수정. 실제 AI 리터러시 마감이 10/23이었다고 주장하는 시험이 아니다.
        new_notice, new_bundle = fixture("ai-literacy-2026", 1, "mju_ctl", "2026-10-23", version=2)
        new_notice["version_number"] = 2
        new_bundle["notices"][0]["version_number"] = 2
        self.write_inputs([(new_notice, new_bundle)], "ai-literacy-2026")
        self.assertEqual(self.run_merge(), 0)
        history = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]["deadline_history"]
        self.assertEqual(len(history), 1)
        # 원문/판을 유지한 채 추출값만 바꾼 경우는 새 변경 이력이 아니다.
        new_bundle["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-10-24"
        self.write_inputs([(new_notice, new_bundle)], "ai-literacy-2026")
        self.assertIn(self.run_merge(), (0, 1))
        document = json.loads(self.out.read_text(encoding="utf-8"))
        after = document["bundles"][0]
        self.assertEqual(after["deadline_history"], [])
        self.assertEqual([event["change"] for event in document["merge_state"]["cards"]["ai-literacy-2026"]["events"]], history)
        self.assertEqual(document["merge_report"]["history_review"], 1)
        self.assertIn(after["extraction"]["fields"]["apply"]["flag"], ("conflict", "needs_check"))

    def test_bad_or_stale_input_and_busy_lock_leave_output_unchanged(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.assertEqual(self.run_merge(), 0)
        before = self.out.read_bytes()
        with CollectorLock(self.data):
            self.assertEqual(self.run_merge(), 2)
        self.assertEqual(self.out.read_bytes(), before)
        (self.data / "notices.json").write_text('{"notices":[]}', encoding="utf-8")
        self.assertEqual(self.run_merge(), 2)
        self.assertEqual(self.out.read_bytes(), before)

    def test_publisher_invalid_merge_config_never_calls_ai_and_holds_lock(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.groups.write_text('version=1\ngroups="bad"', encoding="utf-8")
        with patch.object(publisher, "extract_main") as extract, contextlib.redirect_stderr(io.StringIO()):
            code = publisher.main(["--model", "fixture", "--data-dir", str(self.data), "--out", str(self.data.parent / "cards.json"),
                                   "--merge-contests", "--groups", str(self.groups)])
        self.assertEqual(code, 2)
        extract.assert_not_called()
        self.assertFalse((self.data.parent / "cards.json").exists())

    def publish_without_ai(self, *extra):
        def extract(argv):
            from collector.locking import AlreadyRunning
            with self.assertRaises(AlreadyRunning), CollectorLock(self.data):
                pass
            target = Path(argv[argv.index("--out") + 1])
            target.write_bytes((self.data / "bundles.json").read_bytes())
            return 0
        with patch.object(publisher, "extract_main", side_effect=extract), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return publisher.main(["--model", "fixture", "--data-dir", str(self.data), "--out", str(self.data.parent / "cards.json"),
                                   "--merge-contests", "--groups", str(self.groups), *extra])

    def prepare_blocked_and_withheld(self):
        first = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        other = fixture("ai-literacy-2026", 1, "mju_ctl", "2026-10-21")
        self.write_inputs([first, other])
        groups = []
        for group_id, (notice, _) in (("history-video-5", first), ("ai-literacy-2026", other)):
            groups.append('[[groups]]\nid=' + json.dumps(group_id) + '\nmembers=[{source_id='
                          + json.dumps(notice["source_id"]) + ',external_id=' + json.dumps(notice["external_id"]) + '}]\n')
        self.groups.write_text('version=1\n' + ''.join(groups), encoding="utf-8")
        self.assertIn(self.publish_without_ai(), (0, 1))
        cards = self.data.parent / "cards.json"
        previous = json.loads(cards.read_text(encoding="utf-8"))
        canonical = next(card for card in previous["cards"] if card["id"] == "history-video-5")
        source_id = first[1]["card_id"]
        previous["cards"].append({**deepcopy(canonical), "id": source_id})  # 옛 병합 전 공개 카드
        cards.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"] = raw["bundles"][:1]  # 다른 그룹은 원문은 있으나 추출이 없어 보류
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return cards, previous, source_id

    def prepare_only_blocked(self):
        first = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        self.write_inputs([first])
        self.assertIn(self.publish_without_ai(), (0, 1))
        cards = self.data.parent / "cards.json"
        previous = json.loads(cards.read_text(encoding="utf-8"))
        canonical = previous["cards"][0]
        source_id = first[1]["card_id"]
        previous["cards"].append({**deepcopy(canonical), "id": source_id})
        previous["cards"].append({**deepcopy(canonical), "id": "older-unrelated"})
        cards.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
        return cards, previous, source_id

    def test_publisher_opt_in_preserves_lock_and_runs_merge_verify_export(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30"),
                           fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)])
        self.assertIn(self.publish_without_ai(), (0, 1))
        cards = json.loads((self.data.parent / "cards.json").read_text(encoding="utf-8"))["cards"]
        self.assertEqual([card["id"] for card in cards], ["history-video-5"])
        self.assertEqual(len(cards[0]["deadline_history"]), 1)
        log_file = next((self.data / "publish_logs").glob("*.jsonl"))
        entry = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual([stage["stage"] for stage in entry["stages"]], ["check", "extract", "merge", "verify", "export"])

    def test_existing_merge_state_requires_merge_mode_before_push_or_ai(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge(), 0)
        before_state = self.out.read_bytes()
        fake_root = self.data.parent / "pipeline"
        cards = self.data.parent / publisher.CARDS
        cards.parent.mkdir(parents=True, exist_ok=True)
        cards.write_bytes(b'{"previous":true}')
        with patch.object(publisher, "ROOT", fake_root), patch.object(publisher, "prepare") as prepare, \
                patch.object(publisher, "extract_main", return_value=2) as extract, \
                patch.object(publisher, "push_cards") as push, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = publisher.main(["--model", "fixture", "--data-dir", str(self.data), "--out", str(cards), "--push"])
        self.assertEqual(code, 2)
        prepare.assert_not_called()
        extract.assert_not_called()
        push.assert_not_called()
        self.assertEqual(cards.read_bytes(), b'{"previous":true}')
        self.assertEqual(self.out.read_bytes(), before_state)

    def test_publisher_rejects_public_output_in_private_data_before_ai(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge(), 0)
        preserved = [self.out, self.data / "notices.json", self.data / "analysis.json", self.data / "bundles.json"]
        before = {path: path.read_bytes() for path in preserved}
        for target in (self.out, self.data / "bundles.json", self.data / "cards.json", self.data / "nested" / "cards.json"):
            with self.subTest(target=target), patch.object(publisher, "extract_main", return_value=2) as extract, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publisher.main(["--model", "fixture", "--data-dir", str(self.data), "--out", str(target),
                                       "--merge-contests", "--groups", str(self.groups)])
                self.assertEqual(code, 2)
                extract.assert_not_called()
                self.assertEqual({path: path.read_bytes() for path in preserved}, before)

    def test_exporter_does_not_overwrite_inputs_config_or_private_state(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge(), 0)
        config, blocked = self.data.parent / "sources.toml", self.data.parent / "blocked.toml"
        config.write_bytes((ROOT / "config/sources.toml").read_bytes())
        blocked.write_text("blocked = []\n", encoding="utf-8")
        other = self.data / "private.json"
        other.write_bytes(b'{"private":true}')
        preserved = (self.out, config, blocked, other)
        before = {path: path.read_bytes() for path in preserved}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for target in preserved:
                with self.subTest(target=target):
                    self.assertEqual(export_main(["--bundles", str(self.out), "--out", str(target),
                                                  "--config", str(config), "--blocked", str(blocked)]), 2)
                    self.assertEqual({path: path.read_bytes() for path in preserved}, before)
            cards = self.data / "cards.json"  # 독립 exporter는 같은 폴더의 cards.json을 허용한다.
            self.assertIn(export_main(["--bundles", str(self.out), "--out", str(cards),
                                       "--config", str(config), "--blocked", str(blocked)]), (0, 1))
        self.assertEqual([card["id"] for card in json.loads(cards.read_text(encoding="utf-8"))["cards"]], ["history-video-5"])

    def test_direct_export_rejects_unmerged_or_stale_state(self):
        self.write_inputs([fixture("history-video-5", 1, "mju_innovation", "2026-09-30")])
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge(), 0)
        cards = self.data.parent / "cards.json"
        cards.write_bytes(b'{"previous":true}')
        forged = self.data / "stale.json"
        stale = json.loads(self.out.read_text(encoding="utf-8"))
        stale["merge_state"]["cards"]["history-video-5"]["history_quarantined"] = True
        forged.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for source in (self.data / "bundles.json", forged):
                self.assertEqual(export_main(["--bundles", str(source), "--out", str(cards), "--allow-empty"]), 2)
                self.assertEqual(cards.read_bytes(), b'{"previous":true}')
            self.assertIn(export_main(["--bundles", str(self.out), "--out", str(cards)]), (0, 1))
        self.assertEqual([card["id"] for card in json.loads(cards.read_text(encoding="utf-8"))["cards"]], ["history-video-5"])

    def test_allow_empty_does_not_replace_cards_when_group_is_withheld(self):
        fixtures = [fixture("history-video-5", 1, "mju_innovation", "2026-09-30"),
                    fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)]
        self.write_inputs(fixtures)
        self.assertIn(self.publish_without_ai(), (0, 1))
        cards = self.data.parent / "cards.json"
        before = cards.read_bytes()
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"] = raw["bundles"][:1]
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.publish_without_ai("--allow-empty"), 2)
        merged = json.loads((self.data / "card_bundles.merged.json").read_text(encoding="utf-8"))
        self.assertEqual(merged["merge_report"]["withheld"], 1)
        self.assertEqual(cards.read_bytes(), before)
        verified = self.data / "withheld.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.data / "card_bundles.merged.json"),
                                          "--notices", str(self.data / "notices.json"), "--out", str(verified)]), 0)
            self.assertEqual(export_main(["--bundles", str(verified), "--out", str(cards), "--allow-empty"]), 2)
        self.assertEqual(cards.read_bytes(), before)

    def test_publisher_removes_only_blocked_group_and_its_old_source_card_when_others_held(self):
        cards, previous, source_id = self.prepare_blocked_and_withheld()
        blocked = {"history-video-5"}
        with patch.object(merger, "load_blocked", return_value=blocked), \
                patch.object(publisher, "load_blocked", return_value=blocked), \
                patch("exporter.__main__.load_blocked", return_value=blocked):
            self.assertEqual(self.publish_without_ai(), 1)
        after = json.loads(cards.read_text(encoding="utf-8"))
        expected = {**previous, "cards": [card for card in previous["cards"] if card["id"] not in blocked | {source_id}]}
        self.assertEqual(after, expected)
        self.assertEqual([card["id"] for card in after["cards"]], ["ai-literacy-2026"])

    def test_direct_export_removes_blocked_source_and_group_when_others_held(self):
        cards, previous, source_id = self.prepare_blocked_and_withheld()
        blocked = self.data.parent / "blocked.toml"
        blocked.write_text('[[blocked]]\ncard_id=' + json.dumps(source_id)
                           + '\nreason="test"\nsince=2026-11-07\n', encoding="utf-8")
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 1)
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((merged["bundles"], merged["merge_report"]["withheld"]), ([], 1))
        verified = self.data / "withheld.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            self.assertEqual(export_main(["--bundles", str(verified), "--out", str(cards),
                                          "--blocked", str(blocked), "--allow-empty"]), 1)
        after = json.loads(cards.read_text(encoding="utf-8"))
        expected = {**previous, "cards": [card for card in previous["cards"]
                                         if card["id"] not in {"history-video-5", source_id}]}
        self.assertEqual(after, expected)
        self.assertEqual([card["id"] for card in after["cards"]], ["ai-literacy-2026"])

    def test_newly_blocked_source_suppresses_merged_card_during_normal_export(self):
        cards, _, source_id = self.prepare_blocked_and_withheld()
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"].append(fixture("ai-literacy-2026", 1, "mju_ctl", "2026-10-21")[1])
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        blocked = self.data.parent / "changed-blocked.toml"
        blocked.write_text("blocked = []\n", encoding="utf-8")
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 0)
        verified = self.data / "latest.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            # 병합·검증 뒤 차단 목록이 바뀐다. 병합 ID까지 차단되어야 한다.
            blocked.write_text('[[blocked]]\ncard_id=' + json.dumps(source_id)
                               + '\nreason="test"\nsince=2026-11-07\n', encoding="utf-8")
            self.assertIn(export_main(["--bundles", str(verified), "--out", str(cards), "--blocked", str(blocked)]), (0, 1))
        self.assertEqual([card["id"] for card in json.loads(cards.read_text(encoding="utf-8"))["cards"]], ["ai-literacy-2026"])

    def test_newly_blocked_source_is_removed_even_if_other_group_is_withheld(self):
        cards, previous, source_id = self.prepare_blocked_and_withheld()
        blocked = self.data.parent / "changed-blocked.toml"
        blocked.write_text("blocked = []\n", encoding="utf-8")
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 1)
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(([bundle["card_id"] for bundle in merged["bundles"]], merged["merge_report"]["withheld"]),
                         (["history-video-5"], 1))
        verified = self.data / "latest.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            blocked.write_text('[[blocked]]\ncard_id=' + json.dumps(source_id)
                               + '\nreason="test"\nsince=2026-11-07\n', encoding="utf-8")
            self.assertEqual(export_main(["--bundles", str(verified), "--out", str(cards),
                                          "--blocked", str(blocked), "--allow-empty"]), 1)
        after = json.loads(cards.read_text(encoding="utf-8"))
        expected = {**previous, "cards": [card for card in previous["cards"]
                                         if card["id"] not in {"history-video-5", source_id}]}
        self.assertEqual(after, expected)

    def test_publisher_all_blocked_without_allow_empty_removes_only_blocked_cards(self):
        cards, previous, source_id = self.prepare_only_blocked()
        blocked = {source_id}
        with patch.object(merger, "load_blocked", return_value=blocked), \
                patch.object(publisher, "load_blocked", return_value=blocked), \
                patch("exporter.__main__.load_blocked", return_value=blocked):
            self.assertEqual(self.publish_without_ai(), 1)
        after = json.loads(cards.read_text(encoding="utf-8"))
        expected = {**previous, "cards": [card for card in previous["cards"]
                                         if card["id"] not in {"history-video-5", source_id}]}
        self.assertEqual(after, expected)
        self.assertEqual([card["id"] for card in after["cards"]], ["older-unrelated"])

    def test_first_merge_missing_member_still_expands_canonical_block_to_old_source(self):
        from exporter.cards import CardBundle, build_cards_json

        first = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        missing = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        self.write_inputs([first, missing])
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"] = raw["bundles"][:1]
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        previous, _ = build_cards_json([CardBundle(**first[1])], [], STAMP)
        old_id = first[1]["card_id"]
        previous["cards"].append({**deepcopy(previous["cards"][0]), "id": "older-unrelated"})
        cards = self.data.parent / "cards.json"
        cards.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
        blocked = self.data.parent / "blocked.toml"
        blocked.write_text('[[blocked]]\ncard_id="history-video-5"\nreason="test"\nsince=2026-11-07\n', encoding="utf-8")
        self.out = self.data / "card_bundles.merged.json"
        self.assertFalse(self.out.exists(), "이전 병합 상태가 없는 최초 실행")
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 1)
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        members = merged["merge_state"]["cards"]["history-video-5"]["members"]
        self.assertIn([first[0]["source_id"], first[0]["external_id"]], members)
        self.assertIn([missing[0]["source_id"], missing[0]["external_id"]], members)
        verified = self.data / "first-merge.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            self.assertEqual(export_main(["--bundles", str(verified), "--out", str(cards),
                                          "--blocked", str(blocked)]), 1)
        after = json.loads(cards.read_text(encoding="utf-8"))
        self.assertEqual(after, {**previous, "cards": [card for card in previous["cards"] if card["id"] != old_id]})

    def test_exporter_all_blocked_with_and_without_empty_permission(self):
        cards, previous, source_id = self.prepare_only_blocked()
        blocked = self.data.parent / "blocked.toml"
        blocked.write_text('[[blocked]]\ncard_id=' + json.dumps(source_id)
                           + '\nreason="test"\nsince=2026-11-07\n', encoding="utf-8")
        self.out = self.data / "card_bundles.merged.json"
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 0)
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((merged["bundles"], merged["merge_report"]["withheld"]), ([], 0))
        verified = self.data / "all-blocked.verified.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            args = ["--bundles", str(verified), "--out", str(cards), "--blocked", str(blocked)]
            self.assertEqual(export_main(args), 1)
            after = json.loads(cards.read_text(encoding="utf-8"))
            expected = {**previous, "cards": [card for card in previous["cards"]
                                             if card["id"] not in {"history-video-5", source_id}]}
            self.assertEqual(after, expected)
            cards.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
            self.assertIn(export_main([*args, "--allow-empty"]), (0, 1))
        self.assertEqual(json.loads(cards.read_text(encoding="utf-8"))["cards"], [])

    def test_closed_contest_requires_explicit_empty_publish_permission(self):
        first = fixture("ai-literacy-2026", 1, "mju_ctl", "2026-10-21")
        terminal = fixture("ai-literacy-2026", 3, "mju_ctl", "2026-10-21", kind="취소", version=2)
        terminal[0]["title"] = "AI 리터러시 공모전 취소 안내"
        terminal[0]["body_text"] = "AI 리터러시 공모전을 취소합니다."
        refresh_notice(terminal)
        self.write_inputs([first, terminal], "ai-literacy-2026")
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"] = raw["bundles"][:1]  # 결과 명단은 추출기에 보내지 않는다.
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        cards = self.data.parent / "cards.json"
        cards.write_text('{"previous":true}', encoding="utf-8")
        self.assertEqual(self.publish_without_ai(), 2)
        self.assertEqual(json.loads(cards.read_text(encoding="utf-8")), {"previous": True})
        self.assertIn(self.publish_without_ai("--allow-empty"), (0, 1))
        self.assertEqual(json.loads(cards.read_text(encoding="utf-8"))["cards"], [])

    def test_unmatched_or_image_evidence_cannot_create_history(self):
        old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        old[1]["extraction"]["fields"]["apply"]["evidence"][0].update(location="이미지", file="poster.png")
        old[1]["notices"][0]["files"] = ["poster.png"]
        self.write_inputs([old, latest])
        self.assertIn(self.run_merge(), (0, 1))
        merged = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(merged["bundles"][0]["deadline_history"], [])

    def test_unverified_top_level_values_withhold_group(self):
        cases = (("title", "unmatched"), ("organizer", "unmatched"), ("organizer", "image"), ("scope", "missing"))
        for index, (name, problem) in enumerate(cases):
            with self.subTest(field=name, problem=problem):
                self.out = self.data / f"unverified-top-{index}.json"
                item = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                field = item[1]["extraction"]["fields"][name]
                if problem == "unmatched":
                    field["evidence"][0]["quote"] = "원문에서 확인할 수 없는 시험 문장"
                elif problem == "image":
                    field["evidence"][0].update(location="이미지", file="poster.png")
                    item[1]["notices"][0]["files"] = ["poster.png"]
                else:
                    field["evidence"] = []
                self.write_inputs([item])
                self.assertEqual(self.run_merge(), 1)
                merged = json.loads(self.out.read_text(encoding="utf-8"))
                self.assertEqual(merged["bundles"], [])
                self.assertEqual(merged["merge_report"]["withheld"], 1)
                self.assertTrue(merged["merge_state"]["cards"]["history-video-5"]["holds"])

    def test_deadline_quote_requires_matching_end_role_date_and_time(self):
        field = {"flag": "found", "value": {"end": {"date": "2026-10-23", "time": None, "year_inferred": False}},
                 "evidence": [{"quote": ""}]}
        cases = [
            ("신청 마감 2026-10-23", "2026-10-23", None, True),
            ("접수 마감 2026.10.23.", "2026-10-23", None, True),
            ("모집 기한 2026년 10월 23일", "2026-10-23", None, True),
            ("모집 기간 2026.10.1.~10.23.", "2026-10-23", None, True),
            ("신청 2026-10-23까지", "2026-10-23", None, True),
            ("신청기간 2026.10.1. 09:00 ~ 10.23. 18:30", "2026-10-23", "18:30", True),
            ("신청 기한 2026-10-23 18시 30분", "2026-10-23", "18:30", True),
            ("참가 신청서 제출 기한: 2026-10-23", "2026-10-23", None, True),
            ("신청 기한 2026-10-23 18:30", "2026-10-23", "17:30", False),
            ("신청 기한 2026-10-23 18:30", "2026-10-23", None, False),
            ("모집 기간 2026.10.1.~10.23.", "2026-10-01", None, False),
            ("신청 시작 2026-10-23", "2026-10-23", None, False),
            ("접수 2026-10-23 시작, 종료일 추후 공지", "2026-10-23", None, False),
            ("신청 2026-10-23부터 종료일 추후 공지", "2026-10-23", None, False),
            ("접수 2026-10-23 개시, 종료일 2026-10-25", "2026-10-23", None, False),
            ("신청 마감 2026-10-23 예정", "2026-10-23", None, False),
            ("신청 마감일 미정, 공지 게시일 2026-10-23", "2026-10-23", None, False),
            ("신청 마감일은 2026-10-23이 아님", "2026-10-23", None, False),
            ("작품 제출 마감: 2026-10-23", "2026-10-23", None, False),
            ("영상 출품 마감: 2026-10-23", "2026-10-23", None, False),
            ("공모전 모집 작품 제출 마감: 2026-10-23", "2026-10-23", None, False),
            ("신청서 및 작품 제출 기한: 2026-10-23", "2026-10-23", None, False),
            ("모집 기간 10.1.~10.23.", "2026-10-23", None, False),
            ("신청 기한 2026-10-23 오후 6시", "2026-10-23", "18:00", False),
            ("행사 일자 2026-10-23", "2026-10-23", None, False),
            ("행사 일정 2026-10-23 신청 마감", "2026-10-23", None, False),
            ("모집 마감 2026-10-23, 본선 2026-10-26", "2026-10-23", None, False),
            ("신청마감 2026-10-10, 결과발표 2026-10-23", "2026-10-23", None, False),
            ("신청 2026-10-01 행사 2026-10-23", "2026-10-23", None, False),
        ]
        for quote, day, clock, expected in cases:
            with self.subTest(quote=quote, day=day, time=clock):
                field["evidence"][0]["quote"] = quote
                field["value"]["end"].update(date=day, time=clock)
                self.assertEqual(deadline_is_grounded(field), expected)
        field["evidence"] = [{"quote": "신청 마감 2026-10-23"}, {"quote": "접수 마감 2026-10-25"}]
        field["value"]["end"].update(date="2026-10-23", time=None)
        self.assertFalse(deadline_is_grounded(field), "서로 다른 날짜 인용이 섞이면 보류")

    def test_cli_rejects_value_not_present_in_deadline_quote(self):
        old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        old[1]["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-09-28"
        latest[1]["extraction"]["fields"]["apply"]["value"]["end"]["date"] = "2026-10-25"
        for item in (old, latest):
            item[1]["meta"]["merge_apply_verified"] = True  # 입력 측 허위 메타 무시
        self.write_inputs([old, latest])
        self.assertIn(self.run_merge(), (0, 1))
        bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
        self.assertEqual(bundle["deadline_history"], [])
        self.assertFalse(bundle["meta"]["merge_apply_verified"])
        self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_rejects_start_date_as_deadline_even_when_quote_exists(self):
        old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        quote = "접수 2026-10-23 시작, 종료일 추후 공지"
        latest[0]["body_text"] += " " + quote
        latest[1]["extraction"]["fields"]["apply"]["evidence"][0]["quote"] = quote
        refresh_notice(latest)
        self.write_inputs([old, latest])
        self.assertIn(self.run_merge(), (0, 1))
        bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
        self.assertEqual(bundle["deadline_history"], [])
        self.assertFalse(bundle["meta"]["merge_apply_verified"])
        self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_rejects_nonapplication_dates_despite_matching_quote(self):
        quotes = ("신청 마감일 미정, 공지 게시일 2026-10-23", "신청 마감일은 2026-10-23이 아님",
                  "작품 제출 마감: 2026-10-23", "영상 출품 마감: 2026-10-23")
        for index, quote in enumerate(quotes):
            with self.subTest(quote=quote):
                self.out = self.data / f"bad-deadline-{index}.json"
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
                latest[0]["body_text"] += " " + quote
                latest[1]["extraction"]["fields"]["apply"]["evidence"][0]["quote"] = quote
                refresh_notice(latest)
                self.write_inputs([old, latest])
                self.assertIn(self.run_merge(), (0, 1))
                bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
                self.assertEqual(bundle["deadline_history"], [])
                self.assertFalse(bundle["meta"]["merge_apply_verified"])
                self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_checks_unquoted_deadline_context_for_denial_roles_and_time(self):
        cases = [
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23이 아닙니다"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23\n이 아닙니다"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23\n(위 날짜는)\n아닙니다"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23\n18:30"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23\n결과 발표 2026-10-25"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23" + "가" * 501),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23부터 시작, 종료일 미정"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23 18:30"),
            ("신청 마감: 2026-10-23", "게시일과 신청 마감: 2026-10-23"),
            ("신청 마감: 2026-10-23", "신청 마감: 2026-10-23. 신청 마감: 2026-10-23이 아님"),
        ]
        for index, (quote, original) in enumerate(cases):
            with self.subTest(original=original):
                self.out = self.data / f"truncated-deadline-{index}.json"
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
                latest[0]["body_text"] = "역사 영상 공모전. 대학혁신지원사업단 교내 모집 신청기한 연장. " + original
                latest[1]["extraction"]["fields"]["apply"]["evidence"][0]["quote"] = quote
                refresh_notice(latest)
                self.write_inputs([old, latest])
                self.assertIn(self.run_merge(), (0, 1))
                bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
                self.assertEqual(bundle["deadline_history"], [])
                self.assertFalse(bundle["meta"]["merge_apply_verified"])
                self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_checks_unquoted_update_context_for_denial_and_uncertainty(self):
        for index, suffix in enumerate(("하지 않습니다", "\n하지 않습니다", "\n(위 안내는)\n사실이 아닙니다",
                                        " 예정입니다", " 가능성을 검토합니다", " 사실이 아닙니다", "가" * 501)):
            with self.subTest(suffix=suffix):
                self.out = self.data / f"truncated-update-{index}.json"
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
                latest[0]["body_text"] = ("역사 영상 공모전. 대학혁신지원사업단 교내 모집 신청기한 연장" + suffix
                                          + ". 참가 신청서 제출 기한: 2026-10-23")
                refresh_notice(latest)
                self.write_inputs([old, latest])
                self.assertIn(self.run_merge(), (0, 1))
                bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
                self.assertFalse(bundle["meta"]["merge_kind_verified"])
                self.assertEqual(bundle["deadline_history"], [])
                self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_update_needs_real_quote_found_flag_and_matching_rule(self):
        for case in ("missing_quote", "general_only", "needs_check", "unknown_rule"):
            with self.subTest(case=case):
                # 출력은 수집 데이터 폴더에 있어야 한다.
                self.out = self.data / ("merged-" + case + ".json")
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
                kind = latest[1]["extraction"]["fields"]["kind"]
                latest[1]["meta"]["merge_kind_verified"] = True
                if case == "missing_quote":
                    kind["evidence"][0]["quote"] = "신청기한 연장 공지사항"
                elif case == "general_only":
                    kind["evidence"][0]["quote"] = "교내 모집"
                elif case == "needs_check":
                    kind["flag"] = "needs_check"
                else:
                    latest[0]["title"] = "역사 영상 공모전 참가자 모집"
                    refresh_notice(latest)
                self.write_inputs([old, latest])
                self.assertIn(self.run_merge(), (0, 1))
                bundle = json.loads(self.out.read_text(encoding="utf-8"))["bundles"][0]
                self.assertFalse(bundle["meta"]["merge_kind_verified"])
                self.assertEqual(bundle["deadline_history"], [])
                self.assertIn(bundle["extraction"]["fields"]["apply"]["flag"], ("needs_check", "conflict"))

    def test_cli_ai_cancellation_alone_is_withheld_even_if_quote_matches(self):
        for verified in (True, False):
            with self.subTest(verified=verified):
                self.out = self.data / (f"terminal-{verified}.json")
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="취소", version=2)
                latest[0]["title"] = "역사 영상 공모전 참가자 모집"
                latest[0]["body_text"] += " 대회가 취소되었습니다."
                refresh_notice(latest)
                kind = latest[1]["extraction"]["fields"]["kind"]
                kind["evidence"][0]["quote"] = "대회가 취소되었습니다" if verified else "교내 모집"
                latest[1]["meta"]["merge_terminal_verified"] = not verified
                self.write_inputs([old, latest])
                self.assertEqual(self.run_merge(), 1)
                merged = json.loads(self.out.read_text(encoding="utf-8"))
                self.assertEqual(merged["bundles"], [])
                self.assertEqual(merged["merge_report"]["closed"], 0)
                self.assertEqual(merged["merge_report"]["withheld"], 1)
                verified_file = self.data / f"verified-{verified}.json"
                cards = self.data.parent / f"cards-{verified}.json"
                cards.write_bytes(b'{"previous":true}')
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                                  "--out", str(verified_file)]), 0)
                    self.assertEqual(export_main(["--bundles", str(verified_file), "--out", str(cards), "--allow-empty"]), 2)
                self.assertEqual(cards.read_bytes(), b'{"previous":true}')

    def test_cli_merge_verify_export_uses_korean_day_for_mixed_timezones(self):
        old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        old[0]["published_at"] = "2026-11-01T22:00:00+09:00"
        latest[0]["published_at"] = "2026-11-01T17:00:00+00:00"
        refresh_notice(old)
        refresh_notice(latest)
        self.write_inputs([old, latest])
        self.assertEqual(self.run_merge(), 0)
        verified, cards = self.data / "verified.json", self.data.parent / "cards.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_main(["--bundles", str(self.out), "--notices", str(self.data / "notices.json"),
                                          "--out", str(verified)]), 0)
            self.assertIn(export_main(["--bundles", str(verified), "--out", str(cards)]), (0, 1))
        card = json.loads(cards.read_text(encoding="utf-8"))["cards"][0]
        self.assertEqual([(source["board_id"], source["posted_at"]) for source in card["sources"]],
                         [("mju-general", "2026-11-02"), ("mju-innovation", "2026-11-01")])

    def test_cli_terminal_signal_must_be_confirmed_and_not_an_upcoming_or_negated_event(self):
        cases = [
            ("결과", "최종 결과 발표 안내", "found", False),
            ("결과", "합격자 발표 예정", "found", False),
            ("결과", "최종 결과 발표를 취소합니다", "found", False),
            ("취소", "대회가 취소되지 않았습니다", "found", False),
            ("취소", "대회가 취소되었습니다", "needs_check", False),
        ]
        for index, (value, quote, flag, confirmed) in enumerate(cases):
            with self.subTest(value=value, quote=quote, flag=flag):
                self.out = self.data / f"terminal-case-{index}.json"
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind=value, version=2)
                latest[0]["title"] = "역사 영상 공모전 참가자 모집"
                latest[0]["body_text"] += " " + quote
                refresh_notice(latest)
                kind = latest[1]["extraction"]["fields"]["kind"]
                kind["evidence"][0]["quote"] = quote
                kind["flag"] = flag
                latest[1]["meta"]["merge_terminal_verified"] = True
                self.write_inputs([old, latest])
                self.assertEqual(self.run_merge(), 0 if confirmed else 1)
                merged = json.loads(self.out.read_text(encoding="utf-8"))
                self.assertEqual(merged["merge_report"]["closed"], int(confirmed))
                self.assertEqual(merged["merge_report"]["withheld"], int(not confirmed))
                self.assertEqual(merged["bundles"], [])

    def test_rule_intermediate_results_delay_and_unconfirmed_cancellation_are_withheld(self):
        cases = [
            ("대회 예선 결과 발표 안내", "대회의 예선 결과 발표 안내입니다."),
            ("대회 결과 발표 연기 안내", "대회의 결과 발표가 연기되었습니다."),
            ("대회 취소 안내", "대회는 취소되지 않았습니다."),
            ("대회 취소 안내", "대회 취소 여부를 검토합니다."),
        ]
        for index, (title, body) in enumerate(cases):
            with self.subTest(title=title, body=body):
                self.out = self.data / f"rule-terminal-{index}.json"
                old = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
                latest = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="결과", version=2)
                latest[0].update(title=title, body_text=body)
                refresh_notice(latest)
                self.write_inputs([old, latest])
                # 결과·취소 본문은 AI에 넘기지 않아도 원문으로 보수적으로 판정한다.
                raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
                raw["bundles"] = raw["bundles"][:1]
                (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(self.run_merge(), 1)
                report = json.loads(self.out.read_text(encoding="utf-8"))["merge_report"]
                self.assertEqual((report["closed"], report["withheld"]), (0, 1))

    def test_old_terminal_notice_cannot_close_a_newer_recruitment(self):
        first = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        later = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="모집", version=2)
        first[0].update(title="역사 영상 공모전 취소 안내", body_text="역사 영상 공모전을 취소합니다.")
        later[0].update(title="역사 영상 공모전 참가자 모집", body_text="역사 영상 공모전 참가자를 모집합니다.")
        refresh_notice(first)
        refresh_notice(later)
        self.write_inputs([first, later])
        raw = json.loads((self.data / "bundles.json").read_text(encoding="utf-8"))
        raw["bundles"] = raw["bundles"][1:]
        (self.data / "bundles.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.run_merge(), 1)
        report = json.loads(self.out.read_text(encoding="utf-8"))["merge_report"]
        self.assertEqual((report["closed"], report["withheld"]), (0, 1))

    def test_export_version_number_counts_content_versions_only(self):
        repo = Repository(self.data)
        try:
            notice = Notice("mju_ctl", "ctl/792/1", "https://ctl.mju.ac.kr/post", "title", None, "body")
            repo.save(notice, b"original")
            repo.save(notice, b"same content")
            repo.save(replace(notice, body_text="new"), b"changed")
            repo.export(self.data / "notices.json")
            self.assertEqual(json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]["version_number"], 2)
        finally:
            repo.close()

    def test_curated_groups_use_known_sources_and_separate_campus_contests(self):
        from collector.config import load_sources
        source_ids = {s.id for s in load_sources(ROOT / "config/sources.toml")}
        groups = load_groups(ROOT / "config/contest_groups.toml")
        self.assertEqual(len(groups), 5)
        self.assertTrue(all(source in source_ids for group in groups for source, _ in group.members))
        self.assertFalse(any(group.id.startswith("evc-") for group in groups))

    def test_original_card_blocklist_prevents_canonical_id_bypass(self):
        first = fixture("history-video-5", 1, "mju_innovation", "2026-09-30")
        second = fixture("history-video-5", 2, "mju_general", "2026-10-23", kind="연장", version=2)
        self.write_inputs([first, second])
        blocked = self.data.parent / "blocked.toml"
        blocked.write_text('[[blocked]]\ncard_id=' + json.dumps(first[1]["card_id"]) + '\nreason="fixture"\nsince=2026-11-07\n', encoding="utf-8")
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 0)
        result = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(result["bundles"], [])
        self.assertEqual(result["merge_report"]["blocked_ids"], ["history-video-5"])
        before = self.out.read_bytes()
        blocked.write_text('blocked="invalid"', encoding="utf-8")
        self.assertEqual(self.run_merge("--blocked", str(blocked)), 2)
        self.assertEqual(self.out.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
