"""마감 변경 이력의 값이 해당 인용문 자체에서 확인되는지 보수적으로 검사한다."""
import html
import re
import unicodedata
from datetime import date

from verifier.checks import normalize


DATE = re.compile(
    r"(?<![\d.])(?:(?P<year>20\d{2})[.년/\-])?"
    r"(?P<month>\d{1,2})[.월/\-](?P<day>\d{1,2})(?!\d)(?:일|\.)?"
)
WEEKDAY = r"(?:\([월화수목금토일](?:요일)?\))?"
TIME_FORM = r"(?:\d{1,2}:\d{2}|\d{1,2}시(?:\d{1,2}분)?)"
TIME = re.compile(r"(?:(?P<hour>\d{1,2}):(?P<minute>\d{2})|(?P<korean_hour>\d{1,2})시(?:(?P<korean_minute>\d{1,2})분)?)")
BETWEEN = re.compile(rf"^{WEEKDAY}(?P<start>{TIME_FORM})?(?:~|∼|부터)$")
AFTER_WEEKDAY = re.compile(rf"^{WEEKDAY}[,，]?")
APP_CONTEXT = re.compile(r"신청|접수|모집")
SUBMISSION = re.compile(r"제출|출품")
APPLICATION_FORM = re.compile(r"신청서(?:를|의|을)?(?:온라인|방문|이메일)?제출")
ARTIFACT = re.compile(r"작품|영상|결과물|출품|포트폴리오|제안서|기획서|논문|원고|제작물")
OTHER_STAGE = re.compile(r"행사|본선|발표|교육|결선|예선|시상|심사|운영|결과|게시일|작성일|등록일")
START_MARKER = re.compile(r"시작|개시|오픈|부터")
END_PREFIX = re.compile(r"(?:마감|기한|종료)(?:일시|일자|시간|시각|일|시)?(?:은|는|이|가|을|를|:|=)?$")
END_SUFFIX = re.compile(rf"^{WEEKDAY}\s*(?:{TIME_FORM}(?:[~∼]{TIME_FORM})?)?(?:까지|마감|종료)")
TENTATIVE = re.compile(r"추후|미정|예정|미확정|변경가능|연장가능|검토|보류|확정전|아니|아님|아닙|않|없|불가")
TIME_CUE = re.compile(r"오전|오후|정오|자정|(?:\d{1,3}|[Nn])(?:시|:)", re.I)
TIME_TRAILER = re.compile(r"^(?:반|오전|오후|[ap]m|:\d*|\d+(?:분|초)?|분|초)", re.I)
NEGATED_UPDATE = re.compile(
    r"(?:연장|변경|정정)(?:을|를|은|는|이|가)?(?:하지않|되지않|없|불가|아니|"
    r"예정|검토|여부|가능성|고려|계획|취소|확정되지않|확정전)"
)
UNCERTAIN_UPDATE = re.compile(r"취소|철회|가능성|여부|계획|예시|못|안함|안되|안하|안해|사실무근|사실과다")
APPLICATION_UPDATE = re.compile(r"(?:신청|접수|모집|기한|마감).{0,20}(?:연장|변경|정정)|"
                                r"(?:연장|변경|정정).{0,20}(?:신청|접수|모집|기한|마감)")


def compact(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text))
    # 날짜 뒤의 시각과 숫자 사이 공백은 이어 붙이면 다른 숫자가 된다.
    return re.sub(r"\s+", lambda match: " " if match.start() and match.end() < len(text)
                  and text[match.start() - 1].isdigit() and text[match.end()].isdigit() else "", text)


def _clock(match):
    hour = int(match.group("hour") or match.group("korean_hour"))
    minute = int(match.group("minute") or match.group("korean_minute") or 0)
    if hour > 23 or minute > 59:
        raise ValueError("유효하지 않은 시각")
    return f"{hour:02}:{minute:02}"


