"""상태·일정 추론 없이 동일한 추출값만 합치는 미리보기 코어."""
import copy
import json

from collector.repository import content_hash
from extractor.common import posted_day
from extractor.daily import card_id_of
from verifier.stamp import STAMP_KEY
from .groups import CARD_ID, validate_groups


def expand_blocked(blocked: set[str], groups) -> set[str]:
    """운영자가 적은 ID에서 그룹/모든 구성 ID로 양방향 차단을 확장한다."""
    validate_groups(groups)
    if any(not isinstance(value, str) or not CARD_ID.fullmatch(value) for value in blocked):
        raise ValueError("차단 ID 형식 오류")
    expanded = set(blocked)
    for group in groups:
        member_ids = {card_id_of(*pair) for pair in group.members}
        if None in member_ids:
            raise ValueError("구성 카드 ID 생성 실패")
        if group.id in blocked or not member_ids.isdisjoint(blocked):
            expanded.update(member_ids)
            expanded.add(group.id)
    return expanded


def identity(notice):
    if not isinstance(notice, dict):
        raise ValueError("공지 식별자 형식 오류")
    pair = notice.get("source_id"), notice.get("external_id")
    if any(not isinstance(part, str) or not part for part in pair):
        raise ValueError("공지 식별자 없음")
    return pair


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _reason(bundles, originals):
    for bundle in bundles:
        notice = bundle["notices"][0]
        original = originals.get(identity(notice))
        if original is None:
            return "missing_original"
        if (type(notice.get("version_id")) is not int or notice["version_id"] < 1
                or type(original.get("version_id")) is not int or original["version_id"] < 1
                or notice["version_id"] != original.get("version_id")
                or notice.get("input_hash") != content_hash(original)
                or any(notice.get(key) != original.get(key) for key in ("url", "title", "published_at"))):
            return "stale_or_missing_provenance"
        if not posted_day(notice.get("published_at"), ""):
            return "missing_posted_day"
        extraction = bundle.get("extraction")
        if (not isinstance(extraction, dict) or not isinstance(extraction.get("is_contest"), dict)
                or extraction["is_contest"].get("value") is not True):
            return "contest_requires_review"
        if extraction.get("warnings"):
            return "extraction_warning"
        fields = extraction.get("fields")
        if not isinstance(fields, dict):
            return "missing_fields"
        for name in ("title", "organizer", "scope"):
            field = fields.get(name)
            if not isinstance(field, dict) or field.get("flag") != "found" or field.get("value") is None:
                return "identity_requires_review"
        kind = fields.get("kind", {})
        if not isinstance(kind, dict) or kind.get("flag") != "found" or kind.get("value") not in ("모집", "연장", "변경"):
            return "notice_kind_requires_review"
        if bundle.get("deadline_history"):
            return "history_requires_review"
    # 서로 다른 값의 선택·누락값 보완·자격 합집합은 이 단계에서 하지 않는다.
    expected = bundles[0]["extraction"]["fields"]
    for bundle in bundles:
        fields = bundle["extraction"]["fields"]
        if set(fields) != set(expected):
            return "different_field_keys"
        for name, field in fields.items():
            if (not isinstance(field, dict) or field.get("flag") not in ("found", "missing")
                    or field.get("flag") != expected[name].get("flag")
                    or _canonical(field.get("note")) != _canonical(expected[name].get("note"))
                    or _canonical(field.get("value")) != _canonical(expected[name].get("value"))):
                return "different_or_uncertain_fields"
            for key in ("evidence", "alternatives"):
                items = field.get(key, [])
                if not isinstance(items, list) or any(not isinstance(item, dict) or type(item.get("notice")) is not int
                                                      or item["notice"] != 1 for item in items):
                    return "invalid_evidence_index"
    return None


def _merge(group, bundles):
    ordered = sorted(bundles, key=lambda item: (posted_day(item["notices"][0]["published_at"], ""),
                                               *identity(item["notices"][0])))
    result = copy.deepcopy(ordered[-1])
    # 합쳐진 내용은 검증된 원래 묶음과 다르므로 이전 검증 표시는 승계하지 않는다.
    result.pop(STAMP_KEY, None)
    result.update(card_id=group.id, notices=[copy.deepcopy(item["notices"][0]) for item in ordered],
                  statuses={}, deadline_history=[])
    for name, field in result["extraction"]["fields"].items():
        for key in ("evidence", "alternatives"):
            field[key] = []
            for index, bundle in enumerate(ordered, 1):
                for item in bundle["extraction"]["fields"][name].get(key, []):
                    remapped = copy.deepcopy(item)
                    remapped["notice"] = index
                    field[key].append(remapped)
    return result


def preview(data, originals, groups, blocked=frozenset()):
    """운영 JSON과 다른 candidates 형식. 미등록/병합 불가 입력은 그대로 보존한다."""
    validate_groups(groups)
    if not isinstance(data, dict) or not isinstance(data.get("bundles"), list):
        raise ValueError("입력 bundles 배열 없음")
    original_map = {}
    for notice in originals:
        key = identity(notice)
        if key in original_map:
            raise ValueError("원문 공지 중복")
        original_map[key] = notice
    indexed, ids = {}, set()
    for bundle in data["bundles"]:
        if (not isinstance(bundle, dict) or not isinstance(bundle.get("card_id"), str)
                or not CARD_ID.fullmatch(bundle["card_id"]) or not isinstance(bundle.get("notices"), list)
                or len(bundle["notices"]) != 1):
            raise ValueError("공지별 단일 추출 묶음 필요")
        key = identity(bundle["notices"][0])
        if key in indexed or bundle["card_id"] in ids:
            raise ValueError("공지 또는 카드 ID 중복")
        indexed[key] = bundle
        ids.add(bundle["card_id"])
    members = {key for group in groups for key in group.members}
    for key in members & indexed.keys():
        if indexed[key]["card_id"] != card_id_of(*key):
            raise ValueError("구성 공지의 카드 ID 불일치")
    if any(group.id in ids for group in groups):
        raise ValueError("병합 ID와 기존 카드 ID 충돌")
    expanded = expand_blocked(set(blocked), groups)
    result = [copy.deepcopy(item) for key, item in indexed.items() if key not in members]
    reports = []
    for group in groups:
        found = [indexed[key] for key in group.members if key in indexed]
        if not found:
            continue
        if group.id in expanded:
            result.extend(copy.deepcopy(found))
            reports.append({"id": group.id, "action": "blocked"})
            continue
        reason = "missing_member" if len(found) != len(group.members) else _reason(found, original_map)
        if reason:
            result.extend(copy.deepcopy(found))
            reports.append({"id": group.id, "action": "fallback", "reason": reason})
        else:
            result.append(_merge(group, found))
            reports.append({"id": group.id, "action": "merged"})
    removed = {item["card_id"] for item in result if item["card_id"] in expanded}
    result = [item for item in result if item["card_id"] not in expanded]
    matched = {value for value in blocked if expand_blocked({value}, groups) & removed}
    return {"preview_version": 1, "candidates": result,
            "report": {"groups": reports, "blocked": {"requested_ids": sorted(blocked),
                       "expanded_ids": sorted(expanded), "removed_ids": sorted(removed),
                       "unmatched_requested_ids": sorted(set(blocked) - matched)}}}
