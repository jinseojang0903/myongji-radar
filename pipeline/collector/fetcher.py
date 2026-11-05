import time
from urllib.parse import urljoin, urlsplit

import requests


class Fetcher:
    """HTML·첨부 공통: 순차 요청, 호스트별 간격, 제한적 재시도, 응답 5MB 상한."""

    def __init__(self, interval: float = 1.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "MjuRadarPrototype/0.1"
        self.interval = interval
        self.last_request = {}

    def get(self, url: str, allowed_hosts: list[str]) -> bytes:
        for _ in range(6):
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in allowed_hosts or parts.username or parts.port not in (None, 443):
                raise ValueError(f"허용하지 않은 요청 주소: {url}")
            for attempt in range(3):
                delay = self.interval - (time.monotonic() - self.last_request.get(parts.hostname, 0))
                if delay > 0:
                    time.sleep(delay)
                self.last_request[parts.hostname] = time.monotonic()
                try:
                    response = self.session.get(url, timeout=(5, 20), allow_redirects=False, stream=True)
                    with response:
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            retry = response.headers.get("Retry-After", "")
                            if response.status_code == 429 and (not retry.isdigit() or int(retry) > 30):
                                response.raise_for_status()
                            time.sleep(int(retry) if retry.isdigit() else 2 ** attempt)
                            continue
                        response.raise_for_status()
                        if response.is_redirect:
                            url = urljoin(url, response.headers["Location"])
                            break
                        content = bytearray()
                        for chunk in response.iter_content(65536):
                            content.extend(chunk)
                            if len(content) > 5 * 1024 * 1024:
                                raise ValueError("응답이 5MB 상한을 초과했습니다")
                        return bytes(content)
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
            else:
                raise RuntimeError("요청 재시도 소진")
        raise ValueError("리다이렉트가 너무 많습니다")

    def close(self):
        self.session.close()