def _end_clock(tail):
    """끝 날짜 뒤 명확한 24시간 표기만 읽는다. 모호한 추가 시각은 거절한다."""
    tail = tail.lstrip()
    tail = tail[AFTER_WEEKDAY.match(tail).end():]
    match = TIME.match(tail)
    if not match:
        if TIME_CUE.search(tail):
            raise ValueError("해석하지 못한 시각")
        return None
    first = _clock(match)
    rest = tail[match.end():]
    if TIME_TRAILER.match(rest):
        raise ValueError("해석하지 못한 시각")
    if rest.startswith(("~", "∼")):
        second = TIME.match(rest[1:])
        if not second:
            raise ValueError("해석하지 못한 시간 범위")
        last = _clock(second)
        if last < first:
            raise ValueError("시각 범위 역전")
        rest = rest[1 + second.end():]
        if TIME_TRAILER.match(rest):
            raise ValueError("해석하지 못한 종료 시각")
        first = last
    if TIME_CUE.search(rest):
        raise ValueError("추가 시각이 있어 역할이 모호함")
    return first


def _date(match, year=None):
    explicit = match.group("year")
    if not explicit and year is None:
        raise ValueError("연도 없음")
    return date(int(explicit) if explicit else year, int(match.group("month")), int(match.group("day")))


def _quote_deadline(text):
    """(날짜, 시각), None(날짜 없는 근거) 또는 ValueError(모호함)."""
    text = compact(text)
    dates = [item for item in DATE.finditer(text) if not (
        item.start() > 1 and text[item.start() - 1] == ":" and text[item.start() - 2].isdigit())]
    if not dates:
        return None
    if len(dates) > 2:
        raise ValueError("서로 다른 일정이 섞임")
    prefix = text[:dates[0].start()]
    if not APP_CONTEXT.search(prefix) or OTHER_STAGE.search(text):
        raise ValueError("신청·접수 마감인지 불명확")
    # 출품/결과물 제출은 참가 신청과 별도의 마감일일 수 있다.
    # 신청서 자체의 제출만 예외로 두며, 결과물까지 섞이면 보류한다.
    if SUBMISSION.search(text) and (not APPLICATION_FORM.search(prefix) or ARTIFACT.search(text)):
        raise ValueError("참가 신청서와 결과물 제출 기한이 구분되지 않음")
    suffix = text[dates[-1].end():]
    if TENTATIVE.search(text) or APP_CONTEXT.search(suffix):
        raise ValueError("마감이 미확정이거나 다른 신청 일정이 섞임")
    first = _date(dates[0])
    if len(dates) == 1:
        if START_MARKER.search(text) or not (END_PREFIX.search(prefix) or END_SUFFIX.match(suffix)):
            raise ValueError("시작일 또는 기한 역할 불명확")
        end = first
    else:
        between = text[dates[0].end():dates[1].start()]
        matched = BETWEEN.fullmatch(between)
        if not matched:
            raise ValueError("날짜 범위 불명확")
        if matched.group("start"):
            _clock(TIME.fullmatch(matched.group("start")))
        end = _date(dates[1], first.year)
        if end < first:
            raise ValueError("날짜 범위 역전 또는 연도 불명확")
        if START_MARKER.search(suffix):
            raise ValueError("범위 끝 날짜의 역할이 불명확")
    return end.isoformat(), _end_clock(suffix)


def deadline_is_grounded(field):
    """실제 원문 검증과 별도로 추출 마감값이 명확한 신청 마감 인용에 있는지 확인한다."""
    if not isinstance(field, dict) or field.get("flag") != "found" or not isinstance(field.get("value"), dict):
        return False
    end = field["value"].get("end")
    if not isinstance(end, dict) or end.get("year_inferred") is not False:
        return False
    value, clock = end.get("date"), end.get("time")
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            return False
        if clock is not None and (not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock)):
            return False
        evidence = field.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return False
        observed = []
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("quote"), str) or len(item["quote"]) > 500:
                return False
            parsed = _quote_deadline(item["quote"])
            if parsed is not None:
                observed.append(parsed)
        return bool(observed) and all(item == (value, clock) for item in observed)
    except (ValueError, TypeError, OverflowError):
        return False


