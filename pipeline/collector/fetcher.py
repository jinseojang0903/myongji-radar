"""공통 HTTP 수집: 호스트 정책과 간격, 제한 재시도, 프로세스 단위 전체 deadline."""
import json
import math
import multiprocessing
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookiejar import Cookie
from urllib.parse import urljoin, urlsplit

import requests

MAX_FILE_BYTES = 5 * 1024 * 1024
TOTAL_TIMEOUT = 60.0


class FetchPolicyError(ValueError):
    """허용 주소·응답 크기 정책 위반. 첨부에서는 재시도 불필요 경고로 분리한다."""


class ResponseTooLarge(FetchPolicyError):
    pass


class FetchDeadlineExceeded(requests.Timeout):
    """단일 get의 간격·재시도·리다이렉트·body를 합친 시간 예산 소진."""


def checked_url(url, allowed_hosts):
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchPolicyError("올바르지 않은 요청 주소") from exc
    if (parts.scheme != "https" or parts.hostname not in allowed_hosts
            or parts.username is not None or parts.password is not None or port not in (None, 443)):
        raise FetchPolicyError("허용하지 않은 요청 주소")
    return parts


def retry_delay(header, attempt, now=None):
    """Retry-After의 초/HTTP-date 모두 상한 30초. 잘못된 값은 짧은 backoff."""
    header = header.strip()
    if header.isascii() and header.isdigit():
        digits = header.lstrip("0") or "0"
        return 30.0 if len(digits) > 2 else float(min(int(digits), 30))
    try:
        when = parsedate_to_datetime(header)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return min(30.0, max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return min(30.0, float(2 ** attempt))


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise FetchDeadlineExceeded("요청 전체 시간 제한을 초과했습니다")
    return value


def bounded_sleep(delay, deadline):
    if delay <= 0:
        remaining(deadline)
        return
    budget = remaining(deadline)
    time.sleep(min(delay, budget))
    remaining(deadline)


def cookie_records(jar):
    records = []
    for cookie in jar:
        record = dict(cookie.__dict__)
        record["rest"] = record.pop("_rest")
        records.append(record)
    return records


def cookie_jar(records):
    jar = requests.cookies.RequestsCookieJar()
    for record in records:
        jar.set_cookie(Cookie(**record))
    return jar


def _fetch_worker(connection, url, allowed_hosts, interval, last_request, headers, deadline, cookies):
    """예외 객체·외부 오류 메시지 대신 정형 진단만 IPC로 전달한다."""
    fetcher = Fetcher(interval=interval)
    fetcher.last_request = last_request
    fetcher.session.headers.update(headers)
    fetcher.session.cookies = cookie_jar(cookies)
    try:
        content = fetcher._get(url, allowed_hosts, deadline)
        result = {"kind": "ok", "last_request": fetcher.last_request}
    except Exception as exc:
        if isinstance(exc, ResponseTooLarge):
            kind = "size"
        elif isinstance(exc, FetchPolicyError):
            kind = "policy"
        elif isinstance(exc, requests.Timeout):
            kind = "timeout"
        elif isinstance(exc, requests.HTTPError):
            kind = "http"
        elif isinstance(exc, requests.ConnectionError):
            kind = "connection"
        else:
            kind = "error"
        response = exc.response if isinstance(exc, requests.HTTPError) else None
        result = {"kind": kind, "status": response.status_code if response is not None else None,
                  "last_request": fetcher.last_request}
        content = None
    finally:
        cookies = cookie_records(fetcher.session.cookies)
        fetcher.close()
    result["cookies"] = cookies
    try:
        connection.send_bytes(json.dumps(result).encode("utf-8"))
        if content is not None:
            connection.send_bytes(content)
    finally:
        connection.close()


def _receive(connection, result, ready):
    """IPC 부분 전송도 부모의 deadline 대기를 막지 않도록 전용 reader에서 받는다."""
    try:
        result["meta"] = json.loads(connection.recv_bytes(65536))
        if result["meta"]["kind"] == "ok":
            result["content"] = connection.recv_bytes(MAX_FILE_BYTES)
    except (OSError, EOFError, ValueError, KeyError) as exc:
        result["ipc_error"] = type(exc).__name__
    finally:
        ready.set()


class Fetcher:
    """순차 get 전용. 각 호출의 HTTP session은 worker 안에서 생성·종료된다."""

    def __init__(self, interval: float = 1.0, total_timeout: float = TOTAL_TIMEOUT):
        if not math.isfinite(total_timeout) or total_timeout <= 0:
            raise ValueError("전체 요청 제한은 양수여야 합니다")
        self.session = requests.Session()  # 공개 headers/close 호환. 실제 연결은 worker 소유.
        self.session.headers["User-Agent"] = "MjuRadarPrototype/0.1"
        self.interval = interval
        self.total_timeout = total_timeout
        self.last_request = {}

    def get(self, url: str, allowed_hosts: list[str]) -> bytes:
        checked_url(url, allowed_hosts)  # 거부된 주소는 프로세스·요청도 만들지 않는다.
        deadline = time.monotonic() + self.total_timeout
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_fetch_worker, args=(sender, url, allowed_hosts, self.interval,
                                  dict(self.last_request), dict(self.session.headers), deadline,
                                  cookie_records(self.session.cookies)), daemon=True)
        ready, result = threading.Event(), {}
        reader = threading.Thread(target=_receive, args=(receiver, result, ready), daemon=True, name="mju-http-result")
        started = False
        timed_out = False
        try:
            process.start()
            started = True
            sender.close()
            reader.start()
            if not ready.wait(max(0.0, deadline - time.monotonic())):
                timed_out = True
                raise FetchDeadlineExceeded("요청 전체 시간 제한을 초과했습니다")
            remaining(deadline)
            if "ipc_error" in result:
                raise RuntimeError("HTTP 작업자 응답을 받지 못했습니다")
            meta = result["meta"]
            self.last_request.update(meta["last_request"])
            self.session.cookies = cookie_jar(meta.get("cookies", []))
            kind = meta["kind"]
            if kind == "ok":
                return result["content"]
            if kind == "size":
                raise ResponseTooLarge("응답이 5MB 상한을 초과했습니다")
            if kind == "policy":
                raise FetchPolicyError("허용하지 않은 요청 또는 리다이렉트")
            if kind == "timeout":
                raise FetchDeadlineExceeded("요청 시간이 초과되었습니다")
            if kind == "http":
                response = requests.Response()
                response.status_code = meta["status"]
                raise requests.HTTPError(f"HTTP {meta['status']}", response=response)
            if kind == "connection":
                raise requests.ConnectionError("HTTP 연결 실패")
            raise RuntimeError("HTTP 작업자 실행 실패")
        except FetchDeadlineExceeded:
            timed_out = True
            raise
        finally:
            sender.close()
            if started:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                if process.is_alive():
                    raise RuntimeError("HTTP 작업자를 종료하지 못했습니다")
                process.close()
            receiver.close()
            if reader.ident is not None:
                reader.join(timeout=0.5)
                if reader.is_alive():
                    raise RuntimeError("HTTP 작업자 통신을 종료하지 못했습니다")
            # 완료/오류 결과를 못 받았어도 다음 호출의 호스트 간격을 보수적으로 보장한다.
            if timed_out or not ready.is_set() or "ipc_error" in result:
                now = time.monotonic()
                self.last_request.update({host: now for host in allowed_hosts})

    def _get(self, url: str, allowed_hosts: list[str], deadline: float) -> bytes:
        """worker 내부 구현. parent 감독이 DNS·연결·읽기 정지까지 전체 시간을 제한한다."""
        for _ in range(6):
            parts = checked_url(url, allowed_hosts)
            for attempt in range(3):
                delay = self.interval - (time.monotonic() - self.last_request.get(parts.hostname, 0))
                bounded_sleep(delay, deadline)
                self.last_request[parts.hostname] = time.monotonic()
                try:
                    budget = remaining(deadline)
                    response = self.session.get(url, timeout=(min(5, budget), min(20, budget)),
                                                allow_redirects=False, stream=True)
                    with response:
                        remaining(deadline)
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            bounded_sleep(retry_delay(response.headers.get("Retry-After", ""), attempt), deadline)
                            continue
                        response.raise_for_status()
                        if response.is_redirect:
                            url = urljoin(url, response.headers["Location"])
                            break
                        content = bytearray()
                        for chunk in response.iter_content(65536):
                            remaining(deadline)
                            content.extend(chunk)
                            if len(content) > MAX_FILE_BYTES:
                                raise ResponseTooLarge("응답이 5MB 상한을 초과했습니다")
                        remaining(deadline)
                        return bytes(content)
                except FetchDeadlineExceeded:
                    raise
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == 2:
                        raise
                    bounded_sleep(2 ** attempt, deadline)
            else:
                raise RuntimeError("요청 재시도 소진")
        raise FetchPolicyError("리다이렉트가 너무 많습니다")

    def close(self):
        self.session.close()
