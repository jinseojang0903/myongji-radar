"""같은 원문·파일 바이트에서 실제 첨부 입력만 바뀌는 경우의 재추출."""
import json
import unittest

import test_daily_extract as fixtures
from collector.repository import content_hash
from extractor.daily import _failure_count, same_attachment_input, EMPTY_ATTACHMENT_HASH, INPUTS


class AttachmentCacheRecoveryTests(unittest.TestCase):
    def test_legacy_failure_count_uses_the_same_attachment_migration_rule_as_cache(self):
        notice = {'version_id': 1, 'title': 'title', 'images': [], 'body_text': 'body',
                  'attachments': [{'name': 'a.pdf', 'url': 'https://test.mju.ac.kr/a'}]}
        record = {'version_id': 1, 'model': 'opus', 'prompt_fingerprint': 'fp', 'input_hash': content_hash(notice), 'count': 3}
        for files, digest, expected in (([], EMPTY_ATTACHMENT_HASH, 3), (['a.pdf'], EMPTY_ATTACHMENT_HASH, 0),
                                         ([], 'new-input', 0)):
            with self.subTest(files=files, digest=digest):
                record['files'] = files
                count = _failure_count({'id': record}, 'id', notice, 'opus', 'fp', INPUTS, digest)
                self.assertEqual(count, expected)
                self.assertEqual(count == 3, same_attachment_input(record, notice, digest))

    def setUp(self):
        self.f = fixtures.DailyExtractTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.notices = [self.f.notices[0]]
        self.f.add_attachment(self.f.notices[0], "안내.pdf", "첨부에서만 읽을 수 있는 참가 자격")
        self.item = self.f.notices[0]["attachments"][0]
        self.text_path = self.f.folder / self.item["text_path"]

    def call(self, *extra):
        self.f.write_inputs()
        fake = fixtures.FakeRun()
        self.assertEqual(self.f.run_cli(fake, *extra)[0], 0)
        return fake

    def test_conversion_recovery_reextracts_without_new_source_version(self):
        self.item["text_status"] = "needs_review"
        source_hash = content_hash(self.f.notices[0])
        self.call()
        self.item["text_status"] = "extracted"
        self.assertEqual(source_hash, content_hash(self.f.notices[0]))
        fake = self.call()
        self.assertEqual(len(fake.calls), 2, "확인 호출 + 같은 판의 새 첨부 입력 재추출")
        self.assertIn("첨부에서만", fake.calls[1]["input"].decode("utf-8"))
        self.assertEqual(self.call().calls, [], "실제 입력이 같으면 다시 부르지 않음")

    def test_missing_text_and_changed_text_both_invalidate_cache(self):
        self.call()
        self.text_path.unlink()
        self.assertEqual(len(self.call().calls), 2)
        self.assertEqual(self.call().calls, [])
        self.text_path.write_text("복구되어 새로 읽은 참가 자격", encoding="utf-8")
        self.assertEqual(len(self.call().calls), 2)
        self.assertEqual(self.call().calls, [])

    def test_text_beyond_input_limit_does_not_cause_another_call(self):
        self.text_path.write_text("가" * 13000, encoding="utf-8")
        self.call()
        self.text_path.write_text("가" * 12000 + "나" * 1000, encoding="utf-8")
        self.assertEqual(self.call().calls, [], "AI에 실제로 들어가지 않는 뒷부분은 같은 입력")

    def test_old_cache_without_attachment_digest_is_migrated_once(self):
        self.call()
        cache = self.f.folder / "extractions" / (fixtures.CTL + "100.json")
        entry = json.loads(cache.read_text(encoding="utf-8"))
        entry.pop("attachment_input_hash", None)
        cache.write_text(json.dumps(entry), encoding="utf-8")
        self.assertEqual(len(self.call().calls), 2)
        self.assertEqual(self.call().calls, [])

    def test_old_body_only_cache_can_stay_without_a_mass_reextraction(self):
        self.item["text_status"] = "needs_review"
        self.call()
        cache = self.f.folder / "extractions" / (fixtures.CTL + "100.json")
        entry = json.loads(cache.read_text(encoding="utf-8"))
        entry.pop("attachment_input_hash")
        cache.write_text(json.dumps(entry), encoding="utf-8")
        self.assertEqual(self.call().calls, [])

    def test_masked_number_change_is_the_same_actual_input(self):
        self.text_path.write_text("학번 60123456", encoding="utf-8")
        self.call()
        self.text_path.write_text("학번 60987654", encoding="utf-8")
        self.assertEqual(self.call().calls, [])

    def test_recovered_text_resets_the_old_input_failure_limit(self):
        self.item["text_status"] = "needs_review"
        self.f.write_inputs()
        for _ in range(3):
            failing = fixtures.FakeRun(fixtures.stream(), fixtures.stream(output=None))
            self.assertEqual(self.f.run_cli(failing)[0], 0)
        failure_path = self.f.folder / 'extractions' / '_failures.json'
        records = json.loads(failure_path.read_text(encoding='utf-8'))
        for record in records.values():
            record.pop('attachment_input_hash', None)
        failure_path.write_text(json.dumps(records), encoding='utf-8')
        self.assertEqual(self.call().calls, [], "같은 입력을 3번 실패하면 포기")
        self.item["text_status"] = "extracted"
        self.assertEqual(len(self.call().calls), 2, "새 첨부 입력에는 옛 실패 횟수를 넘기지 않음")

    def test_cross_model_also_reextracts_the_recovered_attachment(self):
        self.item["text_status"] = "needs_review"
        self.call("--cross-model", "haiku")
        self.item["text_status"] = "extracted"
        fake = self.call("--cross-model", "haiku")
        self.assertEqual(len(fake.calls), 4)
        self.assertIn("첨부에서만", fake.calls[1]["input"].decode("utf-8"))
        self.assertIn("첨부에서만", fake.calls[3]["input"].decode("utf-8"))
        self.assertEqual(self.call("--cross-model", "haiku").calls, [])
