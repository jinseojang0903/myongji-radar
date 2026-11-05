"""프롬프트 v0.2 입력 만들기. 규칙은 prompts/extract_v0.2/input_template.md의 주석 블록을 따른다.

- 공지 내용과 속성 값 전체를 이스케이프한다 (& < > "). 일부 태그만 막으면 본문의 </body>로 구조를 깨뜨릴 수 있다.
- 공지 번호는 넘겨준 순서다. 호출하는 쪽이 게시일이 이른 것부터, 같은 날이면 출처 id순으로 넘긴다 (정답표 공지 번호와 같다).
- 결과 공지(수상자·합격자 명단 등)는 본문을 넣지 않고 제목만 넣는다 (#14 완료 기준: 명단이 AI 입력으로 가지 않게).
  공지 성격(kind)은 제목으로도 알 수 있다. 제목에 모집 신호가 함께 있으면("모집 및 결과발표 일정") 모집 공지로 보고 넣는다.
- 그래도 섞여 들어오는 학번(8자리 숫자)은 본문에서 가린다 (모집 공지에 지난 회차 수상자가 있는 경우 등).
  20261015 같은 붙은 날짜도 가려지지만 공지 날짜는 대개 점·글자로 나뉜다. 가린 글자는 원문에 없으므로 그 부분을 인용한
  근거는 근거 대조(#15)에서 실패해 확인 필요가 된다 (안전한 쪽).
  이름 나열은 코드로 가리지 못한다: 결과 공지 판정이 놓친 명단은 AI로 간다 (README 남은 위험).
- 첨부 글자와 이미지는 #12(이미지·첨부 내려받기) 전이라 아직 넣지 않는다. 넣지 않은 것은 실행 기록(meta)에 남긴다.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "extract_v0.2"
PROMPT_VERSION = "v0.2"
# 공백을 지운 제목에서 찾는다. 명단 낱말이 있으면 모집 신호·발표 연기와 상관없이 결과 공지로 본다
# ("서포터즈 모집 최종 합격자 발표", "수상자 발표 및 2기 모집", "1차 합격자 발표 및 2차 발표 연기")
NAME_LIST_TITLE = re.compile(r"진출팀|진출자|수상자|수상작|수상팀|입상자|입상작|당선|합격자|최종합격|선발자|명단")
# 그 밖의 결과 공지 낱말 (collector.rules의 결과 공지 낱말 포함). 모집 신호·발표 연기와 함께 있으면 모집·일정 공지로 본다
RESULT_TITLE = re.compile(r"결과발표|결과안내|최종결과|선발결과|선정결과|심사결과|합격안내|시상식|시상안내|매칭결과")
RECRUIT_TITLE = re.compile(r"모집|참가신청|신청안내|접수안내|공모안내|개최안내")
RESULT_DELAY_TITLE = re.compile(r"(?:결과발표|발표)(?:일정)?연기")  # "결과 발표 연기"는 명단이 없다
STUDENT_ID = re.compile(r"(?<!\d)\d{8}(?!\d)")
ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


@dataclass
class InputNotice:
    """AI에게 넘길 공지 하나. posted는 YYYY-MM-DD (한국 날짜)."""
    board: str
    posted: str
    url: str
    title: str
    body: str
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (파일 이름, 뽑은 글자) — #12 뒤
    images: list[str] = field(default_factory=list)  # 이미지 파일 이름 — #12 뒤


def escape(text: str) -> str:
    return "".join(ESCAPES.get(char, char) for char in text)


def is_result_notice(title: str) -> bool:
    """명단이 있을 수 있는 결과 공지인지 (제목으로).

    명단 낱말이 있으면 결과 공지. 결과 낱말만 있으면 모집 신호("모집 및 결과발표 일정")나 발표 연기가 함께 있을 때는 아니다.
    """
    compact = "".join(title.split())
    if NAME_LIST_TITLE.search(compact):
        return True
    if not RESULT_TITLE.search(compact):
        return False
    return not RECRUIT_TITLE.search(compact) and not RESULT_DELAY_TITLE.search(compact)


def mask_student_ids(text: str) -> str:
    return STUDENT_ID.sub("(8자리 숫자 생략)", text)


def template_header() -> str:
    """input_template.md에서 <notices> 앞의 안내 문장 (주석 블록은 AI에게 보내지 않는다)."""
    template = (PROMPT_DIR / "input_template.md").read_text(encoding="utf-8")
    return template.split("<notices>", 1)[0].strip()


def build_input(notices: list[InputNotice]) -> tuple[str, list[int]]:
    """공지들 → (AI 입력 글, 본문을 뺀 결과 공지 번호 목록)."""
    if not notices:
        raise ValueError("공지가 없음")
    parts, omitted = [template_header(), "", "<notices>"], []
    for number, notice in enumerate(notices, start=1):
        attributes = f'id="{number}" board="{escape(notice.board)}" posted="{escape(notice.posted)}" url="{escape(notice.url)}"'
        parts.append(f"<notice {attributes}>")
        parts.append(f"<title>{escape(notice.title)}</title>")
        if is_result_notice(notice.title):
            omitted.append(number)
            parts.append("<body></body>")
        else:
            parts += ["<body>", escape(mask_student_ids(notice.body.strip())), "</body>"]
        for name, text in notice.attachments:
            parts += [f'<attachment name="{escape(name)}">', escape(mask_student_ids(text.strip())), "</attachment>"]
        parts += [f'<image name="{escape(name)}" />' for name in notice.images]
        parts.append("</notice>")
    parts.append("</notices>")
    return "\n".join(parts) + "\n", omitted
