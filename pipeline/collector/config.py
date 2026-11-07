"""출처 설정의 공통 기본값을 적용하고 수집 전에 형식을 검증한다."""
import tomllib
import re
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import ADAPTERS
from .models import Source


def load_sources(path: Path) -> list[Source]:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = config.get("defaults", {})
    entries = config.get("sources")
    if not isinstance(defaults, dict):
        raise ValueError("[defaults]는 테이블이어야 합니다")
    if not isinstance(entries, list) or not entries:
        raise ValueError("[[sources]]를 하나 이상 등록해야 합니다")

    sources = []
    seen = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"sources[{index}]는 테이블이어야 합니다")
        # HTML 목록은 RSS 주소 없이도 등록할 수 있다.
        values = {"rss_url": "", **defaults, **entry}
        try:
            source = Source(**values)
        except TypeError as exc:
            raise ValueError(f"sources[{index}] 설정 필드 오류: {exc}") from exc
        for field in ("id", "name", "adapter", "title_selector", "body_selector", "attachment_selector"):
            value = getattr(source, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"sources[{index}]: {field}는 비어 있지 않은 문자열이어야 합니다")
        if source.id in seen:
            raise ValueError(f"출처 ID 중복: {source.id}")
        seen.add(source.id)
        if source.adapter not in ADAPTERS:
            raise ValueError(f"미등록 어댑터: {source.adapter}")
        if source.discovery not in ("rss", "html"):
            raise ValueError(f"{source.id}: discovery는 rss 또는 html이어야 합니다")
        for field in ("rss_url", "list_url"):
            if not isinstance(getattr(source, field), str):
                raise ValueError(f"{source.id}: {field}는 문자열이어야 합니다")
        required_url = "list_url" if source.discovery == "html" else "rss_url"
        if not getattr(source, required_url).strip():
            raise ValueError(f"{source.id}: {source.discovery} 발견에는 {required_url}이 필요합니다")
        if (not isinstance(source.allowed_hosts, list) or not source.allowed_hosts
                or any(not isinstance(host, str) or not host.strip() for host in source.allowed_hosts)):
            raise ValueError(f"{source.id}: allowed_hosts는 비어 있지 않은 문자열 목록이어야 합니다")
        if any(not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+mju\.ac\.kr", host)
               for host in source.allowed_hosts):
            raise ValueError(f"{source.id}: allowed_hosts는 .mju.ac.kr 하위의 정확한 호스트만 허용합니다")
        for field in ("rss_url", "list_url"):
            if not getattr(source, field):
                continue
            try:
                parts = urlsplit(getattr(source, field))
                valid = (parts.scheme == "https" and parts.hostname in source.allowed_hosts
                         and parts.username is None and parts.password is None and parts.port in (None, 443))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{source.id}: {field}는 인증정보 없는 HTTPS·허용 호스트·443 주소여야 합니다")
        if type(source.priority) is not int:
            raise ValueError(f"{source.id}: priority는 정수여야 합니다")
        if not isinstance(source.repair_rss_ampersands, bool):
            raise ValueError(f"{source.id}: repair_rss_ampersands는 불리언이어야 합니다")
        sources.append(source)
    return sorted(sources, key=lambda source: source.priority)
