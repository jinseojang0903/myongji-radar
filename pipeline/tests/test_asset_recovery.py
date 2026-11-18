"""첨부 예산으로 밀린 파일의 다음 실행 처리. 실제 네트워크·문서 변환은 쓰지 않는다."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from collector.assets import AssetCollector
from collector.models import Notice
from collector.fetcher import FetchPolicyError, ResponseTooLarge, RunDeadlineExceeded
import requests


class AssetRecoveryTests(unittest.TestCase):
    def test_notice_limits_do_not_mark_source_as_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = AssetCollector(Path(folder), Mock())
            for reason in ('asset_count', 'notice_bytes', 'url_policy', 'file_bytes', 'http_permanent'):
                old = {'attachments': [{'url': 'https://test.mju.ac.kr/a', 'download_reason': reason}]}
                self.assertFalse(collector.has_pending(old), reason)
            for reason in ('run_requests', 'run_bytes', 'run_time', 'run_deadline', 'download_failed'):
                self.assertTrue(collector.has_pending({'attachments': [{'download_reason': reason}]}), reason)

    def test_retry_reasons_and_previous_file_survive_failed_download(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            url = 'https://test.mju.ac.kr/a'
            original = Notice('test', '1', url, 'title', None, 'body', attachments=[{'url': url, 'name': 'a.pdf'}])
            collector = AssetCollector(data, Mock(get=Mock(return_value=b'original-file')))
            with patch.object(collector, 'convert'):
                collector.enrich(original, ['test.mju.ac.kr'])
            old = copy.deepcopy(original.attachments[0])
            response = requests.Response(); response.status_code = 404
            for error, reason in ((requests.HTTPError('PRIVATE', response=response), 'http_permanent'),
                                  (requests.Timeout('PRIVATE'), 'download_failed'),
                                  (RunDeadlineExceeded('PRIVATE'), 'run_deadline')):
                with self.subTest(reason=reason):
                    notice = Notice('test', '1', url, 'title', None, 'body', attachments=[{'url': url, 'name': 'a.pdf'}])
                    events = AssetCollector(data, Mock(get=Mock(side_effect=error))).enrich(
                        notice, ['test.mju.ac.kr'], {'attachments': [old]})
                    item = notice.attachments[0]
                    self.assertEqual(item['download_reason'], reason)
                    self.assertEqual(item['sha256'], old['sha256'])
                    self.assertEqual((data / item['local_path']).read_bytes(), b'original-file')
                    self.assertNotEqual(item['download_status'], 'downloaded')
                    self.assertNotIn('PRIVATE', str(events))

    def test_deferred_file_precedes_previously_downloaded_file_next_run(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            previous = None
            for run in (1, 2):
                notice = Notice("test", "1", "https://test.mju.ac.kr/post", "title", None, "body",
                                attachments=[{"url": "https://test.mju.ac.kr/a", "name": "a.pdf"},
                                             {"url": "https://test.mju.ac.kr/b", "name": "b.pdf"}])
                fetcher = Mock(get=Mock(side_effect=lambda url, hosts: b"aaaaaa" if url.endswith("/a") else b"bbbbbb"))
                collector = AssetCollector(data, fetcher)
                with patch("collector.assets.MAX_RUN_BYTES", 10), patch.object(collector, "convert"):
                    collector.enrich(notice, ["test.mju.ac.kr"], previous)
                if run == 1:
                    self.assertEqual([x["download_status"] for x in notice.attachments], ["downloaded", "skipped"])
                else:
                    self.assertEqual(fetcher.get.call_args_list[0].args[0], "https://test.mju.ac.kr/b")
                    self.assertEqual(notice.attachments[1]["download_status"], "downloaded")
                    self.assertEqual([x["name"] for x in notice.attachments], ["a.pdf", "b.pdf"], "첨부 이름·순서는 유지")
                previous = {"images": copy.deepcopy(notice.images), "attachments": copy.deepcopy(notice.attachments)}

    def test_budget_warning_has_fixed_reason_without_exception_message(self):
        with tempfile.TemporaryDirectory() as folder:
            notice = Notice("test", "1", "https://test.mju.ac.kr/post", "title", None, "body",
                            attachments=[{"url": "https://test.mju.ac.kr/a", "name": "a.pdf"}])
            for constant, reason in (("MAX_RUN_REQUESTS", "run_requests"), ("MAX_RUN_BYTES", "run_bytes"),
                                     ("MAX_RUN_SECONDS", "run_time"), ("MAX_ASSETS", "asset_count"),
                                     ("MAX_NOTICE_BYTES", "notice_bytes")):
                with self.subTest(reason=reason), patch("collector.assets." + constant, 0):
                    fetcher = Mock()
                    events = AssetCollector(Path(folder), fetcher).enrich(notice, ["test.mju.ac.kr"])
                    self.assertEqual(events[0]["reason"], reason)
                    self.assertEqual(notice.attachments[0]["download_reason"], reason)
                    fetcher.get.assert_not_called()
            for error, reason in ((FetchPolicyError("PRIVATE_MESSAGE"), "url_policy"),
                                  (ResponseTooLarge("PRIVATE_MESSAGE"), "file_bytes")):
                with self.subTest(reason=reason):
                    events = AssetCollector(Path(folder), Mock(get=Mock(side_effect=error))).enrich(notice, ["test.mju.ac.kr"])
                    self.assertEqual(events[0]["reason"], reason)
                    self.assertNotIn("PRIVATE_MESSAGE", str(events))
