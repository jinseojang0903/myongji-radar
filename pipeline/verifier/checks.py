"""AI 추출 결과를 그대로 믿지 않는다 (#15).

1. 근거 대조: AI가 인용한 근거 문장이 원문(제목·본문·글자를 뽑은 첨부)에 실제로 있는지 글자 단위로 본다.
   공백·보이지 않는 글자·전각·물결/줄표/따옴표/가운뎃점 변형만 맞추고, 말을 바꾼 인용은 실패로 본다.
   **통과는 "인용이 원문에 있다"는 뜻일 뿐, 값이 그 인용에서 나왔다는 뜻이 아니다** (값 일치는 교차 검증이 맡는다).
2. 상식 검사: 형식 검사(시작 > 마감 등)는 내보내기가 한다. 여기서는 형식은 맞지만 이상한 값을 본다.
   게시일과 동떨어진 연도, 첫 공지 게시일보다 이른 신청 마감, 신청 기간과 "신청" 단계 마감이 다른 경우.

근거 대조에 실패하면 needs_check + evidence_failed로 내린다. 내보내기는 그 항목의 근거를 공개하지 않는다
(사람이 검수한 항목이어도: 사람이 본 것은 그때의 근거다). 상식 검사만 실패하면 사람이 검수한 값은 그대로 둔다.
통과해도 auto_verified로 올리지 않는다: 계약상 auto_verified는 다른 모델과의 일치까지 봐야 하는데,
교차 검증 모델이 아직 정해지지 않았다. 그때까지 통과 항목은 "ai"(확인 전)로 남는다.
TODO(jinseojang0903/2026-11-05): 교차 검증 모델이 정해지면 근거·상식·모델 일치를 모두 통과하고
extraction.warnings(주입 의심)가 없는 항목만 auto_verified로.
"""
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from exporter.cards import DASH_CODES, DOT_CODES, EXTRA_INVISIBLE_CODES, REMOVED_CATEGORIES, SKIPPED_KINDS

# 내보내기의 CARD_FIELDS에 카드 최상위 값(title·organizer·scope)과 kind를 더한 것.
# 최상위 값은 근거를 공개하지 않지만, 지어낸 근거면 내보내기가 경고하도록 대조한다.
FIELDS = ["title", "organizer", "scope", "kind", "apply", "stages", "campus", "grades", "enrollment", "team",
          "deliverables", "benefits", "mileage", "how_to_apply", "contact"]
VERIFIER = "verifier"  # 이 도구가 쓴 statuses 표시 (다시 검증할 때 지우고 새로 쓴다)
YEAR_MARGIN = 1  # 게시 연도 ±1년 밖이면 의심 (연말 공지의 이듬해 일정은 허용)
MIN_QUOTE_CHARS = 4  # 정규화 뒤 이보다 짧은 인용("3명", "전 학년")은 어디에나 들어맞아 코드로 확인했다고 볼 수 없다
MAX_QUOTE_CHARS = 500  # 이보다 긴 인용은 정상 출력이 아니다 (내보내기는 200자까지 공개). 대조 비용도 막는다
# 한글 인용이 이 글자 바로 뒤에서 시작하면 낱말 중간이다 ("가능합니다"가 "불가능합니다"에 맞지 않게)
NEGATION_PREFIXES = set("불비미무부")
# 부정 접두로 뜻이 뒤집히는 인용("가능합니다" ← "불가능합니다", "전공자 대상" ← "비전공자 대상")을 두 가지로 막는다 (#49):
# 1) 낱말 경계: 부정 글자가 낱말의 첫 글자이고 인용이 그 바로 뒤에서 시작하면 (원문의 공백 위치로 판단. "참가비 지원"의
#    "비"는 앞 낱말의 끝이라 해당 없음)
# 2) 붙여 쓴 글("참가불가", "중복지원불가"): 낱말 경계로는 안 보이므로, 뜻이 뒤집히는 흔한 낱말로 시작하는 인용은
#    부정 글자 바로 뒤면 막는다
NEGATABLE_STARTS = ("가능", "필요", "해당", "허용", "참가", "참여", "제출", "신청", "인정", "지급", "포함", "대상", "선발", "공개",
                    "합격", "선정", "등록", "접수", "승인", "충족", "완료", "동의", "필수", "이수", "적격", "적합", "제한")
