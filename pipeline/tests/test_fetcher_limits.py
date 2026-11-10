"""실제 네트워크 없이 HTTP 예산과 spawn 작업자 종료·IPC 경계를 확인한다."""
import json
import multiprocessing
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

import requests

from collector.fetcher import (FetchDeadlineExceeded, Fetcher, FetchPolicyError, MAX_FILE_BYTES,
                               ResponseTooLarge, RunDeadlineExceeded, _fetch_worker, cookie_jar, cookie_records, retry_delay)


HOST = "www.mju.ac.kr"
URL = "https://www.mju.ac.kr/post"


def slow_worker(connection, *args):
    # DNS 또는 첫 응답이 돌아오지 않는 작업의 무해한 대역.
    try:
        time.sleep(10)
    finally:
        connection.close()


def stalled_body_worker(connection, *args):
    # 메타데이터는 왔지만 body IPC가 끝나지 않는 경우도 전체 deadline으로 끝나야 한다.
    try:
        connection.send_bytes(json.dumps({"kind": "ok", "last_request": {}}).encode())
        time.sleep(10)
    finally:
        connection.close()


def response_worker(connection, url, *args):
    try:
        code = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else None
        meta = {"kind": "http" if code else "ok", "status": code,
                "last_request": {HOST: time.monotonic()}}
        connection.send_bytes(json.dumps(meta).encode())
        if code is None:
            connection.send_bytes(b"x" * MAX_FILE_BYTES)
    finally:
        connection.close()


def actual_worker_without_network(connection, url, *args):
    """실제 spawn 안에서 HTTP 응답만 대역으로 바꾸고 실제 worker와 _get을 실행한다."""
    status = 429 if url.endswith("/429") else 200
    with patch("requests.sessions.Session.get", return_value=Response(status, {"Retry-After": "bad"})):
        _fetch_worker(connection, url, *args)


def malformed_worker(connection, *args):
    try:
        connection.send_bytes(b'[]')
    finally:
        connection.close()


class Clock:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class Response:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = 300 <= status < 400
        self.chunks = [b"ok"] if chunks is None else chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError("private body must not leak", response=response)

    def iter_content(self, chunk_size):
        return iter(self.chunks)


