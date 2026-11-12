"""검증된 두 시점의 추출값에서 사람 검토용 후보만 만든다. 의미 검증·공개 이력이 아니다."""
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from extractor.common import posted_day
from extractor.daily import card_id_of
from verifier.stamp import has_valid_stamp
from verifier.checks import MIN_QUOTE_CHARS, MAX_QUOTE_CHARS, normalize
from .groups import CARD_ID, EXTERNAL_ID, SOURCE_ID, validate_groups

KST = timezone(timedelta(hours=9))
HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
SIGNATURE = ("merge_model", "merge_model_id", "prompt_version", "merge_prompt_fingerprint")


def _moment(value):
    if not isinstance(value, str):
        raise ValueError("시점 형식 오류")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("시점 시간대 없음")
    return parsed


def _identity(bundle):
    notices = bundle.get("notices") if isinstance(bundle, dict) else None
    if not isinstance(notices, list) or len(notices) != 1 or not isinstance(notices[0], dict):
        return None
    source, external = notices[0].get("source_id"), notices[0].get("external_id")
    if (not isinstance(source, str) or not SOURCE_ID.fullmatch(source)
            or not isinstance(external, str) or not EXTERNAL_ID.fullmatch(external)):
        return None
    return source, external


def _source_valid(notice):
    if any(type(notice.get(name)) is not int or notice[name] < 1 for name in ("version_id", "version_number")):
        return False
    if not isinstance(notice.get("input_hash"), str) or not HASH.fullmatch(notice["input_hash"]):
        return False
    try:
        url = urlsplit(notice.get("url"))
        return (url.scheme == "https" and url.hostname is not None
                and (url.hostname == "mju.ac.kr" or url.hostname.endswith(".mju.ac.kr"))
                and url.username is None and url.password is None and url.port in (None, 443)
                and not url.query and not url.fragment
                and url.path == f"/bbs/{notice['external_id']}/artclView.do"
                and bool(posted_day(notice.get("published_at"), "")))
    except (TypeError, ValueError):
        return False


def _end(field):
    if not isinstance(field, dict) or field.get("flag") != "found" or not isinstance(field.get("value"), dict):
        return None
    end = field["value"].get("end")
    if not isinstance(end, dict) or end.get("year_inferred") is not False:
        return None
    day, clock = end.get("date"), end.get("time")
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            return None
        if clock is not None and (not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock)):
            return None
    except ValueError:
        return None
    return day + (" " + clock if clock is not None else "")


def _eligible(bundle, generated_at):
    if not has_valid_stamp(bundle, generated_at):
        return "invalid_verification_stamp"
    key = _identity(bundle)
    if key is None or bundle.get("card_id") != card_id_of(*key):
        return "invalid_notice_identity"
    notice = bundle["notices"][0]
    if not _source_valid(notice):
        return "invalid_source_provenance"
    extraction = bundle.get("extraction")
    if (not isinstance(extraction, dict) or not isinstance(extraction.get("is_contest"), dict)
            or extraction["is_contest"].get("value") is not True or extraction.get("warnings")):
        return "contest_requires_review"
    fields = extraction.get("fields")
    if not isinstance(fields, dict) or _end(fields.get("apply")) is None:
        return "deadline_requires_review"
    kind = fields.get("kind")
    if not isinstance(kind, dict) or kind.get("flag") != "found" or kind.get("value") not in ("모집", "연장", "변경"):
        return "notice_kind_requires_review"
    statuses = bundle.get("statuses")
    if not isinstance(statuses, dict):
        return "invalid_verification_status"
    for name in ("apply", "kind"):
        status = statuses.get(name)
        if status is not None and (not isinstance(status, dict) or status.get("status") not in ("ai", "auto_verified", "human_verified")
                                   or ("evidence_failed" in status and status["evidence_failed"] is not False)):
            return "deadline_requires_review" if name == "apply" else "notice_kind_requires_review"
        evidence = fields[name].get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return "missing_comparable_evidence"
        if any(not isinstance(item, dict) or type(item.get("notice")) is not int or item["notice"] != 1
               or item.get("location") not in ("제목", "본문") or item.get("file") is not None
               or not isinstance(item.get("quote"), str)
               or not MIN_QUOTE_CHARS <= len(normalize(item["quote"])) <= MAX_QUOTE_CHARS for item in evidence):
            return "unsupported_comparable_evidence"
    meta = bundle.get("meta")
    if not isinstance(meta, dict) or any(not isinstance(meta.get(name), str) or not meta[name].strip() for name in SIGNATURE):
        return "missing_generation_signature"
    return None


def _index(snapshot, memberships):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("bundles"), list):
        raise ValueError("시점 입력에 bundles 배열이 없습니다")
    result = {}
    for bundle in snapshot["bundles"]:
        key = _identity(bundle)
        if key is None:
            raise ValueError("단일 공지 식별자가 없는 묶음입니다")
        bucket = ("group", memberships[key]) if key in memberships else ("notice", *key)
        result.setdefault(bucket, []).append(bundle)
    return result