# 인용 바로 뒤에 오면 뜻이 뒤집히는 말 ("개인 참가 불가"에서 "개인 참가"만 인용, #49). 정규화가 공백을 지우므로
# 띄어 쓴 것도 바로 이어 붙은 것으로 본다. 조사 한 글자는 건너뛴다 ("개인 참가는 불가"). 막는 쪽으로 넓게 잡는다.
# 인용이 문장부호로 끝나거나 뒤가 다음 줄이면(줄 단위 목록 "대상: 재학생⏎제외 대상: 휴학생") 보지 않는다
TRAILING_NEGATIONS = ("불가", "불필요", "불허", "불참", "금지", "제외", "제한", "없", "아니", "아님", "아닙", "않", "못", "안됨",
                      "안됩", "안돼", "안되", "어렵", "곤란", "거부", "거절", "반려", "취소")
NOT_NEGATIONS = ("불가피", "불가분")  # "불가"로 시작하지만 부정이 아님
TRAILING_PARTICLES = set("은는이가도만을를")
TRAILING_WINDOW = 8  # 인용 뒤에서 볼 글자 수: 조사 1 + 가장 긴 표현(부정 글자 1 + 낱말 최대 3)보다 넉넉히
LINE_BREAKS = set("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")  # str.splitlines()가 줄을 나누는 글자
MAX_EVIDENCE = 50  # 항목당 근거 수 상한 (넘으면 실패: 대조 비용이 커지고 정상 출력이 아니다)
KST = timezone(timedelta(hours=9))

# 표기만 다른 글자를 하나로 (NFKC가 전각 ASCII는 이미 바꾼다). 내보내기의 대시·가운뎃점 목록에 물결·따옴표를 더한다
PERIOD_CODES = {0x3002, 0xFF61}  # 내보내기 DOT_CODES에 있지만 마침표다
TILDE_CODES = {0x223C, 0x301C, 0xFF5E, 0x02DC}
DOUBLE_QUOTE_CODES = {0x201C, 0x201D, 0x201E, 0x201F, 0x00AB, 0x00BB}
SINGLE_QUOTE_CODES = {0x2018, 0x2019, 0x201A, 0x201B}
EXTRA_DOT_CODES = {0x318D, 0x119E, 0x2022, 0x2219}  # 0x119E: NFKC가 0x318D(ㆍ)를 바꾼 글자
FOLD = ({code: "~" for code in TILDE_CODES} | {code: "-" for code in DASH_CODES}
        | {code: '"' for code in DOUBLE_QUOTE_CODES} | {code: "'" for code in SINGLE_QUOTE_CODES}
        | {code: chr(0x00B7) for code in (DOT_CODES - PERIOD_CODES) | EXTRA_DOT_CODES}
        | {code: "." for code in PERIOD_CODES})
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class Normalized(str):
    """정규화한 글. word_starts[i]: i번째 글자가 원문에서 낱말의 첫 글자였는지 (공백을 지운 뒤에도 낱말 경계를 알 수 있게).
    line_starts[i]: i번째 글자 앞에 원문 줄바꿈이 있었는지 (줄 단위 목록에서 다음 줄을 인용의 뒷말로 보지 않게)."""
    word_starts: tuple = ()
    line_starts: tuple = ()


