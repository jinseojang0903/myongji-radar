"""마감일 없는 공개 카드에서 다시 확인할 포스터 공지를 고른다."""

import json

from .models import NoticeRef

MAX_RECOVERY_NOTICES = 12
PERMANENT_REASONS = {"url_policy", "file_bytes", "http_permanent", "asset_count", "notice_bytes"}


def missing_deadline_urls(cards_path):
    """정상적으로 읽은 공개 카드에서 마감이 비어 있는 원문 주소만 고른다."""
    try:
        cards = json.loads(cards_path.read_text(encoding="utf-8"))["cards"]
        return {link["url"] for card in cards
                if not (card["fields"]["apply"]["value"] or {}).get("end")
                for link in card["sources"] if isinstance(link.get("url"), str)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set()


def priority_refs(cards_path, notices_path, sources):
    """주소는 공개 카드가 아니라 수집기가 만든 원문 목록에서 가져온다."""
    missing = missing_deadline_urls(cards_path)
    if not missing:
        return []
    try:
        notices = json.loads(notices_path.read_text(encoding="utf-8"))["notices"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    allowed = {source.id for source in sources}
    pending = {}
    for n in notices:
        if n["source_id"] not in allowed or n["url"] not in missing:
            continue
        if any(image.get("download_status") != "downloaded"
               and image.get("download_reason") not in PERMANENT_REASONS
               for image in n.get("images", [])):
            key = (n["source_id"], n["external_id"])
            pending[key] = NoticeRef(n["external_id"], n["url"], n["title"], n.get("published_at"))
    # 신규 공지를 먼저 확인하고, 한 번에 일반 수집 예산을 과도하게 차지하지 않는다.
    return sorted(pending.items(), key=lambda item: item[1].published_at or "", reverse=True)[:MAX_RECOVERY_NOTICES]
