"""보수적인 본문 규칙. 값의 추출과 참가 가능 판정·사람 검수는 구분한다."""
import re
from datetime import date


class TextView:
    """분절된 HTML 텍스트를 검색하되 근거는 원문 오프셋으로 돌려준다."""

    def __init__(self, text, location="body"):
        self.original = text
        self.location = location
        self.positions = [i for i, char in enumerate(text) if not char.isspace()]
        self.text = "".join(text[i] for i in self.positions)

    def evidence(self, start, end, rule):
        a, b = self.positions[start], self.positions[end - 1] + 1
        return {"location": self.location, "start": a, "end": b,
                "quote": self.original[a:b], "rule": rule}


def field(value=None, evidence=None, status=None, reason=None):
    state = status or ("extracted" if value is not None else "not_stated")
    return {"value": value, "status": state,
            "contract_status": {"extracted": "ai", "needs_review": "needs_check", "not_stated": "missing"}[state],
            "evidence": evidence or [], "reason": reason}


def classify(title, body, config):
    title_view, body_view = TextView(title, "title"), TextView(body)
    excluded = []
    for word in config["non_activity"]["keywords"]:
        if word in title_view.text:
            p = title_view.text.index(word)
            excluded.append(title_view.evidence(p, p + len(word), "non_activity_title"))
    types, evidence = [], []
    # 제목의 활동 신호가 있으면 본문에 우연히 언급된 다른 활동은 유형에 넣지 않는다.
    for view in (title_view, body_view):
        for kind, words in config["activity_types"].items():
            for word in words:
                if word in view.text:
                    p = view.text.index(word)
                    types.append(kind)
                    evidence.append(view.evidence(p, p + len(word), "activity_keyword"))
                    break
        if types:
            break
    stage = "unknown"
    stage_evidence = []
    for name, pattern in [
        ("cancellation", r"(?:개최|행사|모집|대회|프로그램)취소|취소안내"),
        ("result_delayed", r"결과발표연기|결과.*연기"),
        ("results", r"결과발표|결과안내|모집결과|심사결과|선발결과|선정결과|최종합격자|합격자발표|합격결과|합격안내|합격발표|진출팀|수상자|입상자|시상식|매칭결과"),
        ("extension", r"(?:모집|신청|접수|기간).*연장|연장안내"),
        ("change", r"변경|정정"),
        ("recruitment", r"모집|참가안내|신청안내|개최안내|프로그램안내|참여안내|공모전안내"),
    ]:
        match = re.search(pattern, title_view.text)
        if (name == "results" and match and match.group() == "합격자발표"
                and "모집" in title_view.text and title_view.text[match.end():].startswith(("일정", "예정"))):
            continue
        if match:
            stage = name
            stage_evidence.append(title_view.evidence(*match.span(), "notice_stage"))
            break
    classification = "non_activity" if excluded else "candidate" if types else "needs_review"
    return {"activity": field(classification, excluded or evidence, reason="키워드 규칙이며 사람 검수 전"),
            "types": field(sorted(set(types)) if not excluded else [], evidence),
            "notice_kind": field(stage, stage_evidence, status="extracted" if stage != "unknown" else "needs_review"),
            "open_for_application": None}


# 공백을 제거한 TextView 전용. 연도 없는 날짜는 후보로만 남긴다.
DATE = re.compile(r"(?<![\d.])(?:(?P<year>20\d{2})[.년/-])?(?P<month>\d{1,2})[.월/-](?P<day>\d{1,2})(?!\d)(?:일|\.)?")
TIME = re.compile(r"^(?:\([월화수목금토일](?:요일)?\))?[,，]?(?P<hour>\d{1,2})(?::(?P<minute>\d{2})|시(?:(?P<minute_kr>\d{1,2})분)?)(?!\d)")
TIME_SIGNAL = re.compile(
    r"^(?:\([월화수목금토일](?:요일)?\))?[,，]?"
    r"(?:(?:오전|오후)?\d{1,3}(?::\d{0,3}|시(?:\d{1,3}분)?(?:반)?)"
    r"|오전|오후|정오|자정|[Nn]시|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열(?:한|두)?)시)")
TIME_TRAILER = re.compile(r"^(?:오전|오후|[ap]m(?![A-Za-z])|:\d*|\d+(?:분|초)?)", re.I)
DATE_LABELS = re.compile(
    r"(?P<application>신청기간|접수기간|모집기간|신청기한|접수기한|신청마감|접수마감|모집마감|접수(?=[:：]))"
    r"|(?P<submission>제출기간|제출기한|제출마감|출품작제출기한|작품제출기한)"
    r"|(?P<event>행사일시|교육일시|운영일시|행사기간|교육기간|개최일시|일시(?=[:：]))")