def normalize(text: str) -> Normalized:
    """대조용 정규화. HTML 이스케이프를 풀고 NFKC, 보이지 않는 글자 제거, 기호 변형 통일, 영문 소문자.

    공백은 지우되 숫자와 숫자 사이의 공백은 하나 남긴다 ("10월 2\\n1." 이 "10월21."로 붙어 "10월 21"과 맞지 않게).
    NFKC 전후로 기호를 바꾼다: NFKC가 일부 변형(0x318D 등)을 다른 글자로 먼저 바꾸기 때문.
    """
    text = unicodedata.normalize("NFKC", html.unescape(text).translate(FOLD)).translate(FOLD)
    kept = []
    for char in text:
        if char in LINE_BREAKS:
            kept.append("\n")
        elif char.isspace():
            kept.append(" ")
        elif ord(char) not in EXTRA_INVISIBLE_CODES and unicodedata.category(char) not in REMOVED_CATEGORIES:
            kept.append(char)
    collapsed = re.sub(" +", " ", re.sub(r"[ \n]*\n[ \n]*", "\n", "".join(kept))).strip().casefold()
    chars, starts, lines, previous, after_newline = [], [], [], None, False
    for index, char in enumerate(collapsed):
        if char in " \n":
            if collapsed[index - 1].isdigit() and index + 1 < len(collapsed) and collapsed[index + 1].isdigit():
                chars.append(" ")
                starts.append(False)
                lines.append(False)
            previous = " "
            after_newline = after_newline or char == "\n"
            continue
        chars.append(char)
        starts.append(previous is None or not previous.isalnum())  # 공백·문장부호 뒤면 낱말의 첫 글자
        lines.append(after_newline)
        previous, after_newline = char, False
    result = Normalized("".join(chars))
    result.word_starts = tuple(starts)
    result.line_starts = tuple(lines)
    return result


def contains_quote(source: str, quote: str) -> bool:
    """정규화한 원문에 정규화한 인용이 낱말을 자르지 않고 있는지.

    숫자로 시작·끝나는 인용은 숫자 경계를 지킨다 ("3명"이 "13명"에, "3"이 "3.5"·"100"이 "100,000"에 맞지 않게).
    한글 인용은 낱말 첫머리의 부정 글자(불·비·미·무·부) 바로 뒤에서 시작하지 않는다 ("전공자 대상"이 "비전공자 대상"에).
    붙여 쓴 글에서도 뜻이 뒤집히는 흔한 낱말로 시작하는 인용은 부정 글자 바로 뒤에서 시작하지 않는다 ("가능"이 "참가불가능"에).
    한글로 끝나는 인용 바로 뒤에 부정 표현(불가·없음·않음·제외·미제출 등)이 오면 맞지 않는다 ("개인 참가"가 "개인 참가 불가"에).
    source가 normalize()의 결과가 아니면(낱말 경계를 모름) 모든 한글 인용에 부정 글자 검사를 한다 (안전한 쪽).
    값과 인용이 맞는지는 보지 않는다 (교차 검증 몫).
    """
    word_starts = getattr(source, "word_starts", None)
    start = source.find(quote)
    while start != -1:
        end = start + len(quote)
        before, after = source[max(0, start - 2):start], source[end:end + 2]
        is_cut_before = quote[0].isdigit() and _continues_number(before[::-1])
        is_cut_after = quote[-1].isdigit() and _continues_number(after)
        after_negation = before[-1:] in NEGATION_PREFIXES and _is_hangul(quote[0])
        knows_words = word_starts is not None and len(word_starts) == len(source)
        prefix_starts_word = not knows_words or word_starts[start - 1]
        # 붙여 쓴 글 대비 규칙은 인용이 원문에서 낱말 중간에서 시작할 때만 ("참가비 접수 기간"의 "접수"는 띄어 쓴 새 낱말)
        quote_starts_mid_word = not knows_words or not word_starts[start]
        is_mid_word = after_negation and (prefix_starts_word or (quote_starts_mid_word and quote.startswith(NEGATABLE_STARTS)))
        if not (is_cut_before or is_cut_after or is_mid_word or _negated_after(source, quote, end)):
            return True
        start = source.find(quote, start + 1)
    return False


def _negated_after(source: str, quote: str, end: int) -> bool:
    """인용이 한글로 끝나고 원문에서 바로 뒤(정규화로 공백이 지워진 다음 글자부터, 조사 한 글자는 건너뜀)가 부정 표현인지.

    예 (정규화 뒤): "개인참가" + "불가…", "개인참가" + "는불가…", "서류" + "미제출…". 뒤가 다음 줄이면 보지 않는다.
    """
    if not _is_hangul(quote[-1]):
        return False
    line_starts = getattr(source, "line_starts", None)
    if line_starts and len(line_starts) == len(source) and end < len(source) and line_starts[end]:
        return False
    following = source[end:end + TRAILING_WINDOW]
    if following[:1] in TRAILING_PARTICLES and _is_negation(following[1:]):
        return True
    return _is_negation(following)


