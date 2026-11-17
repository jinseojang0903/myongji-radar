"""같은 대회 묶음(#16)을 공개 카드 한 장으로 합친다.

사람이 같은 회차라고 확인한 묶음(`config/contest_groups.toml`)만 합친다. 제목이 비슷하다고 묶지 않는다.
검증기·내보내기를 거친 공개 카드끼리 합치므로 값을 새로 만들지 않는다. 바꾸는 것은 아래뿐이다 (계약 1절 "같은 대회 묶음").
- 값·근거는 가장 최근 공지 카드의 것(게시일, 같은 날이면 게시판 글 번호가 큰 쪽). 그 카드에 없는(missing) 항목만
  앞 공지 카드의 항목을 통째로 쓴다 (근거도 그 공지).
- 출처는 구성 카드의 출처를 모두 (첫 번째가 최신).
- 공지마다 신청 마감이 다르면 마감 변경 이력에 남긴다 (확인 날짜는 새 마감이 적힌 공지의 게시일).
- 판정에 쓰는 항목이 공지마다 다르면 "원문 확인 필요"로 낮춘다. 올리지는 않는다. 낮춘 항목은 사람 검수로도
  올리지 않는다 (exporter/reviews.py: 출처끼리 값이 다른 항목은 한쪽만 대조하고 검수 완료가 될 수 있어서, #121).
"""

import copy
import json
import re

from exporter.cards import MAX_CARD_NOTE_CHARS, MAX_CARD_QUOTE_CHARS, MAX_TEXT_CHARS
from extractor.daily import card_id_of
from merger.groups import validate_groups

# 판정(site/js/rules.js)에 쓰는 항목. 다른 공지와 값이 다르면 어느 쪽이 맞는지 코드가 정하지 않는다
JUDGED_FIELDS = ("apply", "campus", "grades", "enrollment", "team")
DIFFERENT_NOTE = "게시판마다 값이 다름: 이 카드의 다른 출처 공지와 함께 확인"
# 공지마다 값이 다르면 낮출 상태. 사람 검수(human_verified)는 지금 합친 뒤에 붙지만, 묶음 단계에 이미 있으면
# 한 공지만 대조한 검수일 수 있어 함께 낮춘다
LOWERABLE = {"ai", "auto_verified", "human_verified"}
ARTICLE_NUMBER = re.compile(r"/(\d+)/artclView\.do")  # 학교 게시판(K2Web) 글 번호: 게시판을 가리지 않고 올린 순서로 커진다


def member_card_ids(group) -> list[str]:
    """묶음 구성 공지 → 공지별 카드 id (extractor.daily.card_id_of와 같은 규칙).

    Raises:
        ValueError: 카드 id를 만들 수 없는 공지 식별자
    """
    ids = [card_id_of(*pair) for pair in group.members]
    if None in ids:
        raise ValueError("묶음 구성 공지의 카드 id를 만들 수 없음")
    return ids


def group_aliases(groups) -> dict[str, frozenset[str]]:
    """구성 카드 id → 같은 묶음의 id 전체(묶음 id 포함). 차단 목록을 묶음 단위로 넓히는 데 쓴다.

    묶음 id나 구성 카드 하나가 차단되면 묶음 전체를 내린다 (merger.core.expand_blocked와 같은 뜻).
    공지 쌍이 달라도 카드 id가 같아질 수 있어(대소문자·`_`/`-`), 카드 id 기준으로 한 번 더 검사한다.

    Raises:
        ValueError: 구성 카드 id가 두 묶음에 걸치거나 한 묶음에 두 번 나옴, 묶음 id가 어떤 구성 카드 id와 같음
            (차단 확장이 빠지거나 묶음 하나가 사라지므로 설정 오류로 본다)
    """
    validate_groups(groups)
    aliases, group_ids = {}, {group.id for group in groups}
    for group in groups:
        members = member_card_ids(group)
        if len(set(members)) != len(members) or any(card_id in aliases or card_id in group_ids for card_id in members):
            raise ValueError("묶음 구성 카드 id가 겹침 (다른 묶음·묶음 id와 같은 카드 id)")
        ids = frozenset({group.id, *members})
        for card_id in members:
            aliases[card_id] = ids
    return aliases


