"""추출값을 합치되 충돌을 숨기거나 사람 검수 결과를 자동 승계하지 않는다."""
import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from collector.repository import content_hash
from extractor.common import posted_day
from extractor.daily import card_id_of
from .groups import CARD_ID, Group, validate_groups
from .terminal import terminal_notice_is_grounded

KST = timezone(timedelta(hours=9))
TERMINAL = {"results", "cancellation"}
CONTEXT = {"result_delayed"}
UPDATE_KINDS = {"연장", "변경"}
HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _day(value):
    if not isinstance(value, str):
        raise ValueError("확인 시각 형식 오류")
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("확인 시각의 시간대 없음")
    return parsed.astimezone(KST).date().isoformat()


def _identity(notice):
    if not isinstance(notice, dict):
        raise ValueError("병합 공지 형식 오류")
    pair = notice.get("source_id"), notice.get("external_id")
    if any(not isinstance(part, str) or not part for part in pair):
        raise ValueError("병합 공지 식별자 없음")
    return pair


def _source_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname is not None and parsed.hostname.endswith(".mju.ac.kr")
                and parsed.username is None and parsed.password is None and parsed.port in (None, 443))
    except ValueError:
        return False


def _validate_bundle(bundle, originals):
    if (not isinstance(bundle, dict) or not isinstance(bundle.get("card_id"), str)
            or not CARD_ID.fullmatch(bundle["card_id"]) or not isinstance(bundle.get("notices"), list)
            or len(bundle["notices"]) != 1 or not isinstance(bundle.get("extraction"), dict)
            or not isinstance(bundle["extraction"].get("fields"), dict)):
        raise ValueError("병합 입력은 공지 하나당 추출 묶음 하나여야 합니다")
    notice = bundle["notices"][0]
    key = _identity(notice)
    if bundle["card_id"] != card_id_of(*key):
        raise ValueError("기존 공지 카드 ID가 수집 식별자와 다릅니다")
    original = originals.get(key)
    if original is None:
        raise ValueError("병합 공지 원문 없음")
    digest = notice.get("input_hash")
    if (type(notice.get("version_id")) is not int or notice["version_id"] < 1
            or type(original.get("version_id")) is not int
            or notice["version_id"] != original.get("version_id")
            or not isinstance(digest, str) or not HASH.fullmatch(digest)
            or digest != content_hash(original) or notice.get("url") != original.get("url")
            or notice.get("published_at") != original.get("published_at") or notice.get("title") != original.get("title")
            or not _source_url(notice.get("url"))):
        raise ValueError("병합 입력의 현재 원문 버전·해시·출처가 다릅니다")
    number = notice.get("version_number")
    if number is not None and (type(number) is not int or number < 1 or number != original.get("version_number")):
        raise ValueError("병합 입력의 원문 버전 수가 다릅니다")
    if not posted_day(notice.get("published_at"), ""):
        raise ValueError("병합 공지 게시일 없음")
    return key


def _remap(field, number):
    if not isinstance(field, dict):
        raise ValueError("병합 추출 항목 형식 오류")
    result = copy.deepcopy(field)
    for name in ("evidence", "alternatives"):
        items = result.get(name, [])
        if not isinstance(items, list):
            raise ValueError("병합 근거 형식 오류")
        for item in items:
            if not isinstance(item, dict) or type(item.get("notice")) is not int or item["notice"] != 1:
                raise ValueError("단일 공지 추출 근거 번호 오류")
            item["notice"] = number
    return result


def _warn(field, flag, message):
    field["flag"] = flag
    old = field.get("note")
    if old is not None and not isinstance(old, str):
        raise ValueError("병합 항목의 설명 형식 오류")
    # 새 충돌 사유가 기존 300자 설명 때문에 잘려 나가지 않도록 한다.
    room = 300 - len(message) - 2
    field["note"] = (old[:room] + "; " + message if old and room > 0 else message)[:300]


def _signature(bundle):
    meta = bundle.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("병합 메타 형식 오류")
    signature = [meta.get(key) for key in ("merge_model", "merge_model_id", "prompt_version", "merge_prompt_fingerprint")]
    return signature if all(isinstance(value, str) and 0 < len(value) <= 200 for value in signature) else None


