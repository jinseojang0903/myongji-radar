"""사람 검수 기록 (#15·#99). 형식은 `config/human_reviews.toml` 머리 주석.

팀원이 공개된 카드의 값과 근거를 원문과 대조한 뒤, 그때 본 값과 근거 지문을 한 칸씩 남긴다
(`npm run review -- <카드 id>`가 칸을 만들어 준다). 내보내기는 지금 카드의 값과 근거가 **검수 때와 똑같을 때만**
그 항목을 "검수 완료"(human_verified)로 올린다. 공지·추출이 바뀌어 값이나 근거가 달라지면 검수가 풀리고 보고에 남는다.

- 문의처(contact)는 검수 대상이 아니다: 공개 전에 근거(담당자 이름 가능)와 메일을 비워서, 검수하는 사람이
  공개 화면에서 본 것으로는 원문과 대조할 수 없다.
- 형식 오류는 ReviewsError. 내보내기는 검수 없이(모두 "확인 전") 공개하고 보고에 남긴다. 검수가 빠지는 쪽은
  더 조심스러운 쪽이라 공개를 멈추지 않는다 (차단 목록과 다른 점).
- 값과 근거 지문은 공개 카드 기준이다. 지문은 사이트 쪽 도우미(scripts/review_card.mjs)와 같은 방식으로 만든다:
  키를 정렬한 JSON(공백 없음, 비ASCII 그대로)의 SHA-256.
"""
import hashlib
import json
import re
import tomllib
from datetime import date
from pathlib import Path

from exporter.cards import CARD_FIELDS, CARD_ID_PATTERN, MAX_TEXT_CHARS

REVIEWABLE_FIELDS = [name for name in CARD_FIELDS if name != "contact"]
ENTRY_KEYS = {"card_id", "field", "value", "evidence_sha256", "since"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_VALUE_CHARS = 2000
PROMOTABLE = {"ai", "needs_check"}


class ReviewsError(ValueError):
    """검수 기록 형식 오류. 메시지는 몇 번째 칸의 어느 키인지 (값·파일 내용은 담지 않는다)."""


def canonical_json(value) -> str:
    """키를 정렬하고 공백 없이, 비ASCII는 그대로 (scripts/review_card.mjs의 canonicalJson과 같다)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_fingerprint(evidence: list) -> str:
    """공개 카드 항목의 근거 목록 지문."""
    return hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()


def load_reviews(path: Path) -> dict[tuple[str, str], dict]:
    """검수 기록 → {(card_id, field): {"value": 공개 값, "evidence_sha256", "since"}}.

    Raises:
        OSError: 파일을 읽을 수 없을 때
        ReviewsError: TOML 문법·형식 오류
    """
    try:
        raw = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ReviewsError("UTF-8로 저장된 파일이 아님") from None
    except tomllib.TOMLDecodeError as error:
        line = getattr(error, "lineno", None)
        raise ReviewsError("TOML 문법 오류" + (f" ({line}번째 줄)" if line else "")) from None
    if set(raw) != {"review"} or not isinstance(raw["review"], list):
        raise ReviewsError("최상위에는 review 목록만 둔다 (비어 있으면 review = [])")
    reviews: dict[tuple[str, str], dict] = {}
    for number, entry in enumerate(raw["review"], start=1):
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise ReviewsError(f"{number}번 칸: card_id·field·value·evidence_sha256·since 다섯 키만 있어야 함")
        card_id, field = entry["card_id"], entry["field"]
        if not isinstance(card_id, str) or not CARD_ID_PATTERN.fullmatch(card_id):
            raise ReviewsError(f"{number}번 칸: card_id 형식이 아님")
        if field not in REVIEWABLE_FIELDS:
            raise ReviewsError(f"{number}번 칸: field는 {', '.join(REVIEWABLE_FIELDS)} 중 하나 (문의처는 검수 대상 아님)")
        if not isinstance(entry["value"], str) or len(entry["value"]) > MAX_VALUE_CHARS:
            raise ReviewsError(f"{number}번 칸: value는 {MAX_VALUE_CHARS}자 이하 JSON 글")
        try:
            value = json.loads(entry["value"])
        except ValueError:
            raise ReviewsError(f"{number}번 칸: value가 JSON이 아님") from None
        if value is None:
            raise ReviewsError(f"{number}번 칸: 값이 없는 항목은 검수하지 않는다")
        if not isinstance(entry["evidence_sha256"], str) or not SHA256_PATTERN.fullmatch(entry["evidence_sha256"]):
            raise ReviewsError(f"{number}번 칸: evidence_sha256은 소문자 16진수 64자")
        if type(entry["since"]) is not date:
            raise ReviewsError(f"{number}번 칸: since는 2026-11-16 같은 날짜")
        if (card_id, field) in reviews:
            raise ReviewsError(f"{number}번 칸: 같은 카드·항목이 이미 있음 (다시 검수했으면 옛 칸을 고친다)")
        reviews[(card_id, field)] = {"value": value, "evidence_sha256": entry["evidence_sha256"],
                                     "since": entry["since"].isoformat()}
    return reviews


def apply_reviews(card: dict, reviews: dict[tuple[str, str], dict], warnings: list[str]) -> set[str]:
    """공개할 카드에 검수를 덧씌운다. 값과 근거가 검수 때와 같으면 human_verified, 다르면 그대로 두고 경고.

    Returns: 이 카드에서 쓴(맞춘) 검수 항목 이름 (안 맞은 것 포함 — 기록이 이 카드를 가리켰다는 뜻)
    """
    touched = set()
    for name in REVIEWABLE_FIELDS:
        review = reviews.get((card["id"], name))
        if review is None:
            continue
        touched.add(name)
        item = card["fields"].get(name)
        if not isinstance(item, dict) or item.get("status") == "human_verified":
            continue
        if (item.get("status") not in PROMOTABLE or item.get("value") is None or not item.get("evidence")
                or canonical_json(item["value"]) != canonical_json(review["value"])
                or evidence_fingerprint(item["evidence"]) != review["evidence_sha256"]):
            warnings.append(f"{name} 검수 기록이 지금 값·근거와 달라 쓰지 않음 (다시 검수)")
            continue
        item["status"] = "human_verified"
        note = f"팀이 원문과 대조함 ({review['since']})"
        item["note"] = (f"{note} / {item['note']}" if item.get("note") else note)[:MAX_TEXT_CHARS]
    return touched


def apply_all(data: dict, reviews: dict[tuple[str, str], dict], report: dict) -> int:
    """cards.json 전체에 검수를 덧씌우고, 안 맞은 검수와 카드가 없는 검수를 보고 warnings에 남긴다.
    Returns: 검수 완료로 올린 항목 수"""
    used, promoted = set(), 0
    for card in data["cards"]:
        warnings: list[str] = []
        before = sum(item.get("status") == "human_verified" for item in card["fields"].values())
        for name in apply_reviews(card, reviews, warnings):
            used.add((card["id"], name))
        promoted += sum(item.get("status") == "human_verified" for item in card["fields"].values()) - before
        report["warnings"] += [{"id": card["id"], "reason": message} for message in warnings]
    report["warnings"] += [{"id": card_id, "reason": f"{name} 검수 기록이 있지만 이번에 공개하는 카드에 없음 (사라진 카드면 칸을 지우기)"}
                           for card_id, name in sorted(set(reviews) - used)]
    return promoted
