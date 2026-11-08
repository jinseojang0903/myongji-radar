"""규칙이 찾은 종료 후보를 공지 원문 전체로 다시 확인한다.

확실한 종료를 놓치는 것보다 예정·부분 결과·개별 신청 취소를 종료로
잘못 고정하는 것이 더 위험하다. 애매하거나 이미지에만 있는 내용은
False를 돌려 병합에서 사람의 확인을 기다린다.
"""

import html
import re
import unicodedata


# 공지 한 곳이라도 현재 종료를 의심하게 만드는 표현이 있으면 보류한다.
# 예를 들어 결과 공지에 다른 일정의 "예정"이 있어도 False가 될 수 있다.
UNCERTAIN = re.compile(
    r"예정|연기|지연|변경|정정|검토|계획|미정|미확정|추후|가능성|여부|"
    r"보류|유보|철회|번복|확정전|가정|예시|모의|소문|오보|허위|사실이아니|사실무근|사실과다|"
    r"않|아니|아닙|아님|아닌|아닐|없|불가|미발표|취소전|취소철회|취소번복"
)
INDIVIDUAL_CANCEL = re.compile(
    r"(?:신청|접수|등록|참가|참여|지원)(?:자|자의)?(?:은|는|이|가|을|를|의)?취소"
    r"|취소(?:신청|방법|절차|가능|문의)"
)
CANCELLATION_CONFLICT = re.compile(
    r"정상(?:적(?:으로)?)?(?:진행|개최|운영)|재개|재모집|"
    r"(?:다시|계속|그대로).{0,20}(?:진행|개최|운영|열)|"
    r"(?:개최|진행|운영)(?:은|는|이|가|을|를)?(?:유지|계속)|"
    r"취소(?:은|는|이|가|을|를)?취소"
)
PARTIAL_RESULT = re.compile(r"예선|중간|1차|2차|일차|이차|본선진출|최종진출")

# 주어와 취소를 가까이 연결해 '대회 참가 신청이 취소됨'을 대회 취소로 보지 않는다.
CANCELLED = re.compile(
    r"(?:공모전|경진대회|대회|행사|프로그램|콘테스트)"
    r"(?:은|는|이|가|을|를)?(?:부득이하게|불가피하게)?"
    r"(?:개최|진행|운영)?(?:이|가|을|를)?취소"
    r"(?:되었습니다|됐습니다|되었음을|됐음을|됩니다|합니다|하였습니다|했습니다)"
)

# 제목의 '결과 발표'나 추출된 짧은 인용만으로는 실제 발표인지 알 수 없다.
# 본문에서 최종 결과 또는 최종 수상자를 발표한다는 완료형 문장을 요구한다.
FINAL_RESULTS = re.compile(
    r"최종(?:심사|선정)?(?:결과|수상자|수상팀|수상작|입상자|입상팀|입상작)"
    r"(?:명단)?(?:은|는|이|가|을|를)?(?:(?:다음과|아래와)같이)?"
    r"(?:발표|공지|공개)(?:합니다|하였습니다|했습니다|되었습니다|드립니다)"
)


def _compact(value):
    text = unicodedata.normalize("NFKC", html.unescape(value))
    return re.sub(r"\s+", "", text)


def terminal_notice_is_grounded(original: dict, stage: str) -> bool:
    """원문의 명백한 대회 취소/최종 결과 발표만 True로 반환한다.

    수집 스키마의 ``title``과 ``body_text``를 사용한다. 첨부·이미지는
    판독하지 않으므로 본문이 없거나 긍정 문장이 없으면 False다. 규칙
    분류와 다르거나 전체 문맥에 불확실한 신호가 있어도 False다.
    """
    if stage not in ("results", "cancellation") or not isinstance(original, dict):
        return False
    title, body = original.get("title"), original.get("body_text")
    if not isinstance(title, str) or not isinstance(body, str) or not title.strip() or not body.strip():
        return False
    title, body = _compact(title), _compact(body)
    text = title + "\n" + body
    if UNCERTAIN.search(text) or INDIVIDUAL_CANCEL.search(text):
        return False
    if stage == "cancellation":
        return not CANCELLATION_CONFLICT.search(text) and bool(CANCELLED.search(body))
    # 결과 발표 취소는 대회 자체가 끝났다는 뜻이 아니다.
    if "취소" in text or PARTIAL_RESULT.search(text):
        return False
    return bool(FINAL_RESULTS.search(body))