def _deadline(field):
    if not isinstance(field, dict) or field.get("flag") != "found" or not isinstance(field.get("value"), dict):
        return None
    end = field["value"].get("end")
    if not isinstance(end, dict) or end.get("year_inferred") is not False:
        return None
    value, clock = end.get("date"), end.get("time")
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            return None
        if clock is not None:
            if not isinstance(clock, str) or not re.fullmatch(r"\d{2}:\d{2}", clock):
                return None
            hour, minute = map(int, clock.split(":"))
            if hour > 23 or minute > 59:
                return None
            value += " " + clock
    except ValueError:
        return None
    return value


def _snapshot(bundle):
    notice = bundle["notices"][0]
    if (bundle.get("meta") or {}).get("merge_apply_verified") is not True:
        return None
    end = _deadline(bundle["extraction"]["fields"].get("apply"))
    if end is None:
        return None
    return {"identity": list(_identity(notice)), "version_id": notice["version_id"], "input_hash": notice["input_hash"],
            "version_number": notice.get("version_number"), "end": end, "source": notice["url"],
            "signature": _signature(bundle), "posted": posted_day(notice["published_at"], ""), "uncertain": False}


def _event(old, new, checked_day):
    key_fields = ("identity", "version_id", "input_hash", "end", "signature")
    key = hashlib.sha256(_canonical([[part[key] for key in key_fields] for part in (old, new)]).encode()).hexdigest()
    return {"key": key, "change": {"field": "apply_end", "old": old["end"], "new": new["end"],
            "old_source": old["source"], "new_source": new["source"], "checked_at": checked_day}}


def _validate_snapshot(snapshot):
    if snapshot is None:
        return
    keys = {"identity", "version_id", "input_hash", "version_number", "end", "source", "signature", "posted", "uncertain"}
    if not isinstance(snapshot, dict) or set(snapshot) != keys:
        raise ValueError("이전 병합 관측 형식 오류")
    identity = snapshot["identity"]
    if not isinstance(identity, list) or len(identity) != 2 or any(not isinstance(value, str) for value in identity):
        raise ValueError("이전 병합 관측 식별자 오류")
    if (type(snapshot["version_id"]) is not int or snapshot["version_id"] < 1
            or not isinstance(snapshot["input_hash"], str) or not HASH.fullmatch(snapshot["input_hash"])
            or type(snapshot["uncertain"]) is not bool or not _source_url(snapshot["source"])):
        raise ValueError("이전 병합 관측 값 오류")
    if snapshot["version_number"] is not None and (type(snapshot["version_number"]) is not int or snapshot["version_number"] < 1):
        raise ValueError("이전 병합 원문 버전 수 오류")
    _valid_moment(snapshot["end"])
    date.fromisoformat(snapshot["posted"])
    signature = snapshot["signature"]
    if signature is not None and (not isinstance(signature, list) or len(signature) != 4
                                  or any(not isinstance(value, str) or not value for value in signature)):
        raise ValueError("이전 병합 생성 조건 오류")


