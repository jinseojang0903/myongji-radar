"""비공개 첨부 저장. 네트워크는 공통 Fetcher만 사용하고 변환기는 별도 프로세스로 격리한다."""
import hashlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from .asset_text import MAX_TEXT_BYTES, private_path
from .fetcher import FetchPolicyError, MAX_FILE_BYTES, ResponseTooLarge, RunDeadlineExceeded

MAX_NOTICE_BYTES = 20 * 1024 * 1024
MAX_ASSETS = 20
CONVERSION_SECONDS = 15
MAX_RUN_REQUESTS = 1000
MAX_RUN_BYTES = 200 * 1024 * 1024
MAX_RUN_SECONDS = 20 * 60
FILE_FIELDS = ("sha256", "local_path", "size_bytes", "text_path", "text_status", "text_reason")
RUN_BUDGET_REASONS = {"run_requests", "run_bytes", "run_time", "run_deadline"}


class AssetLimit(FetchPolicyError):
    """외부 문구 없이 구분하는 첨부 예산 소진."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def source_order(sources, previous):
    """직전 전체 실행에서 예산으로 밀린 출처부터. 시작 출처 자체가 소진하면 다음 출처에 차례를 준다."""
    deferred = next((row["source"] for row in previous
                     if any(event.get("reason") in RUN_BUDGET_REASONS
                            or event.get("error") == "run_deadline_exceeded"
                            for event in row.get("warnings", []) + row.get("errors", []))), None)
    ids = [source.id for source in sources]
    if deferred not in ids:
        return sources
    start = ids.index(deferred)
    if previous and deferred == previous[0]["source"]:
        start = (start + 1) % len(sources)
    return sources[start:] + sources[:start]


def atomic_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def convert_document(path: Path, data_dir: Path) -> tuple[str, str]:
    """지원 형식은 헤더로 판별. 원격 파일명·명령·URL은 실행하지 않는다."""
    worker = Path(__file__).with_name("document_text.py")
    scratch = private_path(data_dir, "conversion_tmp")
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch) as cwd:
            result = subprocess.run([sys.executable, "-I", str(worker), str(path.resolve())],
                                    cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=CONVERSION_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return "", "conversion_timeout"
    if result.returncode or len(result.stdout) > MAX_TEXT_BYTES:
        # 작업자는 본문/경로 대신 고정 코드 또는 예외 종류만 내보낸다.
        code = result.stderr.decode("ascii", errors="ignore").strip()
        safe = code if re.fullmatch(r"[A-Za-z_]{1,64}", code) else "unsupported_encrypted_or_invalid"
        return "", safe
    text = result.stdout.decode("utf-8", errors="strict").strip()
    return (text, "") if text else ("", "no_text_or_image_only")


class AssetCollector:
    def __init__(self, data_dir: Path, fetcher):
        self.data_dir = data_dir
        self.fetcher = fetcher
        # 같은 실행에서 중복 URL은 한 번만 요청한다. 바이트는 메모리에 쌓지 않는다.
        self.cache = {}
        self.requests = 0
        self.bytes = 0
        self.started = time.monotonic()

    def convert(self, item: dict, target: Path, digest: str):
        """변환 실패는 다운로드 성공 바이트를 되돌리지 않는다."""
        try:
            self.check_deadline()
            text_relative = f"asset_text/{digest}.txt"
            text_target = private_path(self.data_dir, text_relative)
            if text_target.exists() and text_target.stat().st_size <= MAX_TEXT_BYTES:
                text = text_target.read_text(encoding="utf-8")
                if text.strip():
                    item.update(text_status="extracted", text_path=text_relative)
                    return
            self.check_deadline()
            text, reason = convert_document(target, self.data_dir)
            if text:
                atomic_bytes(text_target, text.encode("utf-8"))
                item.update(text_status="extracted", text_path=text_relative)
            else:
                item.update(text_status="needs_review", text_reason=reason)
        except Exception as exc:
            item.update(text_status="needs_review", text_reason="conversion_" + type(exc).__name__)

    def check_deadline(self):
        deadline = getattr(self.fetcher, "run_deadline", None)
        if isinstance(deadline, (int, float)) and time.monotonic() >= deadline:
            raise RunDeadlineExceeded("수집 실행 전체 시간 예산 소진")

    def needs_download(self, item):
        if not item:
            return True
        if item.get("download_reason") in {"url_policy", "file_bytes", "http_permanent", "asset_count", "notice_bytes"}:
            return False  # 매번 확인은 하되 재시도 가능한 첨부보다 먼저 예산을 쓰지 않는다.
        relative = item.get("local_path")
        if not isinstance(relative, str) or not re.fullmatch(r"assets/[0-9a-f]{64}\.bin", relative):
            return True
        try:
            return not private_path(self.data_dir, relative).is_file()
        except (OSError, ValueError):
            return True

    def has_pending(self, previous):
        return any(self.needs_download(item) for kind in ("images", "attachments")
                   for item in (previous or {}).get(kind, []))

    def enrich(self, notice, allowed_hosts: list[str], previous: dict | None = None) -> list[dict]:
        errors = []
        total, count = 0, 0
        tasks = []
        for kind in ("images", "attachments"):
            old = {item["url"]: item for item in (previous or {}).get(kind, [])}
            tasks.extend((kind, item, old.get(item["url"], {})) for item in getattr(notice, kind))
        # 원래 배열은 바꾸지 않아 첨부 이름·번호·해시 순서를 보존한다.
        tasks.sort(key=lambda task: not self.needs_download(task[2]))
        for kind, item, old in tasks:
            count += 1
            url = item["url"]
            try:
                self.check_deadline()
                if count > MAX_ASSETS:
                    raise AssetLimit("asset_count")
                if time.monotonic() - self.started >= MAX_RUN_SECONDS:
                    raise AssetLimit("run_time")
                if total >= MAX_NOTICE_BYTES:
                    raise AssetLimit("notice_bytes")
                key = (url, tuple(sorted(allowed_hosts)))
                if key not in self.cache:
                    if self.requests >= MAX_RUN_REQUESTS:
                        raise AssetLimit("run_requests")
                    if self.bytes >= MAX_RUN_BYTES:
                        raise AssetLimit("run_bytes")
                    if time.monotonic() - self.started >= MAX_RUN_SECONDS:
                        raise AssetLimit("run_time")
                    self.requests += 1
                    content = self.fetcher.get(url, allowed_hosts)
                    self.bytes += len(content)
                    if len(content) > MAX_FILE_BYTES:
                        raise ResponseTooLarge("파일 크기 상한")
                    if self.bytes > MAX_RUN_BYTES:
                        raise AssetLimit("run_bytes")
                else:
                    content = private_path(self.data_dir, self.cache[key]).read_bytes()
                total += len(content)
                if total > MAX_NOTICE_BYTES:
                    raise AssetLimit("notice_bytes")
                digest = hashlib.sha256(content).hexdigest()
                relative = f"assets/{digest}.bin"
                target = private_path(self.data_dir, relative)
                # 파일이 이미 있어도 바이트를 확인해 로컬 손상을 복구한다.
                if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    atomic_bytes(target, content)
                self.cache[key] = relative
                item.update(sha256=digest, local_path=relative, size_bytes=len(content), download_status="downloaded")
                item.pop("download_reason", None)
                item.pop("download_error", None)
                if kind == "images":
                    item.update(text_status="needs_review", text_reason="image_requires_visual_check")
                else:
                    self.convert(item, target, digest)
            except Exception as exc:
                # 현재 요청 실패를 성공으로 위장하지 않고 이전의 검증된 파일만 보존한다.
                for field in FILE_FIELDS:
                    if field in old:
                        item[field] = old[field]
                status = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else None
                permanent = (isinstance(exc, FetchPolicyError)
                             or status is not None and 400 <= status < 500 and status not in (408, 425, 429))
                reason = (exc.reason if isinstance(exc, AssetLimit) else
                          "file_bytes" if isinstance(exc, ResponseTooLarge) else
                          "url_policy" if isinstance(exc, FetchPolicyError) else
                          "run_deadline" if isinstance(exc, RunDeadlineExceeded) else
                          "http_permanent" if permanent else "download_failed")
                item.update(download_status="skipped" if permanent else "failed", download_error=type(exc).__name__,
                            download_reason=reason)
                errors.append({"url": url, "error": "asset: " + type(exc).__name__,
                               "severity": "warning" if permanent else "error", "reason": reason})
        return errors