STOP = re.compile(r"(?<![\d.])\d{1,2}\.(?=[가-힣A-Za-z])|[가-하]\.|신청방법|접수처|신청서배포|제출서류|프로그램일정|교육시간표|문의|장소[:：]")


def unread_clock_end(text):
    """날짜에 붙은 괄호·시각 라벨만 넘기고 일반 안내 문장을 검색하지 않는다."""
    prefix = re.match(r"^(?:\([월화수목금토일](?:요일)?\))?[,，]?(?:\()?(?:(?:마감|종료)시간[:：]?)?(?:\()?", text)
    signal = TIME_SIGNAL.match(text[prefix.end():])
    if not signal:
        return 0
    end = prefix.end() + signal.end()
    return end + 1 if text[end:end + 1] == ")" else end


def read_time(suffix):
    """24시간 표기만 읽고, 해석하지 못한 시각 신호와 근거 길이를 함께 보존한다."""
    original = TextView(suffix)
    suffix = original.text
    def trailer(offset):
        if offset >= len(suffix):
            return None
        raw_tail = original.original[original.positions[offset]:]
        extra = TIME_TRAILER.match(raw_tail)
        adjacent = original.positions[offset] == original.positions[offset - 1] + 1
        if not extra and adjacent:
            extra = re.match(r"^(?:반|분|초)(?=$|까지|부터|[^가-힣A-Za-z])", raw_tail)
        return extra
    tm = TIME.match(suffix)
    if not tm:
        consumed = unread_clock_end(suffix)
        return None, None, "해석하지 못한 시각 표기" if consumed else None, consumed
    hour = int(tm.group("hour"))
    minute = int(tm.group("minute") or tm.group("minute_kr") or 0)
    time_value = f"{hour:02}:{minute:02}" if hour < 24 and minute < 60 else None
    issue = None if time_value else "유효하지 않은 시각"
    end_time, consumed = None, tm.end()
    tail = suffix[consumed:]
    # 3시반·17:00pm 등을 3:00·17:00으로 조용히 잘라 읽지 않는다.
    extra = trailer(consumed)
    if extra:
        return time_value, None, "해석하지 못한 시각 표기", consumed + extra.end()
    time_range = re.match(r"^[~∼～-](\d{1,2}):(\d{2})(?!\d)", tail)
    if time_range:
        hour_end, minute_end = map(int, time_range.groups())
        consumed += time_range.end()
        if hour_end < 24 and minute_end < 60:
            end_time = f"{hour_end:02}:{minute_end:02}"
            if time_value and end_time < time_value:
                issue = "시간 범위 역전 또는 익일 여부 확인 필요"
        else:
            issue = "유효하지 않은 종료 시각"
        extra = trailer(consumed)
        if extra:
            issue, consumed = "해석하지 못한 종료 시각 표기", consumed + extra.end()
    elif tail.startswith(("~", "∼", "～", "-")):
        signal_end = unread_clock_end(tail[1:])
        if signal_end:
            issue, consumed = "해석하지 못한 종료 시각 표기", consumed + 1 + signal_end
    return time_value, end_time, issue, consumed