def _valid_moment(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?", value):
        raise ValueError("마감 이력 날짜 형식 오류")
    datetime.strptime(value, "%Y-%m-%d %H:%M" if len(value) > 10 else "%Y-%m-%d")


def _prior_cards(previous):
    if previous is None:
        return {}
    state = previous.get("merge_state") if isinstance(previous, dict) else None
    if (not isinstance(state, dict) or set(state) != {"schema_version", "cards"}
            or type(state["schema_version"]) is not int or state["schema_version"] != 1 or not isinstance(state["cards"], dict)):
        raise ValueError("이전 병합 상태 형식 오류")
    for card_id, entry in state["cards"].items():
        if not isinstance(card_id, str) or not CARD_ID.fullmatch(card_id) or not isinstance(entry, dict):
            raise ValueError("이전 병합 카드 형식 오류")
        if (set(entry) != {"members", "snapshot", "events", "inputs", "closed", "holds", "history_quarantined"}
                or not isinstance(entry["members"], list)
                or not isinstance(entry["events"], list) or not isinstance(entry["inputs"], list)
                or not isinstance(entry["holds"], list) or type(entry["history_quarantined"]) is not bool
                or type(entry["closed"]) is not bool):
            raise ValueError("이전 병합 카드 상태 오류")
        if entry["closed"] and (entry["snapshot"] is not None or entry["inputs"] or entry["holds"]):
            raise ValueError("종료된 그룹의 관측 상태 오류")
        validate_groups([Group(card_id, tuple(entry["members"]))])
        _validate_snapshot(entry["snapshot"])
        if entry["snapshot"] is not None and entry["snapshot"]["identity"] not in entry["members"]:
            raise ValueError("이전 관측이 병합 구성 공지에 없습니다")
        identities = []
        for item in entry["inputs"]:
            if (not isinstance(item, dict) or set(item) != {"identity", "version_id", "version_number", "input_hash", "signature", "fields_hash"}
                    or item["identity"] not in entry["members"] or type(item["version_id"]) is not int or item["version_id"] < 1
                    or (item["version_number"] is not None and (type(item["version_number"]) is not int or item["version_number"] < 1))
                    or not isinstance(item["input_hash"], str) or not HASH.fullmatch(item["input_hash"])
                    or not isinstance(item["fields_hash"], str) or not HASH.fullmatch(item["fields_hash"])):
                raise ValueError("이전 구성 공지 관측 형식 오류")
            signature = item["signature"]
            if signature is not None and (not isinstance(signature, list) or len(signature) != 4
                                          or any(not isinstance(value, str) or not value for value in signature)):
                raise ValueError("이전 구성 공지 생성 조건 오류")
            if item["identity"] in identities:
                raise ValueError("이전 구성 공지 관측 중복")
            identities.append(item["identity"])
        held = []
        for item in entry["holds"]:
            if (not isinstance(item, dict) or set(item) != {"identity", "version_id", "version_number", "input_hash"}
                    or item["identity"] not in entry["members"] or type(item["version_id"]) is not int or item["version_id"] < 1
                    or (item["version_number"] is not None and (type(item["version_number"]) is not int or item["version_number"] < 1))
                    or not isinstance(item["input_hash"], str) or not HASH.fullmatch(item["input_hash"])):
                raise ValueError("이전 보류 공지의 관측 형식 오류")
            if item["identity"] in held:
                raise ValueError("이전 보류 공지 중복")
            held.append(item["identity"])
        for event in entry["events"]:
            if (not isinstance(event, dict) or set(event) != {"key", "change"}
                    or not isinstance(event["key"], str) or not HASH.fullmatch(event["key"])):
                raise ValueError("이전 병합 이벤트 오류")
            change = event["change"]
            if (not isinstance(change, dict) or set(change) != {"field", "old", "new", "old_source", "new_source", "checked_at"}
                    or change["field"] != "apply_end" or change["old"] == change["new"]
                    or not _source_url(change["old_source"]) or not _source_url(change["new_source"])):
                raise ValueError("이전 마감 이력 오류")
            _valid_moment(change["old"])
            _valid_moment(change["new"])
            date.fromisoformat(change["checked_at"])
    return copy.deepcopy(state["cards"])


def _ordered(bundle):
    notice = bundle["notices"][0]
    return posted_day(notice["published_at"], ""), notice["source_id"], notice["external_id"]


def _comparable_change(old, new, newer_bundle):
    if old["end"] == new["end"]:
        return False
    # 날짜만 알던 동일한 날에 시각이 더해진 것은 실제 연장인지 알 수 없다.
    if old["end"][:10] == new["end"][:10] and len(old["end"]) != len(new["end"]):
        return False
    kind = newer_bundle["extraction"]["fields"].get("kind", {}).get("value")
    return not (kind == "연장" and new["end"] < old["end"])


def _can_initial(old, new, newer_bundle, all_first):
    kind = newer_bundle["extraction"]["fields"].get("kind", {})
    meta = newer_bundle.get("meta") or {}
    return (all_first and old is not None and new is not None and old["signature"] is not None
            and old["signature"] == new["signature"] and old["posted"] < new["posted"]
            and isinstance(kind, dict) and kind.get("flag") == "found"
            and kind.get("value") in UPDATE_KINDS and meta.get("merge_kind_verified") is True
            and meta.get("merge_notice_kind") == {"연장": "extension", "변경": "change"}.get(kind.get("value"))
            and _comparable_change(old, new, newer_bundle))


def _input_records(bundles):
    return [{"identity": list(_identity(bundle["notices"][0])),
             "version_id": bundle["notices"][0]["version_id"], "input_hash": bundle["notices"][0]["input_hash"],
             "version_number": bundle["notices"][0].get("version_number"),
             "signature": _signature(bundle), "fields_hash": hashlib.sha256(_canonical(bundle["extraction"]["fields"]).encode()).hexdigest()}
            for bundle in bundles]


def _held_version(key, original):
    version = original.get("version_id")
    number = original.get("version_number")
    if type(version) is not int or version < 1 or (number is not None and (type(number) is not int or number < 1)):
        raise ValueError("보류 공지의 원문 버전 오류")
    return {"identity": list(key), "version_id": version, "version_number": number, "input_hash": content_hash(original)}


def _hold_resolved(old, current):
    if current["version_id"] < old["version_id"]:
        raise ValueError("보류 공지의 원문 버전 역행")
    before, after = old["version_number"], current["version_number"]
    if before is not None and (after is None or after < before):
        raise ValueError("보류 공지의 원문 버전 수 역행")
    return (current["version_id"] > old["version_id"] and type(before) is int and type(after) is int
            and after == before + 1 and current["input_hash"] != old["input_hash"])


def _observed_latest_update(prior, snapshots, inputs):
    if not prior or prior["snapshot"] is None or snapshots[-1] is None:
        return False
    old, new = prior["snapshot"], snapshots[-1]
    if (old["identity"] != new["identity"] or new["version_id"] <= old["version_id"]
            or old["input_hash"] == new["input_hash"] or old["signature"] is None or old["signature"] != new["signature"]):
        return False
    if (type(old["version_number"]) is not int or type(new["version_number"]) is not int
            or new["version_number"] != old["version_number"] + 1):
        return False
    old_inputs = {tuple(item["identity"]): item for item in prior["inputs"]}
    current_others = {tuple(item["identity"]): item for item in inputs[:-1]}
    old_others = {key: value for key, value in old_inputs.items() if list(key) != new["identity"]}
    return current_others == old_others


def _merge_group(group, bundles, prior, checked_day):
    bundles = sorted(bundles, key=_ordered)
    fields, field_names = {}, set().union(*(bundle["extraction"]["fields"] for bundle in bundles))
    remapped = [{name: _remap(field, number) for name, field in bundle["extraction"]["fields"].items()}
                for number, bundle in enumerate(bundles, 1)]
    snapshots = [_snapshot(bundle) for bundle in bundles]
    inputs = _input_records(bundles)
    old_inputs = {tuple(item["identity"]): item for item in prior["inputs"]} if prior else {}
    for item in inputs:
        old = old_inputs.get(tuple(item["identity"]))
        if old is None:
            continue
        if item["version_id"] < old["version_id"]:
            raise ValueError("구성 공지 원문 버전이 이전 관측보다 오래되었습니다")
        old_number, new_number = old["version_number"], item["version_number"]
        if old_number is not None and (new_number is None or new_number < old_number
                                       or (item["version_id"] > old["version_id"] and new_number <= old_number)
                                       or (item["version_id"] == old["version_id"] and new_number != old_number)):
            raise ValueError("구성 공지 버전 수가 이전 관측과 일치하지 않습니다")
    observed_update = _observed_latest_update(prior, snapshots, inputs)
    current_inputs = {tuple(item["identity"]): item for item in inputs}
    previous_unchanged = not prior or all(current_inputs.get(key) == value for key, value in old_inputs.items())
    all_first = all(bundle["notices"][0].get("version_number") == 1 for bundle in bundles)
    for name in sorted(field_names):
        candidates = [(index, item[name]) for index, item in enumerate(remapped) if name in item]
        current = copy.deepcopy(candidates[-1][1])
        nonmissing = [(index, field) for index, field in candidates if field.get("value") is not None and field.get("flag") != "missing"]
        if name not in remapped[-1] or current.get("value") is None or current.get("flag") == "missing":
            if nonmissing:
                current = copy.deepcopy(nonmissing[-1][1])
                _warn(current, "needs_check", "최신 공지 미기재: 이전 공지의 값 확인 필요")
        elif name != "kind" and any(_canonical(field.get("value")) != _canonical(current.get("value")) for _, field in nonmissing):
            differing = [(index, field) for index, field in nonmissing if _canonical(field.get("value")) != _canonical(current.get("value"))]
            allowed_update = name == "apply" and (observed_update or (previous_unchanged and all(
                _can_initial(snapshots[index], snapshots[-1], bundles[-1], all_first) for index, _ in differing)))
            if not allowed_update:
                _warn(current, "conflict", "같은 대회의 공지별 추출값 충돌: 원문 확인 필요")
                alternatives = current.setdefault("alternatives", [])
                for index, field in differing:
                    alternatives.append({"text": _canonical(field.get("value"))[:200], "notice": index + 1})
        if (name != "kind" and current.get("flag") == "found"
                and any(field.get("flag") in ("needs_check", "conflict") for _, field in nonmissing)):
            _warn(current, "needs_check", "구성 공지에 확인되지 않은 추출값이 있어 원문 확인 필요")
        fields[name] = current
    extraction = copy.deepcopy(bundles[-1]["extraction"])
    extraction["fields"] = fields
    if not all(bundle["extraction"].get("is_contest", {}).get("value") is True for bundle in bundles):
        extraction["is_contest"] = {"value": False, "reason": "구성 공지 추출의 대회 여부를 확인해야 함"}
    result = {"card_id": group.id, "notices": [copy.deepcopy(bundle["notices"][0]) for bundle in bundles],
              "extraction": extraction, "statuses": {}, "deadline_history": [], "meta": copy.deepcopy(bundles[-1].get("meta") or {})}
    events = copy.deepcopy(prior["events"]) if prior else []
    seen = {event["key"] for event in events}

    def add_event(old, new):
        event = _event(old, new, checked_day)
        if event["key"] not in seen:
            events.append(event)
            seen.add(event["key"])

    previous_snapshot = prior["snapshot"] if prior else None
    selected = snapshots[-1]
    missing_snapshot = any(snapshot is None for snapshot in snapshots)
    uncertain = missing_snapshot
    if previous_snapshot is None:
        same_versions = bool(prior) and len(old_inputs) == len(inputs) and all(
            (old := old_inputs.get(tuple(item["identity"]))) is not None
            and old["version_id"] == item["version_id"] and old["input_hash"] == item["input_hash"] for item in inputs)
        if same_versions:
            # 이전에 확정 관측이 없었고 같은 원문을 다시 추출한 경우, 새 이력으로 소급하지 않는다.
            uncertain |= any(old_inputs[tuple(item["identity"])][key] != item[key]
                             for item in inputs for key in ("signature", "fields_hash"))
        elif prior and not previous_unchanged:
            # 기존 구성의 추출이 바뀌고 새 공지가 같이 추가된 경우에도 소급하지 않는다.
            uncertain = True
        else:
            baseline = None
            for index, snapshot in enumerate(snapshots):
                if snapshot is None:
                    baseline = None
                    continue
                if baseline is not None and baseline["end"] != snapshot["end"]:
                    if _can_initial(baseline, snapshot, bundles[index], all_first):
                        add_event(baseline, snapshot)
                    else:
                        uncertain = True
                baseline = snapshot
    elif selected is not None:
        same_context = all(previous_snapshot[key] == selected[key] for key in ("identity", "version_id", "input_hash", "signature"))
        uncertain |= previous_snapshot["uncertain"] and same_context
        if previous_snapshot["end"] != selected["end"]:
            same_signature = previous_snapshot["signature"] is not None and previous_snapshot["signature"] == selected["signature"]
            same_notice_change = (previous_snapshot["identity"] == selected["identity"]
                                  and previous_snapshot["version_id"] < selected["version_id"]
                                  and type(previous_snapshot["version_number"]) is int and type(selected["version_number"]) is int
                                  and selected["version_number"] == previous_snapshot["version_number"] + 1
                                  and previous_snapshot["input_hash"] != selected["input_hash"]
                                  and _comparable_change(previous_snapshot, selected, bundles[-1]))
            different_notice_update = (previous_snapshot["identity"] != selected["identity"]
                                       and previous_unchanged
                                       and selected["identity"] not in [item["identity"] for item in prior["inputs"]]
                                       and previous_snapshot.get("version_number") == 1 and selected.get("version_number") == 1
                                       and _can_initial(previous_snapshot, selected, bundles[-1], all_first))
            if same_signature and (same_notice_change or different_notice_update):
                if not previous_snapshot["uncertain"] and not missing_snapshot:
                    add_event(previous_snapshot, selected)
            else:
                uncertain = True
    if uncertain and "apply" in fields:
        _warn(fields["apply"], "conflict", "원문 수정·생성 조건·일정 순서가 불명확하여 마감 변경 확정 불가")
    if selected is not None:
        selected["uncertain"] = uncertain or fields.get("apply", {}).get("flag") != "found"
    elif previous_snapshot is not None:
        # 중간 판의 마감을 확인하지 못했다면 다음 판과 직접 이어 붙이지 않는다.
        previous_snapshot["uncertain"] = True
    quarantined = (bool(prior and prior["history_quarantined"])
                   or bool(events and (uncertain or selected is None or fields.get("apply", {}).get("flag") != "found")))
    result["deadline_history"] = [] if quarantined else [event["change"] for event in events]
    state = {"members": [list(pair) for pair in group.members],
             "snapshot": selected if selected is not None else previous_snapshot, "events": events, "inputs": inputs,
             "closed": False, "holds": [], "history_quarantined": quarantined}
    return result, state


def merge_document(data, originals, analysis, groups: list[Group], *, previous=None, checked_at):
    """현재 공지별 묶음과 검토 설정 → 병합 묶음+동일 파일에 저장할 비교 상태."""
    checked_day = _day(checked_at)
    validate_groups(groups)
    if not isinstance(data, dict) or not isinstance(data.get("bundles"), list) or not isinstance(analysis, dict):
        raise ValueError("병합 입력 문서 형식 오류")
    by_identity, card_ids = {}, set()
    for bundle in data["bundles"]:
        key = _validate_bundle(bundle, originals)
        if key in by_identity or bundle["card_id"] in card_ids:
            raise ValueError("병합 입력에 중복 공지 또는 카드 ID")
        by_identity[key] = bundle
        card_ids.add(bundle["card_id"])
    kinds = {}
    if not isinstance(analysis.get("notices"), list):
        raise ValueError("병합 규칙 분석 목록 형식 오류")
    for item in analysis["notices"]:
        key = _identity(item)
        if (key in kinds or key not in originals or type(item.get("source_version_id")) is not int
                or item["source_version_id"] != originals[key].get("version_id")):
            raise ValueError("병합 규칙 분석의 공지 버전 불일치")
        classification = item.get("classification")
        stage = classification.get("notice_kind") if isinstance(classification, dict) else None
        if not isinstance(stage, dict) or not isinstance(stage.get("value"), str):
            raise ValueError("병합 규칙 공지 성격 형식 오류")
        kinds[key] = stage["value"]
    cards = _prior_cards(previous)
    current_groups = {group.id: group for group in groups}
    for old_id, old in cards.items():
        if old_id not in current_groups or not {tuple(pair) for pair in old["members"]}.issubset(set(current_groups[old_id].members)):
            raise ValueError("기존 병합 ID나 공지 구성을 변경할 수 없습니다. 상태와 차단 목록 이관이 필요합니다")
    grouped_members = {pair for group in groups for pair in group.members}
    result = [copy.deepcopy(bundle) for key, bundle in by_identity.items() if key not in grouped_members]
    report = {"merged": 0, "withheld": 0, "closed": 0, "history_review": 0,
              "merged_ids": [], "withheld_ids": [], "closed_ids": [], "history_review_ids": []}
    used_ids = {bundle["card_id"] for bundle in result}
    for group in groups:
        if group.id in used_ids:
            raise ValueError("병합 ID가 미등록 공지 카드 ID와 충돌합니다")
        prior = cards.get(group.id)
        if prior and not {tuple(pair) for pair in prior["members"]}.issubset(set(group.members)):
            raise ValueError("기존 병합 그룹에서 공지를 제거하거나 다른 행사로 재사용할 수 없습니다")
        ai_terminal = [by_identity[key] for key in group.members if key in by_identity
                       and by_identity[key]["extraction"]["fields"].get("kind", {}).get("value") in ("취소", "결과")]
        # 규칙 또는 AI 인용만으로 영구 종료하지 않는다. 실제 원문이 명확한 최종 종료이고,
        # 다른 구성 공지보다 나중이어야 오래된 결과가 신규 모집을 가리지 않는다.
        complete = all(key in originals and key in kinds for key in group.members)
        posted = {key: posted_day(originals[key].get("published_at"), "") for key in group.members} if complete else {}
        confirmed_terminal = complete and all(posted.values()) and any(
            kinds[key] in TERMINAL and terminal_notice_is_grounded(originals[key], kinds[key])
            and all(other == key or posted[other] < posted[key] for other in group.members)
            for key in group.members
        )
        if (prior and prior["closed"]) or confirmed_terminal:
            report["closed"] += 1
            report["closed_ids"].append(group.id)
            cards[group.id] = {"members": [list(pair) for pair in group.members], "snapshot": None,
                               "events": copy.deepcopy(prior["events"]) if prior else [], "inputs": [], "closed": True,
                               "holds": [], "history_quarantined": bool(prior and prior["history_quarantined"])}
            continue
        old_holds = {tuple(item["identity"]): item for item in prior["holds"]} if prior else {}
        unresolved = {}
        for key, item in old_holds.items():
            current = originals.get(key)
            if current is None or not _hold_resolved(item, _held_version(key, current)):
                unresolved[key] = item
        suspect_keys = {key for key in group.members if kinds.get(key) in CONTEXT | TERMINAL and key in originals}
        suspect_keys.update(_identity(item["notices"][0]) for item in ai_terminal)
        for key in suspect_keys:
            current = _held_version(key, originals[key])
            old = old_holds.get(key)
            if old is None or _hold_resolved(old, current):
                unresolved[key] = current
            else:
                unresolved[key] = old
        if unresolved:
            # 확인되지 않은 취소/결과나 결과 연기를 같은 원문의 재추출로 조용히 없애지 않는다.
            kept = copy.deepcopy(prior) if prior else {"members": [], "snapshot": None, "events": [], "inputs": [],
                                                       "closed": False, "holds": [], "history_quarantined": False}
            kept["members"] = [list(pair) for pair in group.members]
            kept["holds"] = [unresolved[key] for key in sorted(unresolved)]
            if kept["snapshot"] is not None:
                kept["snapshot"]["uncertain"] = True
            if kept["events"]:
                kept["history_quarantined"] = True
            cards[group.id] = kept
            report["withheld"] += 1
            report["withheld_ids"].append(group.id)
            continue
        required = [key for key in group.members if kinds.get(key) not in CONTEXT]
        if (any(key not in originals or key not in kinds for key in group.members)
                or not required or any(key not in by_identity for key in required)):
            # 첫 병합부터 구성이 빠져도 ID 관계를 남겨야 과거 구성 카드의 차단을 확대할 수 있다.
            kept = copy.deepcopy(prior) if prior else {"members": [], "snapshot": None, "events": [], "inputs": [],
                                                       "closed": False, "holds": [], "history_quarantined": False}
            kept["members"] = [list(pair) for pair in group.members]
            if kept["snapshot"] is not None:
                kept["snapshot"]["uncertain"] = True
            if kept["events"]:
                kept["history_quarantined"] = True
            cards[group.id] = kept
            report["withheld"] += 1
            report["withheld_ids"].append(group.id)
            continue
        group_bundles = [copy.deepcopy(by_identity[key]) for key in required]
        for key, item in zip(required, group_bundles):
            item.setdefault("meta", {})["merge_notice_kind"] = kinds[key]
        bundle, state = _merge_group(group, group_bundles, prior, checked_day)
        # 공개 계약의 제목·주관·범위는 상태 표시가 없는 최상위 값이다.
        # 충돌/확인 필요 값을 그대로 내보내면 사용자가 확정 정보로 오해할 수 있다.
        fields = bundle["extraction"]["fields"]
        if any(name in fields and fields[name].get("flag") != "found" for name in ("title", "organizer", "scope")):
            state["holds"] = [_held_version(key, originals[key]) for key in required]
            if state["snapshot"] is not None:
                state["snapshot"]["uncertain"] = True
            if state["events"]:
                state["history_quarantined"] = True
            cards[group.id] = state
            report["withheld"] += 1
            report["withheld_ids"].append(group.id)
            continue
        cards[group.id] = state
        result.append(bundle)
        if state["history_quarantined"]:
            report["history_review"] += 1
            report["history_review_ids"].append(group.id)
        used_ids.add(group.id)
        report["merged"] += 1
        report["merged_ids"].append(group.id)
    return {"generated_at": data.get("generated_at"), "bundles": result,
            "merge_state": {"schema_version": 1, "cards": cards}, "merge_report": report}
