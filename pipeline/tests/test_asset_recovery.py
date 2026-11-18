"""첨부 예산으로 밀린 파일의 다음 실행 처리. 실제 네트워크·문서 변환은 쓰지 않는다."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collector.assets import AssetCollector
from collector.models import Notice
from collector.poster_recovery import priority_refs
from collector.fetcher import FetchPolicyError, ResponseTooLarge, RunDeadlineExceeded
import requests


class AssetRecoveryTests(unittest.TestCase):
    def test_recovery_selects_only_known_missing_deadlines_with_retryable_posters(self):
        with tempfile.TemporaryDirectory() as folder:
            cards, notices = (Path(folder) / name for name in ("cards.json", "notices.json"))
            def url(n):
                return f"https://test.mju.ac.kr/{n}"
            def card(n, deadline=None):
                return {"sources": [{"url": url(n)}], "fields": {"apply": {"value": deadline}}}
            def notice(n, reason, status="skipped"):
                return {"source_id": "test", "external_id": str(n), "url": url(n), "title": "공모전",
                        "published_at": f"2026-11-0{n}", "images": [
                            {"download_status": status, "download_reason": reason}]}
            cards.write_text(json.dumps({"cards": [card(1), card(2), card(3, {"end": "2026-12-01"}),
                                                   card(4), card(5), card(6), card(2)]}), encoding="utf-8")
            notices.write_text(json.dumps({"notices": [notice(1, "FetchPolicyError"), notice(2, "run_bytes"),
                                                       notice(3, "run_bytes"), notice(4, "url_policy"),
                                                       notice(5, None, "downloaded")]}), encoding="utf-8")
            result = priority_refs(cards, notices, [SimpleNamespace(id="test")])
            self.assertEqual([key for key, ref in result], [("test", "2"), ("test", "1")])

    def test_recovery_poster_precedes_attachment_even_if_poster_has_old_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = AssetCollector(Path(folder), Mock(get=Mock(return_value=b"poster")))
            host = "test.mju.ac.kr"
            image = {"url": f"https://{host}/poster"}
            notice = Notice("test", "1", f"https://{host}/post", "공모전", None, "", images=[image],
                            attachments=[{"url": f"https://{host}/attachment", "name": "안내.pdf"}])
            asset.enrich(Notice("test", "1", notice.url, notice.title, None, "", images=[dict(image)]), [host])
            previous = {"images": [{"url": image["url"], "local_path": next(Path(folder).glob("assets/*")).relative_to(folder).as_posix()}]}
            asset = AssetCollector(Path(folder), Mock(get=Mock(return_value=b"poster")))
            with patch("collector.assets.MAX_RUN_BYTES", 6):
                asset.enrich(notice, [host], previous, poster_first=True)
            self.assertEqual(notice.images[0]["download_status"], "downloaded")
            self.assertEqual(notice.attachments[0]["download_reason"], "run_bytes")

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