def extract_dates(view):
    fields = {name: field() for name in ("application_period", "submission_period", "event_period")}
    buckets = {"application": [], "submission": [], "event": []}
    labels = list(DATE_LABELS.finditer(view.text))
    for i, label in enumerate(labels):
        end = min(len(view.text), label.end() + 170, labels[i + 1].start() if i + 1 < len(labels) else len(view.text))
        stop = STOP.search(view.text, label.end(), end)
        if stop:
            end = stop.start()
        segment = view.text[label.end():end]
        # 표의 열 이름 다음에 다른 단계 날짜가 이어지는 경우를 단일 마감으로 오인하지 않는다.
        if not segment.startswith((":", "：")):
            if segment.startswith(("내", "이후", "이전", "후", "전", "중")):
                continue  # '신청기간 내 제출'은 새 일정 라벨이 아니다.
            buckets[label.lastgroup].append(field(evidence=[view.evidence(label.start(), label.end(), "date_label_without_separator")],
                status="needs_review", reason="일정 라벨 뒤 구분자 없음: 원문 확인 필요"))
            continue
        matches = [m for m in DATE.finditer(segment) if not (
            m.start() > 1 and segment[m.start() - 1] == ":" and segment[m.start() - 2].isdigit())]
        if not matches:
            buckets[label.lastgroup].append(field(evidence=[view.evidence(label.start(), label.end(), "unread_date_label")],
                status="needs_review", reason="일정 라벨은 있으나 날짜를 해석하지 못함"))
            continue
        # 이후 표·다른 일정까지 끌어오지 않도록 세 날짜 이상이면 확정하지 않는다.
        entries, time_ends = [], []
        for index, m in enumerate(matches):
            y, month, day = m.group("year"), int(m.group("month")), int(m.group("day"))
            iso, issue = None, None
            try:
                date(int(y) if y else 2000, month, day)
                if y:
                    iso = date(int(y), month, day).isoformat()
                else:
                    issue = "연도 미기재"
            except ValueError:
                issue = "유효하지 않은 날짜"
            next_date = matches[index + 1].start() if index + 1 < len(matches) else len(segment)
            a, b = label.end() + m.end(), label.end() + next_date
            raw_suffix = view.original[view.positions[a]:view.positions[b - 1] + 1] if a < b else ""
            time_value, end_time, time_issue, time_end = read_time(raw_suffix)
            if time_issue:
                issue = time_issue
            time_ends.append(time_end)
            entries.append({"date": iso, "time": time_value, "precision": "minute" if time_value else "date",
                            "end_time": end_time, "raw": m.group(), "issue": issue})
        # 같은 명시적 범위 안의 2026.10.1 ~ 10.5는 두 번째 연도만 공유한다.
        if len(entries) == 2 and entries[0]["date"] and not matches[1].group("year"):
            between = segment[matches[0].end():matches[1].start()]
            if re.search(r"[~∼～]|부터", between):
                try:
                    second = date(int(matches[0].group("year")), int(matches[1].group("month")), int(matches[1].group("day")))
                    if second.isoformat() >= entries[0]["date"]:
                        entries[1].update(date=second.isoformat(), year_basis="same_explicit_range")
                        if entries[1]["issue"] == "연도 미기재":
                            entries[1]["issue"] = None
                except ValueError:
                    pass
        is_range = len(matches) == 2 and bool(re.search(r"[~∼～]|부터", segment[matches[0].end():matches[1].start()]))
        valid = all(e["date"] and not e["issue"] for e in entries) and (len(entries) == 1 or is_range)
        if is_range and entries[0]["date"] and entries[1]["date"] and entries[0]["date"] > entries[1]["date"]:
            valid = False
        if (is_range and entries[0]["date"] == entries[1]["date"] and entries[0]["time"] and entries[1]["time"]
                and entries[0]["time"] > entries[1]["time"]):
            valid = False
            entries[1]["issue"] = entries[1]["issue"] or "같은 날의 기간 시각 역전"
        # '접수: 날짜'는 시작인지 마감인지 단정할 수 없으므로 후보만 보존한다.
        explicit_deadline = bool(re.search(r"마감|기한|까지", label.group() + segment))
        boundary = "range" if is_range else "deadline" if explicit_deadline else "event_date" if label.lastgroup == "event" else "unspecified"
        last = matches[-1]
        evidence_end = label.end() + last.end() + time_ends[-1]
        value = {"start": entries[0] if is_range else None,
                 "end": entries[-1] if is_range or explicit_deadline else None,
                 "dates": entries, "boundary": boundary, "timezone": "Asia/Seoul"}
        buckets[label.lastgroup].append(field(value, [view.evidence(label.start(), evidence_end, "labeled_date")],
            status="extracted" if valid and boundary != "unspecified" else "needs_review",
            reason=None if valid and boundary != "unspecified" else "연도·날짜·시각·기간 역할 확인 필요"))
    for kind, name in (("application", "application_period"), ("submission", "submission_period"), ("event", "event_period")):
        candidates = buckets[kind]
        if len(candidates) == 1:
            fields[name] = candidates[0]
        elif candidates:
            # 복수 일정은 임의로 가장 늦은 날짜를 대표 마감으로 선택하지 않는다.
            fields[name] = field({"candidates": candidates}, [e for c in candidates for e in c["evidence"]],
                                 status="needs_review", reason="동일 종류의 복수 일정: 원문 대조 필요")
    return fields


ELIGIBILITY_LABEL = re.compile(r"참가자격|신청자격|지원자격|참여자격|모집대상|참가대상|참여대상|신청대상|지원대상|교육대상|대상(?=[:：])")
SECTION_END = re.compile(r"(?<![\d.])\d{1,2}\.(?=[가-힣A-Za-z])")


def eligibility_spans(view):
    spans = []
    for match in ELIGIBILITY_LABEL.finditer(view.text):
        end = min(len(view.text), match.end() + 600)
        stop = SECTION_END.search(view.text, match.end(), end)
        if stop:
            end = stop.start()
        # 명시적인 다음 항목을 경계로 사용한다.
        stop = re.search(r"신청방법|접수기간|주요일정|교육기간|참가신청|문의처", view.text[match.end():end])
        if stop:
            end = match.end() + stop.start()
        if end > match.end():
            spans.append((match.start(), end))
    return spans