def _is_negation(text: str) -> bool:
    if text.startswith(NOT_NEGATIONS):
        return False
    if text.startswith(TRAILING_NEGATIONS):
        return True
    return text[:1] in NEGATION_PREFIXES and text[1:].startswith(NEGATABLE_STARTS)


def _is_hangul(char: str) -> bool:
    return 0xAC00 <= ord(char) <= 0xD7A3


def _continues_number(neighbor: str) -> bool:
    """인용 바로 옆(가까운 글자부터)이 숫자를 이어 가는지: 숫자, 또는 소수점·자릿점 뒤 숫자."""
    return bool(neighbor) and (neighbor[0].isdigit() or (neighbor[0] in ".," and neighbor[1:2].isdigit()))


@dataclass
class FieldCheck:
    """항목 하나의 검증 결과.

    evidence_failures: 근거 문제 (지어냄·틀린 번호·파일 이름·원문 없음). 있으면 근거를 공개하지 않는다.
    sanity_failures: 상식 검사 문제. unverifiable: 코드로 대조하지 못해 사람이 볼 근거 (이미지 등).
    """
    evidence_failures: list[str] = field(default_factory=list)
    sanity_failures: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return list(dict.fromkeys(self.evidence_failures + self.sanity_failures))

    @property
    def result(self) -> str:
        if self.failures:
            return "fail"
        return "unverifiable" if self.unverifiable else "pass"


def check_extraction(extraction: dict, notices: list[dict], originals: list[dict | None]) -> dict[str, FieldCheck]:
    """추출 결과 하나 → {항목: FieldCheck}.

    근거는 flag와 상관없이 있으면 대조한다 (확인 필요 항목도 내보내기가 근거를 공개하므로). flag missing은 근거를 쓰지 않아 뺀다.
    상식 검사는 flag found이고 값이 있는 일정 항목만.
    notices: 묶음의 공지 (AI 입력 순서. published_at으로 게시일, files로 이미지·첨부 이름을 본다).
    originals: notices와 같은 순서의 원문 {title, body_text, attachments[{name, text?}]}. 못 찾으면 None.
    """
    fields = extraction.get("fields") if isinstance(extraction.get("fields"), dict) else {}
    cache: dict = {}
    checks = {}
    for name in FIELDS:
        raw = fields.get(name)
        if not isinstance(raw, dict) or raw.get("flag") == "missing":
            continue
        if raw.get("flag") != "found" and not raw.get("evidence"):
            continue  # 근거 없는 확인 필요 항목은 내보내기가 그대로 확인 필요로 보여 준다
        check = FieldCheck()
        _check_evidence(raw.get("evidence"), notices, originals, check, cache)
        checks[name] = check
    kind = fields["kind"].get("value") if isinstance(fields.get("kind"), dict) else None
    if not (isinstance(kind, str) and kind in SKIPPED_KINDS):
        _check_dates(fields, notices, checks)
    return checks


def _check_evidence(evidence, notices, originals, check: FieldCheck, cache: dict) -> None:
    if not isinstance(evidence, list) or not evidence:
        check.evidence_failures.append("근거 없음")
        return
    if len(evidence) > MAX_EVIDENCE:
        check.evidence_failures.append(f"근거가 {MAX_EVIDENCE}개를 넘음")
        return
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
            check.evidence_failures.append("근거 형식")
            continue
        number = item.get("notice")
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(originals):
            check.evidence_failures.append("근거의 공지 번호가 틀림")
            continue
        location, file_name = item.get("location"), item.get("file")
        if location not in ("제목", "본문", "이미지", "첨부"):
            check.evidence_failures.append("모르는 근거 위치")
            continue
        if location in ("이미지", "첨부"):
            files = notices[number - 1].get("files")
            if not isinstance(file_name, str) or not isinstance(files, list) or file_name not in files:
                # 위치만 이미지·첨부로 적어 대조를 피하지 못하게 한다
                check.evidence_failures.append(f"근거 파일 이름이 공지 {number}의 파일 목록에 없음")
                continue
        if location == "이미지":
            check.unverifiable.append("이미지 근거 (사람 확인)")
            continue
        original = originals[number - 1]
        if original is None:
            check.evidence_failures.append(f"공지 {number}의 원문을 찾지 못해 대조 못 함")
            continue
        key = (number, location, file_name if location == "첨부" else None)  # file은 첨부일 때만 문자열로 확인됨
        if key not in cache:
            source = _source_text(original, location, file_name)
            cache[key] = normalize(source) if source is not None else None
        if cache[key] is None:
            check.unverifiable.append("글자를 뽑지 않은 첨부 근거 (사람 확인)")
            continue
        if len(item["quote"]) > MAX_QUOTE_CHARS:
            check.evidence_failures.append(f"근거가 {MAX_QUOTE_CHARS}자를 넘음")
            continue
        quote = normalize(item["quote"])
        if not quote or not contains_quote(cache[key], quote):
            check.evidence_failures.append(f"근거 문장을 공지 {number} {location}에서 찾지 못함")
        elif len(quote) < MIN_QUOTE_CHARS:
            # 원문에 있기는 하지만 어디에나 들어맞을 만큼 짧다: 지어낸 것은 아니므로 숨기지 않고 사람이 본다
            check.unverifiable.append("근거가 너무 짧아 코드로 확인 못 함 (사람 확인)")


