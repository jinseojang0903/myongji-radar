"""AI 추출 결과(프롬프트 v0.2 형식) → 사이트 카드(docs/api-contract.md 1절).

공개 사이트로 나가는 마지막 관문이라 다음을 코드로 막는다 (#17).
- 계약에 있는 키만 내보낸다. 추출 결과의 다른 키·경고 문구·대체값은 버린다.
- 글 길이 상한, 항목당 근거 개수 상한, 카드당 근거 글자 합계 상한 (공지 본문을 이어 붙여 다시 만들 수 없게).
- 모든 글에서 보이지 않는 글자·제어 문자를 지우고, 휴대폰 번호·학교 밖 이메일·긴 숫자열을 가리고,
  허용 목록 밖 주소를 뺀다 (공지 속 주입 문구나 개인 연락처가 공개되지 않게).
- 링크는 사이트 safeUrl과 같은 기준: https이고 mju.ac.kr·forms.gle·docs.google.com(하위 도메인 포함)만.
- 값을 고치거나 버리면 그 항목은 원문 확인 필요로 내린다 (사람이 본 값과 공개되는 값이 달라지지 않게).
형식이 틀린 카드는 빼고 이유를 돌려준다 (카드 하나 때문에 내보내기 전체가 멈추지 않게).
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit

SCHEMA_VERSION = "0.1"
CARD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CONTRACT_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?$")
ALLOWED_HOSTS = ("mju.ac.kr", "forms.gle", "docs.google.com")  # site/js/html.js와 같게
ALLOWED_EMAIL_DOMAIN = "mju.ac.kr"  # 학생 메일도 이 도메인이라, 문의처 이메일은 사람 검수 뒤에만 공개한다

MAX_TITLE_CHARS = 200      # schema.json title
MAX_ORGANIZER_CHARS = 100  # schema.json organizer
MAX_TEXT_CHARS = 300       # schema.json 요약 글자
MAX_QUOTE_CHARS = 200      # schema.json 근거 문장
MAX_SHORT_CHARS = 100      # 파일 이름·부서·메타
MAX_TERM_CHARS = 30        # 학적 용어 하나
MAX_AREA_CHARS = 20
MAX_PHONE_CHARS = 40
MAX_URL_CHARS = 500
MAX_EVIDENCE = 5           # 항목당 근거
MAX_CARD_QUOTE_CHARS = 1500  # 카드 전체 근거 글자 합계 (같은 문장은 한 번만 센다). 예시 카드는 약 660자
MAX_CARD_NOTE_CHARS = 1000   # 카드 전체 AI note 글자 합계 (note로 본문을 옮겨 싣지 못하게)
MAX_LIST_ITEMS = 10        # 학적 목록·단계 수
MAX_REASON_CHARS = 80      # 뺀 이유에 넣는 값 (로그에 AI 값이 길게 남지 않게)

VERIFIED = {"auto_verified", "human_verified"}
FLAGS = {"found", "missing", "needs_check", "conflict"}
SKIPPED_KINDS = {"결과", "취소"}  # 수상자 발표·취소 공지는 신청할 카드가 아니다
EVIDENCE_LOCATIONS = {"제목", "본문", "이미지", "첨부"}
SCOPES = {"교내", "외부"}
STAGE_NAMES = {"신청", "출품", "예선", "본선", "발표", "시상", "기타"}
# 마감 단계 (site/js/ics.js DEADLINE_STAGES와 같다). 나머지는 그날 열리는 일정이라 날짜가 하나면 그 날짜가 곧 end다
DEADLINE_STAGES = {"신청", "출품"}
DELIVERABLE_TAGS = ["기획서·제안서", "영상", "에세이·수기", "코드·개발", "발표", "디자인", "현장 참가", "기타"]
CAMPUSES = {"인문", "자연"}
META_KEYS = ("prompt_version", "extract_model", "cross_model", "checked_at")
# 계약 1절 fields 목록. title·organizer·scope는 카드 최상위로, kind는 계약에 없어 내보내지 않는다.
# 핵심 항목(신청기간·자격)을 앞에 두어 카드당 근거 글자 상한을 먼저 쓰게 한다.
CARD_FIELDS = ["apply", "stages", "campus", "grades", "enrollment", "team", "deliverables",
               "benefits", "mileage", "how_to_apply", "contact"]

BACKSLASH = chr(92)
# 줄바꿈류는 공백으로. 나머지 보이지 않는 글자는 유니코드 범주로 지운다 (목록으로 막으면 빠지는 글자가 생김)
LINE_BREAK_CODES = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029, 0x3000}
REMOVED_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}  # 제어·서식(양방향·폭 없는 글자·소프트 하이픈·태그 문자)·서로게이트·사용자 정의·미할당
EXTRA_INVISIBLE_CODES = ({0x034F, 0x115F, 0x1160, 0x180E, 0x3164, 0xFFA0}  # 글자로 분류되지만 보이지 않는 것 (한글 채움 문자 등)
                         | set(range(0xFE00, 0xFE10)) | set(range(0xE0100, 0xE01F0)))  # 이체자 선택자
# 판정용 사본에서만 바꾸는 글자 (전각·여러 대시·가운뎃점). 한 글자 → 한 글자라 위치가 원문과 같다
DASH_CODES = {0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D,
              0x2500, 0x2501, 0x2796, 0x3161, 0x30FC, 0xFF70}  # 한글 'ㅡ'·괘선·장음 기호도 대시처럼 쓰인다
DOT_CODES = {0x00B7, 0x2027, 0x30FB, 0xFF65, 0x3002, 0xFF61}

URL_CHAR = "[A-Za-z0-9._~:/?#@!$&'()*+,;=%" + re.escape(BACKSLASH) + r"\[\]-]"
BARE_DOMAIN_ENDINGS = "com|net|org|kr|me|ly|io|co|gl|gle|link|site|xyz|app|page|to|info|biz|kakao"  # 단축 주소·오픈채팅 포함
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)" + URL_CHAR + "+"
                         + r"|(?<![\w@.-])(?:[A-Za-z0-9-]+\.)+(?:" + BARE_DOMAIN_ENDINGS + r")\b(?:/" + URL_CHAR + "*)?",
                         re.IGNORECASE | re.ASCII)
URL_TRAILING = ".,;:!?)]'"
EMAIL_IN_TEXT = re.compile(r"(?<![\w.%+-])[\w.%+-]+\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", re.IGNORECASE)
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$", re.ASCII)
PHONE_SEPARATOR = r"[\s\-.()/~]{0,3}"
MOBILE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?82" + PHONE_SEPARATOR + r"(?:\(0\))?" + PHONE_SEPARATOR + r"0?|\(?0)1[016789]\)?"
                            + PHONE_SEPARATOR + r"\d{3,4}" + PHONE_SEPARATOR + r"\d{4}(?!\d)", re.ASCII)
MOBILE_DIGITS = re.compile(r"0?1[016789]\d{7,8}", re.ASCII)
LONG_NUMBER_IN_TEXT = re.compile(r"(?<!\d)\d{8,}(?!\d)", re.ASCII)  # 학번(8자리)·계좌번호 등
PHONE = re.compile(r"^[\s0-9A-Za-z()+\-.~,/:가-힣]*$")
URL_HOST = re.compile(r"^[a-z0-9.-]+$", re.ASCII)
GENERATED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$", re.ASCII)  # 사이트 Date.parse가 읽는 꼴
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$", re.ASCII)

PROBLEM_TEXT = {
    "pii": "개인 연락처로 보이는 부분을 가림",
    "url": "허용 목록 밖 주소를 글에서 뺌",
    "clipped": "글이 길어 자름",
}


class CardExportError(ValueError):
    """카드 하나를 계약 형식으로 만들 수 없을 때 (그 카드만 뺀다)."""


class CardSkipped(Exception):
    """대회 카드가 아닌 추출 결과 (오류가 아니므로 종료 코드에 영향 없음)."""


@dataclass
class CardBundle:
    """카드 하나를 만들 재료. #14(추출)·#15(검증)·#16(병합)이 채운다.

    notices: 그 대회의 공지들. **AI 입력과 같은 순서**(게시일 이른 것부터, 같은 날이면 출처 id순)여야
        근거의 notice 번호(1부터)가 맞는다 (어긋나면 카드를 뺀다). 각 항목은 source_id·url·title·published_at,
        그리고 files(AI에게 넘긴 첨부·이미지 이름 목록, 근거의 file을 대조).
    extraction: AI 출력 그대로. 글은 AI 입력처럼 HTML 이스케이프(&amp; 등)된 상태여도 되고, 내보낼 때 되돌린다.
        사람이 값을 고쳤으면 고친 값을 extraction에 반영해서 넘긴다.
    statuses: 항목 이름 → 검증 결과. {"status": "auto_verified" | "human_verified" | "needs_check",
        "checked_value": 검증한 v0.2 값, "note": 이유, "evidence_failed": 근거를 원문에서 못 찾았으면 true,
        "by": 쓴 도구 (검증기는 "verifier"), "checked_evidence": 사람이 검수한 근거 (verifier.checks.evidence_fingerprint)}.
        사람 검수 기록만 이 밖에서 넣는다 (AI 출력에서 채우지 않는다).
        검증 통과는 checked_value가 지금 extraction 값과 같을 때만 쓴다 (재추출로 값이 바뀌면 "ai"로 내림).
        "needs_check" 문자열만 줘도 된다. 항목이 없으면 "ai"(검증 전).
    deadline_history: #16의 마감 변경 이력 (계약 Change). 형식이 틀린 항목은 빼고 경고한다.
    """

    card_id: str
    notices: list[dict]
    extraction: dict
    statuses: dict = field(default_factory=dict)
    deadline_history: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def build_card(bundle: CardBundle, warnings: list[str] | None = None) -> dict:
    """추출 결과 하나 → 계약 1절 Card.

    warnings: 카드는 내보내지만 확인이 필요한 일(빼낸 이력 등)을 덧붙일 목록.
    Raises:
        CardSkipped: 대회가 아니거나 결과·취소 공지일 때
        CardExportError: id·출처 공지·대회명·주관·교내/외부가 계약을 만족하지 않을 때
    """
    warnings = warnings if warnings is not None else []
    if not isinstance(bundle.card_id, str) or not CARD_ID_PATTERN.fullmatch(bundle.card_id):
        raise CardExportError(f"카드 id 형식: {_short(bundle.card_id)}")
    extraction = bundle.extraction if isinstance(bundle.extraction, dict) else {}
    is_contest = extraction.get("is_contest")
    if not isinstance(is_contest, dict) or is_contest.get("value") is not True:
        raise CardSkipped("대회가 아님 (is_contest)")
    fields = extraction.get("fields")
    if not isinstance(fields, dict):
        raise CardExportError("추출 결과에 fields가 없음")
    kind = _raw_value(fields, "kind")
    if kind in SKIPPED_KINDS:
        raise CardSkipped(f"{kind} 공지")
    if not isinstance(bundle.notices, list) or not bundle.notices:
        raise CardExportError("출처 공지가 없음")
    if not isinstance(bundle.statuses, dict):
        raise CardExportError("statuses가 객체가 아님")
    notices = _checked_notices(bundle.notices)

    title = _required_text(fields, "title", MAX_TITLE_CHARS, warnings)
    organizer = _required_text(fields, "organizer", MAX_ORGANIZER_CHARS, warnings)
    scope = _raw_value(fields, "scope")
    if scope not in SCOPES:
        raise CardExportError(f"교내/외부 값: {_short(scope)}")
    for name in ("title", "organizer", "scope"):
        flag = fields[name].get("flag")
        if flag != "found":
            # 계약상 최상위 값이라 원문 확인 필요를 표시할 자리가 없어 보고로 알린다
            warnings.append(f"{name}의 flag가 {_short(flag)}인데 확정 값처럼 공개됨")
        verification = bundle.statuses.get(name)
        if isinstance(verification, dict) and verification.get("evidence_failed") is True:
            # 최상위 값은 근거를 공개하지 않지만, 근거를 지어냈다면 값도 의심스럽다 (#15)
            warnings.append(f"{name}의 근거를 원문에서 찾지 못했는데 확정 값처럼 공개됨")

    has_warnings = isinstance(extraction.get("warnings"), list) and len(extraction["warnings"]) > 0
    context = _CardContext()
    card_fields = {name: _field(name, fields.get(name), notices, bundle.statuses.get(name), has_warnings, context)
                   for name in CARD_FIELDS}
    if context.dropped_quotes:
        warnings.append(f"근거 글자 합계 상한({MAX_CARD_QUOTE_CHARS}자)을 넘어 근거 {context.dropped_quotes}개를 뺌")
    if context.dropped_notes:
        warnings.append(f"AI note 합계 상한({MAX_CARD_NOTE_CHARS}자)을 넘어 note {context.dropped_notes}개를 뺌")
    if context.missing_files:
        warnings.append(f"이미지·첨부 근거 {context.missing_files}개의 파일 이름이 공지 files에 없어 뺌")
    return {
        "id": bundle.card_id,
        "title": title,
        "organizer": organizer,
        "scope": scope,
        "sources": [{"board_id": notice["board_id"], "url": notice["url"], "title": notice["title"], "posted_at": notice["posted_at"]}
                    for notice in reversed(notices)],  # 계약: 첫 번째가 최신
        "fields": card_fields,
        "deadline_history": _deadline_history(bundle.deadline_history, warnings),
        "meta": _meta(bundle.meta, warnings),
    }


def build_cards_json(bundles: list[CardBundle], boards: list[dict], generated_at: str,
                     blocked: frozenset[str] | set[str] = frozenset()) -> tuple[dict, dict]:
    """카드 묶음 → cards.json 전체.

    blocked: 수동 차단 목록의 카드 id (#65, `exporter/blocklist.py`). 이 카드는 만들지 않는다.
    Returns:
        (cards.json 데이터, 보고 {"rejected": 뺀 카드, "skipped": 대회 아님, "warnings": 내보냈지만 확인할 일,
        "blocked": 차단해 뺀 카드})
        각 항목은 {"id", "reason"}. 같은 id가 다시 나오면 먼저 나온 카드를 쓴다. 차단 이유는 보고에 넣지 않는다.
        차단 목록의 id가 이번 묶음에 없으면(오타·이미 사라진 카드) 조용히 넘어가지 않게 warnings에 남긴다.
    Raises:
        ValueError: generated_at이 시간대가 있는 ISO 8601 시각이 아닐 때 (사이트가 전체를 못 읽게 되므로)
    """
    _check_generated_at(generated_at)
    cards, seen = [], set()
    report = {"rejected": [], "skipped": [], "warnings": [], "blocked": []}
    matched: set[str] = set()
    for bundle in bundles:
        card_id = _printable_id(bundle.card_id)
        if isinstance(bundle.card_id, str) and bundle.card_id in blocked:
            if bundle.card_id not in matched:
                report["blocked"].append({"id": card_id, "reason": "수동 차단 목록"})
            matched.add(bundle.card_id)
            continue
        warnings: list[str] = []
        try:
            card = build_card(bundle, warnings)
            if card["id"] in seen:
                raise CardExportError("같은 카드 id가 두 번 나옴 (먼저 나온 카드를 씀)")
        except CardSkipped as skipped:
            report["skipped"].append({"id": card_id, "reason": str(skipped)})
            continue
        except CardExportError as error:
            report["rejected"].append({"id": card_id, "reason": str(error)})
            continue
        except Exception as error:  # 상류 형식 오류 하나로 내보내기 전체가 멈추지 않게 그 카드만 뺀다
            report["rejected"].append({"id": card_id, "reason": f"처리 중 오류 ({type(error).__name__})"})
            continue
        seen.add(card["id"])
        cards.append(card)
        report["warnings"] += [{"id": card_id, "reason": message} for message in warnings]
    report["warnings"] += [{"id": card_id, "reason": "차단 목록에 있지만 이번 묶음에 없는 카드 (오타이거나 사라진 카드면 목록에서 지우기)"}
                           for card_id in sorted(set(blocked) - matched)]
    data = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "boards": boards, "cards": cards}
    return data, report


def boards_from_sources(sources) -> list[dict]:
    """수집 출처 설정(pipeline/config/sources.toml) → 계약 boards. 게시판 목록 주소가 없으면 null."""
    return [{"id": source.id, "name": _clean(source.name, MAX_SHORT_CHARS)[0], "list_url": _safe_url(source.list_url)}
            for source in sources]


def write_cards_json(path: Path, data: dict) -> None:
    """같은 폴더의 임시 파일에 다 쓴 뒤 바꿔 끼운다 (사이트가 반쯤 쓴 파일을 읽지 않게).

    Raises:
        OSError: 쓰기·바꿔 끼우기 실패 (기존 파일은 그대로, 임시 파일은 지운다)
        ValueError: NaN·Infinity처럼 JSON으로 쓸 수 없는 값
    """
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o644)  # mkstemp는 소유자만 읽게 만든다. 배포 쪽이 다른 사용자여도 읽을 수 있게
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ---- 항목 ----

class _CardContext:
    """카드 하나를 만드는 동안의 합계: 근거 글자(같은 문장은 한 번), AI note 글자, 파일 이름이 안 맞은 근거 수."""

    def __init__(self):
        self.quote_chars = 0
        self.seen_quotes: set[str] = set()
        self.dropped_quotes = 0
        self.note_chars = 0
        self.dropped_notes = 0
        self.missing_files = 0

    def take_quote(self, quote: str) -> bool:
        if quote in self.seen_quotes:
            return True
        if self.quote_chars + len(quote) > MAX_CARD_QUOTE_CHARS:
            self.dropped_quotes += 1
            return False
        self.quote_chars += len(quote)
        self.seen_quotes.add(quote)
        return True

    def take_note(self, note: str) -> bool:
        if self.note_chars + len(note) > MAX_CARD_NOTE_CHARS:
            self.dropped_notes += 1
            return False
        self.note_chars += len(note)
        return True


def _field(name: str, raw, notices: list[dict], verification, has_warnings: bool, context: _CardContext) -> dict:
    """추출 항목 하나 → 계약 Field {value, status, evidence, note}.

    flag: missing → 값 없음, needs_check·conflict·모르는 값 → 원문 확인 필요, found → 검증 결과(없으면 "ai").
    값을 계약 형식으로 만들 수 없거나 고쳐야 했으면 원문 확인 필요로 둔다 (틀린 값을 검증된 것처럼 공개하지 않게).
    """
    raw = raw if isinstance(raw, dict) else {}
    flag = raw.get("flag")
    raw_value = raw.get("value")
    problems: list[str] = []
    notes: list[str] = []
    ai_note = _clean(raw["note"], MAX_TEXT_CHARS)[0] if isinstance(raw.get("note"), str) else ""
    if ai_note and not context.take_note(ai_note):
        ai_note = ""
    if flag not in FLAGS:
        problems.append("모르는 flag")
    if flag == "missing" or (raw_value is None and flag == "found"):
        return _make_field(None, "missing", [], [], [], ai_note)

    status_hint, verification_note, evidence_failed = _verification(verification, raw_value)
    evidence, evidence_problems = _evidence(raw.get("evidence"), notices, context)
    problems += evidence_problems
    if evidence_failed:
        evidence = []
        notes.append("근거 문장을 원문에서 찾지 못함")
    if raw_value is None:
        notes.append("공지마다 값이 다름" if flag == "conflict" else "")
        return _make_field(None, "needs_check", evidence, problems, notes, ai_note)

    value, value_problems = _value(name, raw_value)
    problems += value_problems
    if _has_inferred_year(raw_value):
        notes.append("연도가 공지에 없어 게시일의 연도로 추정함")
    if name == "stages":
        notes += _other_stage_labels(raw_value) + _start_only_stage_notes(raw_value)
    if verification_note:
        notes.append(verification_note)
    if value is None:
        return _make_field(None, "needs_check", evidence, problems, notes, ai_note)

    if flag != "found" or problems or evidence_failed or status_hint == "needs_check":
        status = "needs_check"
    elif status_hint in VERIFIED:
        status = status_hint
    else:
        status = "ai"
    if status == "auto_verified" and has_warnings:
        status = "ai"
        notes.append("공지에 AI에게 주는 지시처럼 보이는 문구가 있어 자동 검증 결과를 쓰지 않음")
    if name == "contact" and status != "human_verified":
        evidence = []  # 문의처 근거에는 담당자 이름이 흔하다. 이름은 패턴으로 못 가려 사람이 본 것만 싣는다
    if status in VERIFIED and not evidence:
        status = "ai"  # 계약: 자동 검증 통과·검수 완료는 원문 근거가 있어야 한다
    if name == "contact" and status != "human_verified" and value.get("email"):
        # 학생도 mju.ac.kr을 써서 부서 메일인지 사람이 확인해야 공개한다. 근거가 없어 검수 완료가 풀린 경우도 비운다
        value = {**value, "email": ""}
        notes.append("문의 이메일은 사람이 검수하기 전이라 비움")
    return _make_field(value, status, evidence, problems, notes, ai_note)


def _make_field(value, status: str, evidence: list[dict], problems: list[str], notes: list[str], ai_note: str) -> dict:
    """note 순서: 내보내기가 고친 문제 → 안내(연도 추정 등) → AI의 note (길이 상한에 내보내기 이유가 잘리지 않게)."""
    parts = []
    if problems:
        parts.append("내보내기 검사: " + ", ".join(dict.fromkeys(problems)))
    parts += [note for note in dict.fromkeys(notes) if note]
    if ai_note:
        parts.append(ai_note)
    item = {"value": value, "status": status, "evidence": evidence}
    if parts:
        item["note"] = _clip(" / ".join(parts), MAX_TEXT_CHARS)
    return item


def _verification(entry, raw_value):
    """statuses의 한 항목 → (상태 또는 None, 덧붙일 note, 근거 대조 실패 여부)."""
    if entry is None:
        return None, "", False
    if entry == "needs_check":
        return "needs_check", "", False
    if not isinstance(entry, dict) or entry.get("status") not in VERIFIED | {"needs_check"}:
        return None, "검증 결과 형식이 달라 쓰지 않음", False
    note = _clean(entry["note"], MAX_TEXT_CHARS)[0] if isinstance(entry.get("note"), str) else ""
    evidence_failed = entry.get("evidence_failed") is True
    if entry["status"] == "needs_check":
        return "needs_check", note, evidence_failed
    if "checked_value" not in entry or entry["checked_value"] != raw_value:
        return None, "검증한 값과 지금 값이 달라 검증 결과를 쓰지 않음", evidence_failed
    return entry["status"], note, evidence_failed


def _value(name: str, raw):
    """항목별 값 변환. (계약 값 또는 None, 문제 목록). 값을 고치거나 일부를 버리면 문제로 남긴다."""
    problems: list[str] = []
    if name == "apply":
        if not isinstance(raw, dict):
            return None, ["신청기간 형식"]
        start, start_problem = _contract_date(raw.get("start"), "시작")
        end, end_problem = _contract_date(raw.get("end"), "마감")
        problems += [problem for problem in (start_problem, end_problem) if problem]
        if start is None and end is None:
            return None, problems or ["신청기간 날짜 없음"]
        if start and end and start[:10] > end[:10]:
            problems.append("마감이 시작보다 빠름")
        return {"start": start, "end": end, "time_given": bool(end and len(end) > 10)}, problems
    if name == "stages":
        if not isinstance(raw, list):
            return None, ["단계 형식"]
        stages = []
        for stage in raw:
            if not isinstance(stage, dict) or stage.get("name") not in STAGE_NAMES:
                problems.append("모르는 단계 이름")
                continue
            end, _, problem = _stage_end(stage)
            if problem:
                problems.append(problem)
            if end is None:
                continue
            stages.append({"name": stage["name"], "end": end, "time_given": len(end) > 10})
        if len(stages) > MAX_LIST_ITEMS:
            problems.append("단계가 너무 많아 자름")
            stages = stages[:MAX_LIST_ITEMS]
        return (stages or None), problems
    if name in ("campus", "grades"):
        if not isinstance(raw, dict):
            return None, [f"{name} 형식"]
        if raw.get("any") is True:
            return "any", problems
        allowed = raw.get("allowed")
        is_valid = _is_campus if name == "campus" else _is_grade
        if not isinstance(allowed, list) or not allowed or not all(is_valid(item) for item in allowed):
            return None, [f"{name} 목록"]
        return list(dict.fromkeys(allowed)), problems
    if name == "enrollment":
        if not isinstance(raw, dict) or not isinstance(raw.get("allowed"), list) or not isinstance(raw.get("excluded"), list):
            return None, ["학적 형식"]
        return {key: _terms(raw[key], problems) for key in ("allowed", "excluded")}, problems
    if name == "team":
        if not isinstance(raw, dict):
            return None, ["팀 형식"]
        team = {key: _head_count(raw.get(key), problems) for key in ("min", "max")}
        individual = raw.get("individual_ok")
        if individual is not None and not isinstance(individual, bool):
            problems.append("개인 참가 여부 형식")
            individual = None
        team["individual_ok"] = individual
        if team["min"] and team["max"] and team["min"] > team["max"]:
            problems.append("최소 인원이 최대보다 큼")
        return team, problems
    if name == "deliverables":
        if not isinstance(raw, dict):
            return None, ["제출물 형식"]
        given = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        if not isinstance(raw.get("tags"), list) or any(tag not in DELIVERABLE_TAGS for tag in given):
            problems.append("모르는 제출물 종류를 뺌")
        tags = [tag for tag in DELIVERABLE_TAGS if tag in given]
        return {"tags": tags, "text": _clean_into(raw.get("text"), MAX_TEXT_CHARS, problems)}, problems
    if name == "benefits":
        if not isinstance(raw, str) or not raw.strip():
            return None, ["혜택 형식"]
        return _clean_into(raw, MAX_TEXT_CHARS, problems), problems
    if name == "mileage":
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            return None, ["마일리지 형식"]
        area, points = raw.get("area"), raw.get("points")
        if area is not None and not isinstance(area, str):
            problems.append("마일리지 영역 형식")
            area = None
        is_number = isinstance(points, (int, float)) and not isinstance(points, bool) and math.isfinite(points) and points >= 0
        if points is not None and not is_number:
            problems.append("마일리지 점수 형식")
            points = None
        area = (_clean_into(area, MAX_AREA_CHARS, problems) or None) if area is not None else None
        return {"text": _clean_into(raw["text"], MAX_TEXT_CHARS, problems), "area": area, "points": points}, problems
    if name == "how_to_apply":
        if not isinstance(raw, dict):
            return None, ["신청 방법 형식"]
        url = raw.get("url")
        safe = _safe_url(html.unescape(url) if isinstance(url, str) else url)
        if url and not safe:
            problems.append("신청 링크가 학교·구글 폼 주소가 아니라 뺌")
        return {"text": _clean_into(raw.get("text"), MAX_TEXT_CHARS, problems), "url": safe}, problems
    if name == "contact":
        if not isinstance(raw, dict):
            return None, ["문의처 형식"]
        return {"dept": _clean_into(raw.get("dept"), MAX_SHORT_CHARS, problems),
                "email": _contact_email(raw.get("email"), problems),
                "phone": _contact_phone(raw.get("phone"), problems)}, problems
    return None, [f"모르는 항목 {name}"]


def _is_campus(item) -> bool:
    return item in CAMPUSES


def _is_grade(item) -> bool:
    return isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 6


def _head_count(item, problems: list[str]):
    if item is None:
        return None
    if isinstance(item, int) and not isinstance(item, bool) and item >= 1:
        return item
    problems.append("인원 수 형식")
    return None


def _terms(items: list, problems: list[str]) -> list[str]:
    """학적 용어 목록. 잘리거나 빠지면 판정이 바뀌므로 문제로 남긴다."""
    terms = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            problems.append("학적 용어 형식")
            continue
        terms.append(_clean_into(item, MAX_TERM_CHARS, problems))
    if len(terms) > MAX_LIST_ITEMS:
        problems.append("학적 용어가 너무 많아 자름")
    return terms[:MAX_LIST_ITEMS]


def _contact_email(value, problems: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append("문의 이메일 형식")
        return ""
    email = _probe(_strip_invisible(html.unescape(value))).strip()  # 전각 ＠ 등도 같은 기준으로 본다
    if not email:
        return ""
    match = EMAIL.fullmatch(email)
    if not match or len(email) > MAX_SHORT_CHARS or not _is_school_domain(match.group(1)):
        problems.append("문의 이메일이 학교 주소가 아니라 뺌")
        return ""
    if _has_personal_contact(email.split("@")[0]):
        problems.append("문의 이메일 앞부분이 휴대폰 번호·학번 같아 뺌")
        return ""
    return email


def _contact_phone(value, problems: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append("문의 전화 형식")
        return ""
    phone, events = _clean(value, MAX_PHONE_CHARS)
    if not phone:
        return ""
    probe = _probe(phone)
    pieces = [re.sub(r"\D", "", piece, flags=re.ASCII) for piece in [probe, *re.split(r"[,/;]", probe)]]
    pieces = ["0" + piece[2:] if piece.startswith("82") else piece for piece in pieces]  # 국가 번호 +82
    if "pii" in events or any(MOBILE_DIGITS.fullmatch(piece) for piece in pieces):
        problems.append("휴대폰 번호라 뺌")
        return ""
    if events or not PHONE.fullmatch(probe) or len(pieces[0]) < 7:
        problems.append("문의 전화 형식")
        return ""
    return phone


def _contract_date(part, label: str):
    """v0.2 {date, time, year_inferred} → 계약 "YYYY-MM-DD" 또는 "YYYY-MM-DD HH:MM". 시각을 채우지 않는다."""
    if part is None:
        return None, None
    if not isinstance(part, dict):
        return None, f"{label} 형식"
    day, time = part.get("date"), part.get("time")
    if day is None:
        return None, None
    if not _is_real_date(day):
        return None, f"{label} 날짜"
    if time is None:
        return day, None
    if not isinstance(time, str) or not TIME_PATTERN.fullmatch(time):
        return day, f"{label} 시각 (날짜만 씀)"
    return f"{day} {time}", None


def _is_real_date(text) -> bool:
    if not isinstance(text, str) or not DATE_PATTERN.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _is_contract_date(text) -> bool:
    """계약 날짜 "YYYY-MM-DD" 또는 "YYYY-MM-DD HH:MM" (사이트 parseContractDate와 같은 기준)."""
    match = CONTRACT_DATE_PATTERN.fullmatch(text) if isinstance(text, str) else None
    return bool(match and _is_real_date(match.group(1)) and (match.group(2) is None or TIME_PATTERN.fullmatch(match.group(2))))


def _has_inferred_year(value) -> bool:
    """v0.2 날짜 {date, time, year_inferred} 중 연도를 추정한 것이 있는지 (apply·stages)."""
    if isinstance(value, dict):
        if value.get("year_inferred") is True:
            return True
        return any(_has_inferred_year(item) for item in value.values() if isinstance(item, (dict, list)))
    if isinstance(value, list):
        return any(_has_inferred_year(item) for item in value)
    return False


def _stage_end(stage: dict) -> tuple[str | None, bool, str | None]:
    """단계 하나의 계약 end → (end 또는 None, start 날짜를 썼는지, 문제).

    end가 있으면 end. end가 틀렸으면 빼고 문제로 남긴다 (시작일로 대신하면 기간이 하루로 보인다).
    end가 비고 start만 있으면: 일정 단계는 모델이 날짜 하나를 start에 넣은 경우라 그 날짜를 쓰고,
    마감 단계(신청·출품)는 시작일을 마감으로 쓰면 D-day가 틀려서 뺀다. 날짜가 아예 없으면(상시 접수) 조용히 뺀다.
    """
    name = stage["name"]
    end, end_problem = _contract_date(stage.get("end"), f"{name} 마감")
    if end is not None or end_problem:
        return end, False, end_problem
    start, start_problem = _contract_date(stage.get("start"), f"{name} 시작")
    if start is None:
        return None, False, start_problem
    if name in DEADLINE_STAGES:
        return None, False, f"마감일 없는 {name} 단계를 뺌"
    return start, True, start_problem


def _start_only_stage_notes(raw) -> list[str]:
    """날짜를 start에만 넣은 일정 단계 (그 날짜를 end로 썼음을 알린다). _value와 같은 판단·같은 개수 상한."""
    names, kept = [], 0
    for stage in raw if isinstance(raw, list) else []:
        if not isinstance(stage, dict) or stage.get("name") not in STAGE_NAMES:
            continue
        end, used_start, _ = _stage_end(stage)
        if end is None:
            continue
        kept += 1
        if kept > MAX_LIST_ITEMS:
            break
        if used_start:
            names.append(stage["name"])
    return [f"날짜가 하나인 {', '.join(dict.fromkeys(names))} 일정은 그 날짜로 표시"] if names else []


def _other_stage_labels(raw) -> list[str]:
    """계약 단계에는 label이 없다. '기타' 단계와 이름이 겹치는 단계(예선 1차·2차)는 원문 표현을 note로 남긴다."""
    stages = [stage for stage in (raw if isinstance(raw, list) else []) if isinstance(stage, dict)]
    names = [stage.get("name") for stage in stages]
    labels = []
    for stage in stages:
        label = stage.get("label")
        if (stage.get("name") == "기타" or names.count(stage.get("name")) > 1) and isinstance(label, str) and label.strip():
            labels.append(_clean(label, MAX_TERM_CHARS)[0])
    return [f"단계 원문 표현: {', '.join(labels)}"] if labels else []


def _evidence(raw, notices: list[dict], context: _CardContext) -> tuple[list[dict], list[str]]:
    """근거 → 계약 evidence. notice 번호를 원문 주소로 바꾸고, 정리·길이·개수·합계 상한을 적용한다.

    번호가 틀린 근거는 버린다. file은 그 공지의 files에 있는 이름일 때만 남긴다 (AI가 지어낸 이름을 공개하지 않게).
    """
    items, problems = [], []
    for item in raw if isinstance(raw, list) else []:
        if len(items) >= MAX_EVIDENCE:
            break  # 근거가 아주 많아도 앞의 몇 개만 정리한다 (느려지지 않게)
        if not isinstance(item, dict) or not isinstance(item.get("quote"), str) or item.get("location") not in EVIDENCE_LOCATIONS:
            continue
        number = item.get("notice")
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(notices):
            problems.append("공지 번호가 틀린 근거를 뺌")
            continue
        quote, events = _clean(item["quote"], MAX_QUOTE_CHARS)
        if not quote:
            continue
        problems += [PROBLEM_TEXT[event] for event in events if event != "clipped"]
        if not context.take_quote(quote):
            continue
        evidence = {"quote": quote, "location": item["location"], "source": notices[number - 1]["url"]}
        file_name = _clean(item["file"], MAX_SHORT_CHARS)[0] if isinstance(item.get("file"), str) else ""
        if file_name and file_name in notices[number - 1]["files"]:
            evidence["file"] = file_name
        elif item["location"] in ("이미지", "첨부"):
            context.missing_files += 1
        items.append(evidence)
    return items, problems


def _checked_notices(raw: list) -> list[dict]:
    """출처 공지 검사. 근거 번호가 이 순서를 가리키므로, 하나라도 틀리거나 순서가 어긋나면 카드를 뺀다."""
    notices = []
    for number, notice in enumerate(raw, start=1):
        if not isinstance(notice, dict):
            raise CardExportError(f"출처 공지 {number}번 형식")
        posted = str(notice.get("published_at") or "")[:10]
        url = _safe_url(notice.get("url"))
        source_id = notice.get("source_id")
        if not _is_real_date(posted) or not url or not isinstance(source_id, str) or not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise CardExportError(f"출처 공지 {number}번의 게시일·주소·출처 id")
        files = notice.get("files") if isinstance(notice.get("files"), list) else []
        notices.append({"board_id": source_id, "url": url, "title": _clean(notice.get("title"), MAX_TITLE_CHARS)[0],
                        "posted_at": posted, "files": {_clean(name, MAX_SHORT_CHARS)[0] for name in files if isinstance(name, str)}})
    order = [(notice["posted_at"], notice["board_id"]) for notice in notices]
    if order != sorted(order):
        raise CardExportError("출처 공지 순서가 AI 입력 순서(게시일 이른 것부터, 같은 날이면 출처 id순)와 다름")
    return notices


def _deadline_history(changes, warnings: list[str]) -> list[dict]:
    """계약 Change. 사이트는 날짜가 틀린 이력이 하나라도 있으면 카드를 통째로 빼므로 여기서 그 항목만 뺀다."""
    if not isinstance(changes, list):
        if changes:
            warnings.append("마감 변경 이력 형식이 달라 뺌")
        return []
    result = []
    for number, change in enumerate(changes, start=1):
        is_valid = (isinstance(change, dict) and change.get("field") == "apply_end"
                    and _is_contract_date(change.get("old")) and _is_contract_date(change.get("new"))
                    and _is_real_date(change.get("checked_at")))
        if not is_valid:
            warnings.append(f"마감 변경 이력 {number}번의 형식이 달라 뺌")
            continue
        result.append({"field": "apply_end", "old": change["old"], "new": change["new"],
                       "old_source": _safe_url(change.get("old_source")), "new_source": _safe_url(change.get("new_source")),
                       "checked_at": change["checked_at"]})
    return result


def _meta(raw, warnings: list[str]) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    meta = {}
    for key in META_KEYS:
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or (key == "checked_at" and not _is_real_date(value))):
            warnings.append(f"meta.{key} 형식이 달라 비움")
            value = None
        meta[key] = _clean(value, MAX_SHORT_CHARS)[0] if value is not None else None
    return meta


def _check_generated_at(text) -> None:
    try:
        parsed = datetime.fromisoformat(text) if isinstance(text, str) and GENERATED_AT_PATTERN.fullmatch(text) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"generated_at이 시간대가 있는 ISO 8601 시각이 아님: {_short(text)}")


def _raw_value(fields: dict, name: str):
    item = fields.get(name)
    return item.get("value") if isinstance(item, dict) else None


def _required_text(fields: dict, name: str, limit: int, warnings: list[str]) -> str:
    value = _raw_value(fields, name)
    text, events = _clean(value, limit) if isinstance(value, str) else ("", set())
    if not text:
        raise CardExportError(f"{name}이 비어 있음")
    if events - {"clipped"}:
        warnings.append(f"{name}에서 " + ", ".join(PROBLEM_TEXT[event] for event in sorted(events - {"clipped"})))
    return text


# ---- 글·주소 ----

def _safe_url(url, allow_http: bool = False) -> str | None:
    """사이트 safeUrl과 같은 기준의 링크 주소. 아니면 None.

    https(글 속 주소 판정에서만 http도), 허용 도메인(하위 포함), 계정 정보·포트 오류 없음, 500자 이하,
    인쇄 가능한 ASCII만(백슬래시 제외: 파이썬과 브라우저가 호스트를 다르게 읽음), 주소 안에 개인 연락처 없음.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS or BACKSLASH in url or any(not "!" <= char <= "~" for char in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # 잘못된 포트면 ValueError
    except ValueError:
        return None
    if parts.scheme not in (("https", "http") if allow_http else ("https",)) or not host or parts.username or parts.password:
        return None
    if not URL_HOST.fullmatch(host) or not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
        return None
    decoded = _fully_decoded(url)
    if decoded is None or _has_personal_contact(decoded) or EMAIL_IN_TEXT.search(_probe(decoded)):
        return None  # 미리 채운 신청 폼 주소 등. 학생도 mju.ac.kr을 써서 학교 메일도 거부
    return url


def _fully_decoded(url: str) -> str | None:
    """판정용으로 주소를 더 바뀌지 않을 때까지 푼다 (%2540 같은 이중 인코딩, + 공백). 3번 풀어도 바뀌면 None."""
    decoded = url
    for _ in range(3):
        unquoted = unquote_plus(decoded)
        if unquoted == decoded:
            return decoded
        decoded = unquoted
    return decoded if unquote_plus(decoded) == decoded else None


def _is_school_domain(domain: str) -> bool:
    domain = domain.lower()
    return domain == ALLOWED_EMAIL_DOMAIN or domain.endswith("." + ALLOWED_EMAIL_DOMAIN)


def _strip_invisible(text: str) -> str:
    """NFC 정규화, 줄바꿈류·모든 공백 문자(&nbsp; 등 범주 Zs) → 보통 공백, 보이지 않는 글자·제어 문자 제거.

    공백을 보통 공백으로 모아야 휴대폰 번호 판정(ASCII 공백만 구분자로 봄)이 &nbsp;로 띄운 번호도 잡는다.
    """
    kept = []
    for char in unicodedata.normalize("NFC", text):
        code = ord(char)
        if code in LINE_BREAK_CODES or unicodedata.category(char) == "Zs":
            kept.append(" ")
        elif unicodedata.category(char) not in REMOVED_CATEGORIES and code not in EXTRA_INVISIBLE_CODES:
            kept.append(char)
    return "".join(kept)


def _probe(text: str) -> str:
    """판정용 사본: 전각 → 반각, 여러 대시 → "-", 가운뎃점 → ".", 다른 문자 체계의 숫자 → 0-9. 길이는 원문과 같다."""
    folded = []
    for char in text:
        code = ord(char)
        if code in DASH_CODES:
            folded.append("-")
        elif code in DOT_CODES:
            folded.append(".")
        elif 0xFF01 <= code <= 0xFF5E:
            folded.append(chr(code - 0xFEE0))
        elif char.isdecimal() and not char.isascii():
            folded.append(str(unicodedata.decimal(char)))
        else:
            folded.append(char)
    return "".join(folded)


def _mask(text: str, pattern: re.Pattern, replace) -> str:
    """판정용 사본에서 찾은 자리를 원문에서 바꾼다. replace(찾은 것) → 바꿀 글 또는 None(그대로)."""
    pieces, last = [], 0
    for match in pattern.finditer(_probe(text)):
        replacement = replace(match)
        if replacement is None:
            continue
        pieces += [text[last:match.start()], replacement]
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _has_personal_contact(text: str) -> bool:
    probe = _probe(text)
    return bool(MOBILE_IN_TEXT.search(probe) or LONG_NUMBER_IN_TEXT.search(probe)
                or any(not _is_school_domain(match.group(1)) for match in EMAIL_IN_TEXT.finditer(probe)))


def _clean(text, limit: int) -> tuple[str, set[str]]:
    """공개할 글 정리 → (글, 일어난 일 {"pii", "url", "clipped"}).

    AI 입력의 HTML 이스케이프를 되돌리고(사이트는 textContent로 그려 화면에 &amp;가 보이므로), 보이지 않는 글자 정리,
    학교 밖 이메일·휴대폰 번호·긴 숫자열 가림, 허용 목록 밖 주소 → "(주소 생략)". 판정은 전각·대시를 정규화한 사본에서 한다.
    """
    if text is None:
        return "", set()
    events: set[str] = set()
    raw = str(text)
    work_limit = max(limit * 3, 1000)  # 정규식이 긴 입력에서 느려지지 않게 먼저 자른다 (결과는 limit 이하라 넉넉함)
    if len(raw) > work_limit:
        events.add("clipped")
        raw = raw[:work_limit]
    cleaned = re.sub(r" {2,}", " ", _strip_invisible(html.unescape(raw))).strip()

    def replace_email(match):
        if _is_school_domain(match.group(1)):
            # 학생도 mju.ac.kr을 써서 부서 메일과 구분할 수 없다. 문제로 세지는 않는다 (문의처 칸은 검수 후 공개)
            return "(학교 이메일 생략)"
        events.add("pii")
        return "(이메일 생략)"

    def replace_url(match):
        found = match.group(0)
        trimmed = found.rstrip(URL_TRAILING)
        candidate = trimmed if trimmed.lower().startswith("http") else "https://" + trimmed
        if _safe_url(candidate, allow_http=True):
            return None
        events.add("url")
        return "(주소 생략)" + found[len(trimmed):]

    def replace_with(label):
        def replace(match):
            events.add("pii")
            return label
        return replace

    cleaned = _mask(cleaned, EMAIL_IN_TEXT, replace_email)
    cleaned = _mask(cleaned, URL_IN_TEXT, replace_url)
    cleaned = _mask(cleaned, MOBILE_IN_TEXT, replace_with("(휴대폰 번호 생략)"))
    cleaned = _mask(cleaned, LONG_NUMBER_IN_TEXT, replace_with("(번호 생략)"))
    if len(cleaned) > limit:
        events.add("clipped")
        cleaned = _clip(cleaned, limit)
    return cleaned, events


def _clean_into(text, limit: int, problems: list[str]) -> str:
    """값 글자 정리. 가리거나 자른 일이 있으면 문제로 남겨 원문 확인 필요로 내린다."""
    cleaned, events = _clean(text, limit)
    problems += [PROBLEM_TEXT[event] for event in sorted(events)]
    return cleaned


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(value) -> str:
    return _clip(repr(value), MAX_REASON_CHARS)


def _printable_id(value) -> str:
    """보고에 찍는 카드 id. 형식이 맞으면 그대로, 아니면 repr (줄바꿈·제어 문자로 로그를 꾸미지 못하게)."""
    return value if isinstance(value, str) and CARD_ID_PATTERN.fullmatch(value) else _short(value)