def _pair_reason(old, new, registered):
    old_ref, new_ref = old["notices"][0], new["notices"][0]
    if any(old["meta"][name] != new["meta"][name] for name in SIGNATURE):
        return "generation_signature_changed"
    same_notice = _identity(old) == _identity(new)
    if same_notice:
        # 값이 그대로여도 서로 모순된 판 정보를 정상 재실행으로 숨기지 않는다.
        if old_ref["url"] != new_ref["url"]:
            return "source_url_changed"
        if (new_ref["version_id"] < old_ref["version_id"]
                or new_ref["version_number"] < old_ref["version_number"]):
            return "version_rollback"
        if (new_ref["version_id"] == old_ref["version_id"]
                and (new_ref["version_number"] != old_ref["version_number"]
                     or new_ref["input_hash"] != old_ref["input_hash"])):
            return "inconsistent_version_metadata"
        if new_ref["version_id"] > old_ref["version_id"] and new_ref["version_number"] == old_ref["version_number"]:
            return "inconsistent_version_metadata"
    old_end, new_end = _end(old["extraction"]["fields"]["apply"]), _end(new["extraction"]["fields"]["apply"])
    if old_end == new_end:
        return "unchanged_deadline"
    if old_end[:10] == new_end[:10] and len(old_end) != len(new_end):
        return "ambiguous_time_precision"
    if new["extraction"]["fields"]["kind"]["value"] == "연장" and new_end < old_end:
        return "decreasing_extension"
    if same_notice:
        if old_ref["input_hash"] == new_ref["input_hash"] or old_ref["version_id"] == new_ref["version_id"]:
            return "same_source_reextracted"
        if new_ref["version_number"] != old_ref["version_number"] + 1:
            return "nonconsecutive_versions"
    elif (not registered or old_ref["version_number"] != 1 or new_ref["version_number"] != 1
          or posted_day(old_ref["published_at"], "") >= posted_day(new_ref["published_at"], "")
          or new["extraction"]["fields"]["kind"]["value"] not in ("연장", "변경")):
        return "cross_notice_order_requires_review"
    return None


def compare_snapshots(before: dict, after: dict, groups, *, checked_at: str) -> dict:
    """검증 표시와 추출값의 차이를 비교하며 모든 후보는 needs_review다.

    입력의 원문·인용·검증 표시는 수정하거나 보고서로 복사하지 않는다.
    이미지·첨부와 값의 의미는 이 단계에서 확인하지 못한다.
    """
    validate_groups(groups)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("시점 입력 형식 오류")
    before_time, after_time = _moment(before.get("generated_at")), _moment(after.get("generated_at"))
    observed = _moment(checked_at)
    if before_time >= after_time or observed < after_time:
        raise ValueError("시점 또는 확인 시각의 순서가 맞지 않습니다")
    memberships = {tuple(pair): group.id for group in groups for pair in group.members}
    previous, current = _index(before, memberships), _index(after, memberships)
    proposals, skipped, unchanged_ids, seen_ids = [], [], [], set()
    for bucket in sorted(previous.keys() | current.keys()):
        old_items, new_items = previous.get(bucket, []), current.get(bucket, [])
        review_id = bucket[1] if bucket[0] == "group" else card_id_of(*bucket[1:])
        if review_id in seen_ids or review_id is None or not CARD_ID.fullmatch(review_id):
            raise ValueError("검토 ID가 중복되거나 잘못되었습니다")
        seen_ids.add(review_id)
        reason = None
        if len(old_items) > 1 or len(new_items) > 1:
            reason = "ambiguous_multiple_notices"
        elif not old_items or not new_items:
            reason = "missing_snapshot_member"
        else:
            old, new = old_items[0], new_items[0]
            reason = (_eligible(old, before["generated_at"]) or _eligible(new, after["generated_at"])
                      or _pair_reason(old, new, bucket[0] == "group"))
        if reason == "unchanged_deadline":
            unchanged_ids.append(review_id)
            continue
        if reason:
            skipped.append({"id": review_id, "reason": reason})
            continue
        proposals.append({"id": review_id, "review_status": "needs_review",
                          "before_version_id": old["notices"][0]["version_id"],
                          "after_version_id": new["notices"][0]["version_id"], "change": {
            "field": "apply_end", "old": _end(old["extraction"]["fields"]["apply"]),
            "new": _end(new["extraction"]["fields"]["apply"]), "old_source": old["notices"][0]["url"],
            "new_source": new["notices"][0]["url"], "checked_at": observed.astimezone(KST).date().isoformat()}})
    return {"review_version": 1, "before_generated_at": before["generated_at"],
            "after_generated_at": after["generated_at"], "proposals": proposals,
            "skipped": skipped, "unchanged_ids": unchanged_ids}
