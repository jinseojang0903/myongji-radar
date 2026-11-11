"""검증 표시 (#19·#49): 검증기가 묶음마다 남기고, 내보내기는 표시가 맞는 묶음만 공개한다.

표시는 묶음 전체(표시 자신만 빼고)의 해시다. 검증 뒤에 무엇이든 바뀌면 표시가 맞지 않는다:
- 검증기를 거치지 않은 추출 묶음(`card_bundles.json`)을 그대로 내보내기에 넣은 경우
- 검증한 파일의 statuses를 손으로 고쳐 `human_verified`를 붙인 경우 (사람 검수는 검증기 입력에 넣는다)
- 수동 병합(#16)처럼 검증 뒤에 묶음을 합치거나 고친 경우 (합친 묶음은 다시 검증한다)
위조를 막는 서명이 아니라 순서를 지키게 하는 표시다. 해시를 다시 계산해 붙이면 통과한다.

exporter가 이 모듈을 쓰므로 여기서는 exporter를 가져오지 않는다 (순환 import 방지).
"""
import hashlib
import json

STAMP_KEY = "verified"
STAMP_BY = "verifier"


def bundle_fingerprint(bundle: dict) -> str:
    """묶음에서 표시를 뺀 나머지의 sha256 (키 순서·들여쓰기와 상관없이 같은 내용이면 같은 값).

    Raises:
        TypeError, ValueError: JSON으로 바꿀 수 없는 값(NaN·무한대 포함)이 있을 때
    """
    content = {key: value for key, value in bundle.items() if key != STAMP_KEY}
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp_bundle(bundle: dict) -> dict:
    """검증을 마친 묶음에 표시를 붙인 새 dict. 이미 있던 표시는 새로 계산한 것으로 바꾼다."""
    content = {key: value for key, value in bundle.items() if key != STAMP_KEY}
    return {**content, STAMP_KEY: {"by": STAMP_BY, "fingerprint": bundle_fingerprint(content)}}


def has_valid_stamp(bundle) -> bool:
    """검증기가 붙인 표시가 있고 지금 내용과 맞는지. 형식이 틀리거나 해시를 계산할 수 없으면 False."""
    if not isinstance(bundle, dict):
        return False
    mark = bundle.get(STAMP_KEY)
    if not isinstance(mark, dict) or mark.get("by") != STAMP_BY or not isinstance(mark.get("fingerprint"), str):
        return False
    try:
        return mark["fingerprint"] == bundle_fingerprint(bundle)
    except (TypeError, ValueError):
        return False
