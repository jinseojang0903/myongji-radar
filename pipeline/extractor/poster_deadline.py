"""두 모델에서 포스터의 신청 마감일을 독립적으로 확인한다."""

from datetime import date
import re
import unicodedata


def poster_deadline(extraction):
    """후보가 될 수 있는 명시적 마감과 날짜가 들어 있는 이미지 근거를 돌려준다."""
    if not isinstance(extraction, dict):
        return None
    warnings = extraction.get("warnings") or []
    # 모델은 일반 안내도 warnings에 적는다. 지시가 의심되면 표시를 만들지 않는다.
    if any(not isinstance(w, str) or re.search(r"지시|명령|프롬프트|인젝션|instruction|prompt|injection", w, re.I)
           for w in warnings):
        return None
    item = (extraction.get("fields") or {}).get("apply") or {}
    if item.get("flag") != "found":
        return None
    end = (item.get("value") or {}).get("end") or {}
    if end.get("year_inferred") is not False:
        return None
    try:
        day = date.fromisoformat(end.get("date"))
    except (TypeError, ValueError):
        return None
    clock = end.get("time")
    if clock is not None and (not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock)):
        return None
    evidence = item.get("evidence") or []
    if not evidence or any(not isinstance(e, dict) or e.get("location") != "이미지" for e in evidence):
        return None
    # 기간 전체를 인용해도 마감에 해당하는 월·일이 같은 이미지에 들어 있어야 한다.
    month, day_number = f"0?{day.month}", f"0?{day.day}"
    pattern = re.compile(rf"(?<!\d){month}(?:\s*월\s*|\s*[./-]\s*){day_number}(?!\d)")
    files = set()
    for e in evidence:
        quote, name, notice = e.get("quote"), e.get("file"), e.get("notice")
        if (not isinstance(quote, str) or not 0 < len(quote) <= 200
                or not isinstance(name, str) or not name or type(notice) is not int or notice < 1):
            continue
        line = unicodedata.normalize("NFKC", quote)
        if pattern.search(line):
            files.add((notice, name))
    return (day.isoformat(), clock, files) if files else None
