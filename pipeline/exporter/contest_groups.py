"""같은 대회 묶음(#16)을 공개 카드 한 장으로 합친다.

사람이 같은 회차라고 확인한 묶음(`config/contest_groups.toml`)만 합친다. 제목이 비슷하다고 묶지 않는다.
검증기·내보내기를 거친 공개 카드끼리 합치므로 값을 새로 만들지 않는다. 바꾸는 것은 아래뿐이다 (계약 1절 "같은 대회 묶음").
- 값·근거는 가장 최근 공지 카드의 것. 그 카드에 없는(missing) 항목만 앞 공지 카드의 항목을 통째로 쓴다 (근거도 그 공지).
- 출처는 구성 카드의 출처를 모두 (첫 번째가 최신).
- 공지마다 신청 마감이 다르면 마감 변경 이력에 남긴다 (오래된 공지 → 최근 공지 순).
- 판정에 쓰는 항목이 공지마다 다르면 "원문 확인 필요"로 낮춘다. 올리지는 않는다.
"""

import copy
import json

from exporter.cards import MAX_TEXT_CHARS
from extractor.daily import card_id_of
from merger.groups import validate_groups

# 판정(site/js/rules.js)에 쓰는 항목. 다른 공지와 값이 다르면 어느 쪽이 맞는지 코드가 정하지 않는다
JUDGED_FIELDS = ("apply", "campus", "grades", "enrollment", "team")
DIFFERENT_NOTE = "게시판마다 값이 다름: 이 카드의 다른 출처 공지와 함께 확인"
LOWERABLE = {"ai", "auto_verified"}  # 묶기 전 단계에는 사람 검수(human_verified)가 아직 붙지 않는다


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
    """
    validate_groups(groups)
    aliases = {}
    for group in groups:
        ids = frozenset({group.id, *member_card_ids(group)})
        for card_id in ids - {group.id}:
            aliases[card_id] = ids
    return aliases


def merge_groups(cards: list[dict], groups, checked_at: str) -> tuple[list[dict], list[dict]]:
    """공개 카드 목록에서 묶음마다 카드 한 장으로 합친다.

    cards: build_cards_json이 만든 계약 Card 목록 (차단·검증 반영 뒤). 바꾸지 않는다.
    checked_at: 마감 변경 이력의 확인 날짜 (YYYY-MM-DD, 이번 내보내기의 generated_at 날짜)
    Returns:
        (합친 뒤 카드 목록, 보고 [{"id", "reason"}]). 구성 카드가 2장 미만인 묶음은 그대로 둔다.
        합친 카드는 그 묶음의 구성 카드 중 목록에서 가장 앞에 있던 자리에 들어간다.
    Raises:
        ValueError: 묶음 설정 형식 오류
    """
    validate_groups(groups)
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
        merged, notes = _merge(group.id, present, checked_at)
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


def _merge(group_id: str, present: list[dict], checked_at: str) -> tuple[dict, list[str]]:
    newest_first = sorted(present, key=lambda card: (_latest_posted(card), card["id"]), reverse=True)
    primary = newest_first[0]
    merged = copy.deepcopy(primary)
    merged["id"] = group_id
    notes = []
    for name in ("organizer", "scope"):  # 최신 공지에서 확인하지 못해 비운 값은 앞 공지에서 확인한 값으로
        if merged[name] is None:
            merged[name] = next((card[name] for card in newest_first[1:] if card[name] is not None), None)
    merged["sources"] = _sources(newest_first)
    for name, item in merged["fields"].items():
        if item.get("status") == "missing":
            fallback = next((card["fields"][name] for card in newest_first[1:]
                             if card["fields"].get(name, {}).get("status") != "missing"), None)
            if fallback is not None:
                merged["fields"][name] = item = copy.deepcopy(fallback)
        if name in JUDGED_FIELDS and _differs(newest_first, name) and item.get("status") in LOWERABLE:
            item["status"] = "needs_check"
            item["note"] = _with_note(item.get("note"))
            notes.append(f"{name} 다름")
    merged["deadline_history"] = _history(newest_first, checked_at)
    return merged, notes


def _latest_posted(card: dict) -> str:
    return max(source["posted_at"] for source in card["sources"])


def _sources(newest_first: list[dict]) -> list[dict]:
    seen, sources = set(), []
    for card in newest_first:
        for source in card["sources"]:
            if source["url"] not in seen:
                seen.add(source["url"])
                sources.append(copy.deepcopy(source))
    # 계약: 첫 번째가 최신. 같은 날은 카드 순서(최신 카드 먼저)를 지킨다
    return sorted(sources, key=lambda source: source["posted_at"], reverse=True)


def _differs(cards: list[dict], name: str) -> bool:
    values = {json.dumps(card["fields"][name].get("value"), ensure_ascii=False, sort_keys=True)
              for card in cards if card["fields"].get(name, {}).get("status") not in (None, "missing")}
    return len(values) > 1


def _with_note(note) -> str:
    text = f"{DIFFERENT_NOTE} / {note}" if note else DIFFERENT_NOTE
    return text[:MAX_TEXT_CHARS]


def _history(newest_first: list[dict], checked_at: str) -> list[dict]:
    """구성 카드 자체의 이력 + 공지 사이 마감 차이 (오래된 공지 → 최근 공지). 같은 변경은 한 번만."""
    changes, seen = [], set()

    def add(change):
        key = (change["old"], change["new"])
        if key not in seen:
            seen.add(key)
            changes.append(change)

    for card in reversed(newest_first):
        for change in card.get("deadline_history", []):
            add(copy.deepcopy(change))
    previous = None
    for card in reversed(newest_first):
        end = _apply_end(card)
        if end is None:
            continue
        if previous is not None and not _same_deadline(end, previous[0]):
            add({"field": "apply_end", "old": previous[0], "new": end, "old_source": previous[1],
                 "new_source": card["sources"][0]["url"], "checked_at": checked_at})
        previous = (end, card["sources"][0]["url"])
    return changes


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