class FetcherLimitTests(unittest.TestCase):
    def fetcher(self, **kwargs):
        fetcher = Fetcher(**kwargs)
        self.addCleanup(fetcher.close)
        return fetcher

    def test_retry_after_seconds_dates_and_invalid_values_are_bounded(self):
        now = datetime(2026, 11, 7, tzinfo=timezone.utc)
        for header, expected in (("999999", 30), ("0", 0), ("0000000012", 12),
                                 (format_datetime(now + timedelta(seconds=90)), 30),
                                 (format_datetime(now + timedelta(seconds=12)), 12),
                                 (format_datetime(now - timedelta(seconds=10)), 0), ("bad", 2)):
            with self.subTest(header=header):
                self.assertEqual(retry_delay(header, 1, now), expected)

    def test_every_retryable_status_caps_retry_after(self):
        for status in (500, 502, 503, 504):
            clock = Clock()
            fetcher = self.fetcher(interval=0)
            responses = [Response(status, {"Retry-After": "120"}), Response(status, {"Retry-After": "120"}), Response()]
            fetcher.session.get = Mock(side_effect=responses)
            with self.subTest(status=status), patch("collector.fetcher.time.monotonic", clock.monotonic), \
                    patch("collector.fetcher.time.sleep", clock.sleep):
                self.assertEqual(fetcher._get(URL, [HOST], 190), b"ok")
                self.assertEqual(clock.sleeps, [30, 30])
                self.assertTrue(all(r.closed for r in responses))

    def test_429_unacceptable_retry_after_fails_immediately_in_actual_get_path(self):
        for header in (None, "", "bad", "31", "999999999999999999", "-1", "1.5", "Wed, 11 Nov 2026 00:00:00 GMT"):
            with self.subTest(header=header):
                clock = Clock()
                fetcher = self.fetcher(interval=0)
                response = Response(429, {} if header is None else {"Retry-After": header})
                fetcher.session.get = Mock(return_value=response)
                with patch("collector.fetcher.time.monotonic", clock.monotonic), patch("collector.fetcher.time.sleep", clock.sleep):
                    with self.assertRaises(requests.HTTPError) as error:
                        fetcher._get(URL, [HOST], 190)
                self.assertEqual(error.exception.response.status_code, 429)
                self.assertEqual(fetcher.session.get.call_count, 1)
                self.assertEqual(clock.sleeps, [])
                self.assertTrue(response.closed)

    def test_429_short_numeric_retry_after_retries_within_deadline(self):
        for header in ("0", "30", "0000000012"):
            with self.subTest(header=header):
                clock = Clock()
                fetcher = self.fetcher(interval=0)
                fetcher.session.get = Mock(side_effect=[Response(429, {"Retry-After": header}), Response()])
                with patch("collector.fetcher.time.monotonic", clock.monotonic), patch("collector.fetcher.time.sleep", clock.sleep):
                    self.assertEqual(fetcher._get(URL, [HOST], 190), b"ok")
                self.assertEqual(fetcher.session.get.call_count, 2)
                self.assertEqual(clock.sleeps, [] if int(header) == 0 else [int(header)])

    def test_expired_run_budget_never_starts_worker(self):
        fetcher = self.fetcher(run_deadline=time.monotonic() - 1)
        with patch("collector.fetcher.multiprocessing.get_context") as context:
            with self.assertRaises(RunDeadlineExceeded):
                fetcher.get(URL, [HOST])
            context.assert_not_called()

    def test_actual_worker_spawn_roundtrip_without_network(self):
        before = {p.pid for p in multiprocessing.active_children()}
        fetcher = self.fetcher(interval=0, total_timeout=5)
        with patch("collector.fetcher._fetch_worker", actual_worker_without_network):
            self.assertEqual(fetcher.get(URL, [HOST]), b"ok")
            with self.assertRaises(requests.HTTPError) as error:
                fetcher.get(f"https://{HOST}/429", [HOST])
            self.assertEqual(error.exception.response.status_code, 429)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        self.assertFalse(any(t.name == "mju-http-result" for t in threading.enumerate()))

    def test_malformed_ipc_metadata_fails_without_reader_or_child_leak(self):
        before = {p.pid for p in multiprocessing.active_children()}
        fetcher = self.fetcher(interval=0, total_timeout=5)
        with patch("collector.fetcher._fetch_worker", malformed_worker):
            with self.assertRaisesRegex(RuntimeError, "HTTP 작업자 응답"):
                fetcher.get(URL, [HOST])
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        self.assertFalse(any(t.name == "mju-http-result" for t in threading.enumerate()))

    def test_run_budget_limits_hung_worker_and_stops_following_request(self):
        before = {p.pid for p in multiprocessing.active_children()}
        fetcher = self.fetcher(total_timeout=60, run_deadline=time.monotonic() + 0.8)
        started = time.monotonic()
        with patch("collector.fetcher._fetch_worker", slow_worker):
            with self.assertRaises(RunDeadlineExceeded):
                fetcher.get(URL, [HOST])
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        self.assertFalse(any(t.name == "mju-http-result" for t in threading.enumerate()))
        with patch("collector.fetcher.multiprocessing.get_context") as context:
            with self.assertRaises(RunDeadlineExceeded):
                fetcher.get(URL, [HOST])
            context.assert_not_called()

    def test_interval_and_retry_consume_the_same_deadline(self):
        clock = Clock()
        fetcher = self.fetcher(interval=1)
        fetcher.last_request[HOST] = clock.value
        fetcher.session.get = Mock(return_value=Response(503, {"Retry-After": "120"}))
        with patch("collector.fetcher.time.monotonic", clock.monotonic), patch("collector.fetcher.time.sleep", clock.sleep):
            with self.assertRaises(FetchDeadlineExceeded):
                fetcher._get(URL, [HOST], 101.5)
        self.assertEqual(clock.sleeps, [1, 0.5])
        self.assertEqual(fetcher.session.get.call_count, 1)
        connect, read = fetcher.session.get.call_args.kwargs["timeout"]
        self.assertEqual((connect, read), (0.5, 0.5))

    def test_redirect_hosts_are_rechecked_and_cannot_reset_deadline(self):
        clock = Clock()
        fetcher = self.fetcher(interval=0)
        fetcher.session.get = Mock(return_value=Response(302, {"Location": "https://evil.example/post"}))
        with patch("collector.fetcher.time.monotonic", clock.monotonic):
            with self.assertRaises(FetchPolicyError):
                fetcher._get(URL, [HOST], 110)
        self.assertEqual(fetcher.session.get.call_count, 1)

        def response(*args, **kwargs):
            clock.value += 0.6
            return Response(302, {"Location": URL + "?redirect=1"})

        fetcher.session.get = Mock(side_effect=response)
        with patch("collector.fetcher.time.monotonic", clock.monotonic):
            with self.assertRaises(FetchDeadlineExceeded):
                fetcher._get(URL, [HOST], 101)
        self.assertEqual(fetcher.session.get.call_count, 2)

    def test_stream_elapsed_time_and_size_are_limited(self):
        clock = Clock()

        def chunks():
            yield b"a"
            clock.value += 2
            yield b"b"

        fetcher = self.fetcher(interval=0)
        fetcher.session.get = Mock(return_value=Response(chunks=chunks()))
        with patch("collector.fetcher.time.monotonic", clock.monotonic):
            with self.assertRaises(FetchDeadlineExceeded):
                fetcher._get(URL, [HOST], 101)
        fetcher.session.get = Mock(return_value=Response(chunks=[b"x" * (MAX_FILE_BYTES + 1)]))
        with self.assertRaises(ResponseTooLarge):
            fetcher._get(URL, [HOST], time.monotonic() + 10)

    def test_supervisor_stops_hung_worker_and_incomplete_body_without_orphans(self):
        before = {p.pid for p in multiprocessing.active_children()}
        for worker in (slow_worker, stalled_body_worker):
            fetcher = self.fetcher(total_timeout=0.8)
            started = time.monotonic()
            with self.subTest(worker=worker.__name__), patch("collector.fetcher._fetch_worker", worker):
                with self.assertRaises(FetchDeadlineExceeded):
                    fetcher.get(URL, [HOST])
            self.assertLess(time.monotonic() - started, 2.5)
            self.assertGreater(fetcher.last_request[HOST], started)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        self.assertFalse(any(t.name == "mju-http-result" for t in threading.enumerate()))

    def test_ipc_large_success_and_http_error_statuses_are_preserved(self):
        fetcher = self.fetcher(total_timeout=5)
        with patch("collector.fetcher._fetch_worker", response_worker):
            self.assertEqual(fetcher.get(URL, [HOST]), b"x" * MAX_FILE_BYTES)
            self.assertIn(HOST, fetcher.last_request)
            for status in (404, 429):
                with self.subTest(status=status), self.assertRaises(requests.HTTPError) as error:
                    fetcher.get(f"https://{HOST}/{status}", [HOST])
                self.assertEqual(error.exception.response.status_code, status)

    def test_policy_rejection_happens_before_process_creation(self):
        fetcher = self.fetcher()
        for url in ("https://evil.example/file", "http://www.mju.ac.kr/file", "https://:secret@www.mju.ac.kr/file"):
            with patch("collector.fetcher.multiprocessing.get_context") as context:
                with self.assertRaises(FetchPolicyError):
                    fetcher.get(url, [HOST])
                context.assert_not_called()

    def test_cookie_roundtrip_preserves_host_path_and_secure_policy(self):
        jar = requests.cookies.RequestsCookieJar()
        jar.set_cookie(requests.cookies.create_cookie("board", "private", domain=HOST, path="/bbs", secure=True))
        restored = cookie_jar(json.loads(json.dumps(cookie_records(jar))))
        good = requests.Request("GET", f"https://{HOST}/bbs/post", cookies=restored).prepare()
        wrong_host = requests.Request("GET", "https://other.mju.ac.kr/bbs/post", cookies=restored).prepare()
        wrong_path = requests.Request("GET", f"https://{HOST}/other", cookies=restored).prepare()
        insecure = requests.Request("GET", f"http://{HOST}/bbs/post", cookies=restored).prepare()
        self.assertEqual(good.headers["Cookie"], "board=private")
        self.assertNotIn("Cookie", wrong_host.headers)
        self.assertNotIn("Cookie", wrong_path.headers)
        self.assertNotIn("Cookie", insecure.headers)
        jar.set_cookie(requests.cookies.create_cookie("expired", "old", domain=HOST, path="/bbs", expires=1))
        restored = cookie_jar(json.loads(json.dumps(cookie_records(jar))))
        request = requests.Request("GET", f"https://{HOST}/bbs/post", cookies=restored).prepare()
        self.assertEqual(request.headers["Cookie"], "board=private")

    def test_worker_preserves_http_status_without_external_error_text(self):
        response = requests.Response()
        response.status_code = 404

        class Connection:
            def __init__(self):
                self.messages = []

            def send_bytes(self, content):
                self.messages.append(content)

            def close(self):
                pass

        connection = Connection()
        with patch.object(Fetcher, "_get", side_effect=requests.HTTPError("private body", response=response)):
            _fetch_worker(connection, URL, [HOST], 0, {}, {}, time.monotonic() + 10, [])
        self.assertEqual(len(connection.messages), 1)
        result = json.loads(connection.messages[0])
        self.assertEqual((result["kind"], result["status"]), ("http", 404))
        self.assertNotIn("private body", connection.messages[0].decode())


if __name__ == "__main__":
    unittest.main()