def _source_text(original: dict, location: str, file_name) -> str | None:
    if location == "제목":
        return original.get("title") if isinstance(original.get("title"), str) else ""
    if location == "본문":
        return original.get("body_text") if isinstance(original.get("body_text"), str) else ""
    for attachment in original.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("name") == file_name and isinstance(attachment.get("text"), str):
            return attachment["text"]
    return None


def _check_dates(fields: dict, notices: list[dict], checks: dict[str, FieldCheck]) -> None:
    dated = [name for name in ("apply", "stages") if name in checks and fields[name].get("flag") == "found"
             and fields[name].get("value") is not None]
    if not dated:
        return
    posted = sorted(day for day in (_posted_day(notice.get("published_at")) for notice in notices) if day)
    if not posted:
        for name in dated:
            checks[name].unverifiable.append("공지 게시일을 읽지 못해 날짜 검사를 못 함")
        return
    first_year, last_year = posted[0].year - YEAR_MARGIN, posted[-1].year + YEAR_MARGIN
    years = f"{posted[0].year}년" if posted[0].year == posted[-1].year else f"{posted[0].year}~{posted[-1].year}년"
    for name in dated:
        for day in _dates(name, fields[name]["value"]):
            if not first_year <= day.year <= last_year:
                checks[name].sanity_failures.append(f"{day.year}년은 게시 연도({years})와 동떨어짐")
                break
    apply_end = _end_of(fields["apply"]["value"]) if "apply" in dated else None
    if apply_end and apply_end < posted[0]:
        checks["apply"].sanity_failures.append("신청 마감이 첫 공지 게시일보다 이름")
    if apply_end and "stages" in dated and isinstance(fields["stages"]["value"], list):
        stage_ends = {_end_of(stage) for stage in fields["stages"]["value"] if isinstance(stage, dict) and stage.get("name") == "신청"}
        stage_ends.discard(None)
        if stage_ends and apply_end not in stage_ends:
            for name in ("apply", "stages"):
                checks[name].sanity_failures.append("신청 기간 마감과 \"신청\" 단계 마감이 다름")


