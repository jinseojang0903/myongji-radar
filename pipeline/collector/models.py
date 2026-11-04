from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    adapter: str
    rss_url: str
    allowed_hosts: list[str]
    title_selector: str
    body_selector: str
    attachment_selector: str
    priority: int = 100
    list_url: str = ""
    discovery: str = "rss"
    repair_rss_ampersands: bool = False


@dataclass(frozen=True)
class NoticeRef:
    external_id: str
    url: str
    title: str
    published_at: str | None


@dataclass
class Notice:
    source_id: str
    external_id: str
    url: str
    title: str
    published_at: str | None
    body_text: str
    images: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    parser_version: str = "k2web-v1"


class Adapter(Protocol):
    def discover(self, xml: bytes, source: Source) -> list[NoticeRef]: ...
    def parse_detail(self, html: bytes, ref: NoticeRef, source: Source) -> Notice: ...