def extract_conditions(view):
    spans = eligibility_spans(view)
    conditions = [view.evidence(a, b, "eligibility_section") for a, b in spans]
    fields = {key: field() for key in ("grades", "student_status", "eligible_campuses", "major_restrictions", "team_size", "participation_mode", "venue_campuses")}
    values = {key: [] for key in fields}
    evidence = {key: [] for key in fields}
    patterns = {
        "grades": r"(?<!\d)(?:[1-6](?:[~∼～,-][1-6])?학년(?!도)(?:이상|이하)?|전학년|학년제한없음)",
        "student_status": r"정규학기등록학부재학생|재학생|재적생|휴학생|수료생|초과학기생|졸업유예자|대학원생|졸업생|편입생",
        "eligible_campuses": r"자연캠퍼스|인문캠퍼스|자연캠|인문캠|양캠퍼스|캠퍼스간연합가능|캠퍼스제한없음",
        "major_restrictions": r"[가-힣·]{2,25}(?:전공|학과|대학)(?:소속|재학생|학생)|전공무관|학과제한없음|공과대학생",
        "team_size": r"[1-9]\d?[~∼～-][1-9]\d?(?:명|인)(?:이하|이내)?(?:의)?팀|(?:팀원|팀당|팀별)(?:최대|총)?[1-9]\d?(?:명|인)|[1-9]\d?(?:명|인)(?:이내|이하|이상)(?:로구성된|의)?팀",
        "participation_mode": r"개인(?:참가|지원|신청)(?:가능|자)?|개인혹은|개인또는|팀(?:참가|지원|신청)|팀별참가자",
    }
    for start, end in spans:
        for key, pattern in patterns.items():
            for match in re.finditer(pattern, view.text[start:end]):
                values[key].append(match.group())
                evidence[key].append(view.evidence(start + match.start(), start + match.end(), "explicit_condition"))
    # 팀 구성은 자격 항목 밖에 따로 있을 수 있다. 역할·단계별 예외는 원문 확인 대상으로 유지.
    for match in re.finditer(r"팀구성(?:인원|원수)[:：]?\d+(?:[~∼～-]\d+)?명|팀원최대\d+명|팀당총\d+명", view.text):
        values["team_size"].append(match.group())
        evidence["team_size"].append(view.evidence(*match.span(), "team_context"))
    # 행사 장소는 별도 필드이며 신청 자격으로 승격하지 않는다. 연락처 구간 제외.
    for match in re.finditer(r"(?:교육장소|행사장소|개최장소|장소)[:：][^※]{0,100}", view.text):
        segment = match.group()
        cut = re.search(r"문의|연락처|신청방법|대상", segment)
        if cut:
            segment = segment[:cut.start()]
        for campus in re.finditer(r"자연캠퍼스|인문캠퍼스|자연캠|인문캠", segment):
            values["venue_campuses"].append(campus.group())
            evidence["venue_campuses"].append(view.evidence(match.start(), match.start() + campus.end(), "venue_only"))
    for key, items in values.items():
        if items:
            # 자격은 긍정/부정·팀 대표·혜택 조건이 섞일 수 있어 단어만으로 허용/제외를 확정하지 않는다.
            fields[key] = field({"mentions": sorted(set(items))}, evidence[key],
                                status="extracted" if key == "venue_campuses" else "needs_review",
                                reason=None if key == "venue_campuses" else "조건 언급 발견: 허용·제외·예외는 근거 구간 확인 필요")
    return fields, conditions


def analyze_notice(notice, config):
    body = TextView(notice.get("body_text", ""))
    classification = classify(notice["title"], body.original, config)
    conditions, eligibility = extract_conditions(body)
    dates = extract_dates(body)
    notices = []
    if notice.get("images"):
        notices.append({"code": "image_only" if not body.text else "images_present",
                        "message": "이미지에 상세 안내가 있습니다. 일정과 참가 조건을 원문에서 확인해주세요."})
    if notice.get("attachments"):
        notices.append({"code": "attachments_present", "message": "첨부파일에 추가 조건이 있을 수 있습니다. 신청 전 확인해주세요."})
    if classification["notice_kind"]["value"] in ("results", "result_delayed", "cancellation"):
        notices.append({"code": "follow_up", "message": "결과·연기·취소 등 후속 공지입니다. 신규 모집으로 판단하지 마세요."})
    missing = [key for key, value in {**dates, **conditions}.items() if value["status"] == "not_stated"]
    if missing:
        notices.append({"code": "missing_fields", "fields": missing, "message": "본문에서 확인하지 못한 항목이 있습니다. 미기재는 제한 없음을 뜻하지 않습니다."})
    return {"source_id": notice["source_id"], "external_id": notice["external_id"],
            "source_version_id": notice["version_id"], "rules_version": config["version"],
            "title": notice["title"], "source_url": notice["url"],
            "classification": classification, "fields": {**dates, **conditions},
            "eligibility_evidence": eligibility, "review_status": "pending",
            "notices": notices, "eligibility_decision": "not_evaluated"}