def _source_text(original, location, file_name):
    if not isinstance(original, dict):
        return None
    if location in ("제목", "본문"):
        value = original.get("title" if location == "제목" else "body_text")
        return value if isinstance(value, str) else None
    if location == "첨부":
        for attachment in original.get("attachments") or []:
            if isinstance(attachment, dict) and attachment.get("name") == file_name and isinstance(attachment.get("text"), str):
                return attachment["text"]
    return None


def _is_sentence_end(source, index):
    char = source[index]
    if char in ";!?":
        return True
    # 날짜의 점과 날짜 뒤 마침표는 앞 날짜의 문맥에 남긴다.
    return char == "." and not (index and source[index - 1].isdigit())


def _quote_contexts(item, originals):
    """문장이 끝날 때까지 줄바꿈을 넘겨 대조하며 너무 긴 문맥은 보류한다."""
    if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
        raise ValueError("근거 형식")
    number = item.get("notice")
    if type(number) is not int or not 1 <= number <= len(originals):
        raise ValueError("근거 공지 번호")
    source = _source_text(originals[number - 1], item.get("location"), item.get("file"))
    if source is None:
        raise ValueError("원문 문맥 없음")
    source, quote = normalize(source), normalize(item["quote"])
    if not quote:
        raise ValueError("빈 근거")
    contexts = []
    start = source.find(quote)
    while start >= 0:
        end = start + len(quote)
        left, right = start, end
        while left > 0 and not _is_sentence_end(source, left - 1):
            left -= 1
            if end - left > 500:
                raise ValueError("원문 문맥이 너무 김")
        while right < len(source) and not _is_sentence_end(source, right):
            right += 1
            if right - left > 500:
                raise ValueError("원문 문맥이 너무 김")
        contexts.append(source[left:right])
        if len(contexts) > 32:
            raise ValueError("동일 근거가 너무 자주 반복됨")
        start = source.find(quote, start + 1)
    if not contexts:
        raise ValueError("근거 원문 없음")
    return contexts


def deadline_context_is_grounded(field, originals):
    """잘린 인용이 숨긴 부정·다른 날짜·시각을 원문 문맥에서도 검사한다."""
    if not deadline_is_grounded(field):
        return False
    end = field["value"]["end"]
    expected = (end["date"], end.get("time"))
    observed = False
    try:
        for item in field["evidence"]:
            if _quote_deadline(item["quote"]) is None:
                continue
            for context in _quote_contexts(item, originals):
                if _quote_deadline(context) != expected:
                    return False
                observed = True
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    return observed


def update_is_grounded(field, stage, check):
    """원문 대조 통과 + 명시적 연장/변경 인용 + 독립 규칙 분류가 같은 경우만 허용."""
    if not isinstance(field, dict) or field.get("flag") != "found" or check is None or check.result != "pass":
        return False
    value = field.get("value")
    if not isinstance(value, str):
        return False
    expected = {"연장": ("extension", r"연장"), "변경": ("change", r"변경|정정")}.get(value)
    if expected is None or stage != expected[0]:
        return False
    evidence = field.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    found = False
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
            return False
        text = compact(item["quote"])
        if NEGATED_UPDATE.search(text):
            return False
        found = found or bool(re.search(expected[1], text))
    return found


def update_context_is_grounded(field, originals):
    """연장·변경 인용이 잘려 숨겨진 부정/미확정과 다른 일정인지 확인한다."""
    if not isinstance(field, dict) or not isinstance(field.get("evidence"), list):
        return False
    found = False
    try:
        for item in field["evidence"]:
            for context in _quote_contexts(item, originals):
                if TENTATIVE.search(context) or NEGATED_UPDATE.search(context) or UNCERTAIN_UPDATE.search(context):
                    return False
                if APPLICATION_UPDATE.search(context):
                    found = True
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    return found