def _posted_day(value) -> date | None:
    """게시 시각 → 한국 날짜. 시간대가 있으면 한국 시간으로 바꾼다 (UTC 23시는 한국 다음 날)."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return _parse(value[:10])
    return (moment.astimezone(KST) if moment.tzinfo else moment).date()


def _dates(name, value) -> list[date]:
    parts = [value] if name == "apply" else (value if isinstance(value, list) else [])
    days = []
    for part in parts:
        if isinstance(part, dict):
            for key in ("start", "end"):
                day = _parse(part[key].get("date")) if isinstance(part.get(key), dict) else None
                if day:
                    days.append(day)
    return days


def _end_of(part) -> date | None:
    if not isinstance(part, dict) or not isinstance(part.get("end"), dict):
        return None
    return _parse(part["end"].get("date"))


def _parse(text) -> date | None:
    """형식이 틀린 날짜는 None (형식 문제는 내보내기가 따로 잡는다)."""
    if not isinstance(text, str) or not DATE_PATTERN.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def evidence_fingerprint(evidence) -> list:
    """사람 검수 기록(checked_evidence)과 비교할 근거 모양: (공지 번호, 위치, 파일, 정규화한 인용)을 정렬한 목록."""
    items = evidence if isinstance(evidence, list) else []
    # file이 None인 근거와 문자열인 근거가 섞여도 정렬되게 키에서 None을 ""로 본다 (#49)
    return sorted(([item.get("notice"), item.get("location"), item.get("file"), normalize(item["quote"])]
                  for item in items if isinstance(item, dict) and isinstance(item.get("quote"), str)
                  and isinstance(item.get("notice"), int) and isinstance(item.get("location"), str)
                  and (item.get("file") is None or isinstance(item.get("file"), str))),
                  key=lambda entry: (entry[0], entry[1], entry[2] or "", entry[3]))


def merge_statuses(existing, extraction: dict, checks: dict[str, FieldCheck]) -> dict:
    """기존 statuses에 검증 결과를 더한다.

    - 이 도구가 전에 쓴 항목(by: verifier)은 지운다. 그때 덮어쓴 사람 검수 기록(previous)이 있으면 되살려 다시 판단한다.
    - 근거 문제: needs_check + evidence_failed (사람이 검수한 항목도. 내보내기가 근거를 공개하지 않는다)
    - 사람 검수(human_verified)는 값과 근거가 검수 때와 같을 때만 믿는다 (checked_value, checked_evidence).
      근거 기록이 없는 사람 검수는 근거를 모두 코드로 대조해 통과했고 문의처가 아닐 때만 믿는다
      (사람이 본 적 없는 이미지 근거·연락처 문장이 "검수 완료"로 공개되지 않게).
    - 상식 문제만: needs_check. 단 믿을 수 있는 사람 검수는 그대로 둔다.
    - 통과·대조 불가: 기존 값을 그대로 둔다 (올리지 않는다).
    """
    statuses = {}
    for name, entry in (existing.items() if isinstance(existing, dict) else []):
        if isinstance(entry, dict) and entry.get("by") == VERIFIER:
            if isinstance(entry.get("previous"), dict):
                statuses[name] = entry["previous"]
            continue
        statuses[name] = entry
    fields = extraction.get("fields") if isinstance(extraction.get("fields"), dict) else {}
    for name, check in checks.items():
        raw = fields[name]
        value = raw.get("value")
        previous = statuses.get(name)
        is_human = isinstance(previous, dict) and previous.get("status") == "human_verified"
        human_problem = _human_problem(name, previous, raw, check) if is_human else None
        if not check.failures and not human_problem:
            continue
        if is_human and not check.evidence_failures and not human_problem:
            continue  # 상식 검사만 걸렸고 사람이 지금 값·근거를 봤다
        reasons = check.failures + ([human_problem] if human_problem else [])
        entry = {"status": "needs_check", "checked_value": value, "note": "; ".join(dict.fromkeys(reasons)),
                 "evidence_failed": bool(check.evidence_failures or human_problem), "by": VERIFIER}
        if is_human:
            entry["previous"] = previous  # 다시 검증해 문제가 사라지면 되살린다
        statuses[name] = entry
    return statuses


def _human_problem(name: str, record: dict, raw: dict, check: FieldCheck) -> str | None:
    if record.get("checked_value") != raw.get("value"):
        return None  # 값이 바뀐 검수 기록은 내보내기가 이미 쓰지 않는다 ("ai"로 내림)
    if "checked_evidence" in record:
        if record["checked_evidence"] != evidence_fingerprint(raw.get("evidence")):
            return "사람이 검수한 근거와 지금 근거가 다름"
        return None
    if check.unverifiable or name == "contact":
        return "사람이 검수한 근거 기록이 없음"
    return None


def fail_all(extraction, existing, reason: str) -> dict:
    """검증 중 예상 못 한 오류가 난 카드: missing이 아닌 모든 항목을 needs_check + evidence_failed로 (검증 없이 통과하지 않게)."""
    fields = extraction.get("fields") if isinstance(extraction, dict) and isinstance(extraction.get("fields"), dict) else {}
    checks = {name: FieldCheck(evidence_failures=[reason]) for name in FIELDS
              if isinstance(fields.get(name), dict) and fields[name].get("flag") != "missing"}
    return merge_statuses(existing, {"fields": fields}, checks)