def merge_groups(cards: list[dict], groups) -> tuple[list[dict], list[dict]]:
    """공개 카드 목록에서 묶음마다 카드 한 장으로 합친다.

    cards: build_cards_json이 만든 계약 Card 목록 (차단·검증 반영 뒤). 바꾸지 않는다.
    Returns:
        (합친 뒤 카드 목록, 보고 [{"id", "reason"}]). 구성 카드가 2장 미만인 묶음은 그대로 둔다.
        합친 카드는 그 묶음의 구성 카드 중 목록에서 가장 앞에 있던 자리에 들어간다.
    Raises:
        ValueError: 묶음 설정 형식 오류
    """
    group_aliases(groups)  # 카드 id 기준 검사까지
    positions = {card["id"]: index for index, card in enumerate(cards)}
    replacements, removed, report = {}, set(), []
    for group in groups:
        present = [cards[positions[card_id]] for card_id in member_card_ids(group) if card_id in positions]
        if len(present) < 2:
            continue
        if group.id in positions:
            # 묶음 id가 다른 카드 id와 같으면 화면 주소가 겹친다. 합치지 않고 알린다
            report.append({"id": group.id, "reason": "묶음 id가 다른 카드 id와 같아 합치지 않음 (contest_groups.toml의 id 바꾸기)"})
            continue
        merged, notes = _merge(group.id, present)
        first = min(positions[card["id"]] for card in present)
        replacements[first] = merged
        removed.update(card["id"] for card in present)
        detail = f"카드 {len(present)}장을 한 장으로" + (f" ({', '.join(notes)})" if notes else "")
        report.append({"id": group.id, "reason": detail})
    result = []
    for index, card in enumerate(cards):
        if index in replacements:
            result.append(replacements[index])
        elif card["id"] not in removed:
            result.append(card)
    return result, report


def _merge(group_id: str, present: list[dict]) -> tuple[dict, list[str]]:
    newest_first = sorted(present, key=_newness, reverse=True)
    primary = newest_first[0]
    merged = copy.deepcopy(primary)
    merged["id"] = group_id
    notes = []
    for name in ("organizer", "scope"):  # 최신 공지에서 확인하지 못해 비운 값은 앞 공지에서 확인한 값으로
        if merged[name] is None:
            merged[name] = next((card[name] for card in newest_first[1:] if card[name] is not None), None)
    merged["sources"] = _sources(newest_first)  # _newness와 같은 기준으로 최신 출처가 첫 번째
    apply_differs = _differs(newest_first, "apply")
    filled = []
    for name, item in merged["fields"].items():
        # 신청 마감이 공지마다 다르면 앞 공지의 일정(옛 신청 마감 포함)으로 채우지 않는다. 캘린더가 단계 일정을 먼저 쓴다
        if item.get("status") != "missing" or (name == "stages" and apply_differs):
            continue
        fallback = next((card["fields"][name] for card in newest_first[1:]
                         if card["fields"].get(name, {}).get("status") != "missing"), None)
        if fallback is not None:
            merged["fields"][name] = copy.deepcopy(fallback)
            filled.append(name)
    for name in JUDGED_FIELDS:
        item = merged["fields"].get(name)
        if item is None:
            continue
        disputed = _differs(newest_first, name) or _uncertain_elsewhere(newest_first, name)
        if disputed:
            # 검수자는 최신 공지 근거만 보고 대조하게 되므로 사람 검수로 올릴 수 있다는 표시를 지운다 (#121)
            item.pop("reviewable", None)
        if item.get("status") in LOWERABLE and disputed:
            item["status"] = "needs_check"
            item["note"] = _with_note(item.get("note"))
            notes.append(f"{name} 다름")
    # 내보내기의 카드당 근거·note 합계 상한을 합친 뒤에도 지킨다 (공지 본문을 이어 붙여 싣지 못하게).
    # 낮추며 붙인 note까지 센 뒤, 넘으면 나중에 채운 항목부터 원래(정보 없음)로 되돌린다
    while filled and not _within_limits(merged):
        name = filled.pop()
        merged["fields"][name] = copy.deepcopy(primary["fields"][name])
        notes.append(f"{name} 상한으로 채우지 않음")
    merged["deadline_history"] = _history(newest_first)
    return merged, notes


def _within_limits(card: dict) -> bool:
    """카드 전체 근거 글자(같은 문장은 한 번)와 note 글자 합계가 내보내기 상한 안인가 (exporter/cards.py)."""
    quotes, note_chars = set(), 0
    for item in card["fields"].values():
        quotes.update(evidence.get("quote", "") for evidence in item.get("evidence", []))
        note_chars += len(item.get("note") or "")
    return sum(map(len, quotes)) <= MAX_CARD_QUOTE_CHARS and note_chars <= MAX_CARD_NOTE_CHARS


def _newness(card: dict) -> tuple:
    """가장 최근 공지 판단: 게시일, 같은 날이면 게시판 글 번호(학교 게시판 시스템이 올린 순서로 매김), 그다음 카드 id.
    게시일은 날짜만 있어 같은 날 두 게시판에 올라온 공지(연장 공지 등)의 앞뒤를 id 글자 순서로 정하지 않게 한다."""
    latest = max(card["sources"], key=lambda source: (source["posted_at"], _article_number(source["url"])))
    return latest["posted_at"], _article_number(latest["url"]), card["id"]


def _article_number(url: str) -> int:
    match = ARTICLE_NUMBER.search(url or "")
    return int(match.group(1)) if match else -1


def _latest_source(card: dict) -> dict:
    return max(card["sources"], key=lambda source: (source["posted_at"], _article_number(source["url"])))


def _sources(newest_first: list[dict]) -> list[dict]:
    seen, sources = set(), []
    for card in newest_first:
        for source in card["sources"]:
            if source["url"] not in seen:
                seen.add(source["url"])
                sources.append(copy.deepcopy(source))
    # 계약: 첫 번째가 최신. 같은 날은 글 번호가 큰 쪽 먼저
    return sorted(sources, key=lambda source: (source["posted_at"], _article_number(source["url"])), reverse=True)


def _differs(cards: list[dict], name: str) -> bool:
    """값이 있는 구성 카드끼리 판정 값이 다른가. 값이 없는(null) 항목은 missing처럼 뺀다.
    목록은 순서를 보지 않고, 신청기간은 _same_deadline 기준으로 본다 (한쪽에만 시각이 있는 같은 날짜는 같다)."""
    values = [card["fields"][name]["value"] for card in cards
              if card["fields"].get(name, {}).get("status") not in (None, "missing")
              and card["fields"][name].get("value") is not None]
    return any(not _equivalent(name, values[0], other) for other in values[1:])


def _uncertain_elsewhere(cards: list[dict], name: str) -> bool:
    """다른 공지에서 이 판정 항목이 "원문 확인 필요"인가 (값이 비어 있어도). 한 공지라도 불확실하면 확정처럼 싣지 않는다."""
    return any(card["fields"].get(name, {}).get("status") == "needs_check" for card in cards[1:])


def _equivalent(name: str, first, second) -> bool:
    if name == "apply" and isinstance(first, dict) and isinstance(second, dict):
        for key in ("start", "end"):
            left, right = first.get(key), second.get(key)
            if left == right:
                continue
            if not isinstance(left, str) or not isinstance(right, str) or not _same_deadline(left, right):
                return False
        return True
    return _comparable(first) == _comparable(second)


def _comparable(value) -> str:
    def unordered(item):
        if isinstance(item, list):
            return sorted((unordered(part) for part in item), key=lambda part: json.dumps(part, ensure_ascii=False, sort_keys=True))
        if isinstance(item, dict):
            return {key: unordered(part) for key, part in item.items()}
        return item
    return json.dumps(unordered(value), ensure_ascii=False, sort_keys=True)


def _with_note(note) -> str:
    text = f"{DIFFERENT_NOTE} / {note}" if note else DIFFERENT_NOTE
    return text[:MAX_TEXT_CHARS]


def _history(newest_first: list[dict]) -> list[dict]:
    """구성 카드 자체의 이력 + 공지 사이 마감 차이. checked_at 순(오래된 것 먼저, 화면은 마지막을 최근 변경으로 씀).

    공지 사이 차이의 checked_at은 새 마감이 적힌 공지의 게시일이다. 내보내기 날짜를 쓰면 같은 변경이 매일
    새 날짜로 바뀌어 보인다. 같은 변경(이전·새 마감과 새 출처가 같음)은 한 번만 남긴다.
    """
    changes = []

    def add(change):
        # 같은 이전·새 마감이면서 새 출처가 같거나 한쪽이 모르는(null) 이력은 같은 변경으로 본다
        for kept in changes:
            if (kept["old"], kept["new"]) == (change["old"], change["new"]) and (
                    kept.get("new_source") is None or change.get("new_source") is None
                    or kept.get("new_source") == change.get("new_source")):
                return
        changes.append(change)

    for card in reversed(newest_first):
        for change in card.get("deadline_history", []):
            add(copy.deepcopy(change))
    previous = None
    for card in reversed(newest_first):
        end = _apply_end(card)
        if end is None:
            continue
        source = _latest_source(card)
        if previous is not None and not _same_deadline(end, previous[0]):
            add({"field": "apply_end", "old": previous[0], "new": end, "old_source": previous[1],
                 "new_source": source["url"], "checked_at": source["posted_at"]})
        previous = (end, source["url"])
    return sorted(changes, key=lambda change: change["checked_at"])


def _same_deadline(first: str, second: str) -> bool:
    """한쪽 공지에만 시각이 적힌 같은 날짜는 마감 변경이 아니다 ("2026-10-23" vs "2026-10-23 15:00")."""
    if first == second:
        return True
    return first[:10] == second[:10] and (len(first) == 10 or len(second) == 10)


def _apply_end(card: dict):
    item = card["fields"].get("apply", {})
    value = item.get("value")
    if item.get("status") == "missing" or not isinstance(value, dict):
        return None
    return value.get("end")
