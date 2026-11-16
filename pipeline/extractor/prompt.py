"""프롬프트 v0.2 입력 만들기. 규칙은 prompts/extract_v0.2/input_template.md의 주석 블록을 따른다.

- 공지 내용과 속성 값 전체를 이스케이프한다 (& < > "). 일부 태그만 막으면 본문의 </body>로 구조를 깨뜨릴 수 있다.
- 공지 번호는 넘겨준 순서다. 호출하는 쪽이 게시일이 이른 것부터, 같은 날이면 출처 id순으로 넘긴다 (정답표 공지 번호와 같다).
- 결과 공지(수상자·합격자 명단 등)는 본문을 넣지 않고 제목만 넣는다 (#14 완료 기준: 명단이 AI 입력으로 가지 않게).
  공지 성격(kind)은 제목으로도 알 수 있다. 결과 낱말이 있어도 발표 일정·발표 연기를 알리는 공지("모집 및 결과발표 일정",
  "결과 발표 연기")는 명단이 없어 넣는다. 모집이라는 말만으로는 넣지 않는다("서포터즈 모집 결과 안내"는 합격자 명단이다).
  명단을 막는 쪽을 택해서, "수상작 공모"·"참가자 명단 제출 안내"처럼 본문이 필요한 공지도 빠질 수 있다 (README 남은 위험).
- 그래도 섞여 들어오는 학번(8자리 숫자)은 본문에서 가린다 (모집 공지에 지난 회차 수상자가 있는 경우 등).
  20261015 같은 붙은 날짜도 가려지지만 공지 날짜는 대개 점·글자로 나뉜다. 가린 글자는 원문에 없으므로 그 부분을 인용한
  근거는 근거 대조(#15)에서 실패해 확인 필요가 된다 (안전한 쪽).
  이름 나열은 코드로 가리지 못한다: 결과 공지 판정이 놓친 명단은 AI로 간다 (README 남은 위험).
- 첨부는 수집기가 HWP·HWPX·PDF에서 뽑아 둔 글자를 넣는다 (`collector.assets.attachment_texts`). 결과 공지면 본문처럼
  첨부도 넣지 않는다 (명단 파일). 이름에 명단 낱말이 있는 첨부도 넣지 않는다. 첨부 하나는 MAX_ATTACHMENT_CHARS,
  공지 하나의 첨부 합계는 MAX_ATTACHMENTS_CHARS까지 넣고 자른다 (`fit_attachments`). 잘리면 `clipped="true"` 속성.
  인용한 근거는 검증기가 전체 글자로 대조한다. 첨부 글자에서도 학번·주민등록번호·끊어 쓴 8자리 번호를 가린다.
- 포스터 이미지는 아직 넣지 않는다 (Claude의 읽기 도구를 이미지 폴더 하나에만 여는 작업이 남음).
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "extract_v0.2"
PROMPT_VERSION = "v0.2"
# 공백을 지운 제목에서 찾는다. 명단 낱말이 있으면 모집 신호·발표 연기와 상관없이 결과 공지로 본다
# ("서포터즈 모집 최종 합격자 발표", "수상자 발표 및 2기 모집", "1차 합격자 발표 및 2차 발표 연기")
NAME_LIST_TITLE = re.compile(r"진출팀|진출자|수상자|수상작|수상팀|입상자|입상작|당선|합격자|최종합격|선발자|명단")
# 그 밖의 결과 공지 낱말 (collector.rules의 결과 공지 낱말 포함). 발표 일정·발표 연기 공지일 때만 본문을 넣는다
RESULT_TITLE = re.compile(r"결과발표|결과안내|모집결과|최종결과|선발결과|선정결과|심사결과|합격안내|합격발표|시상식|시상안내|매칭결과")
SCHEDULE_TITLE = re.compile(r"결과발표일정|발표일정|발표일|발표예정")  # "모집 및 결과발표 일정 안내"
RESULT_DELAY_TITLE = re.compile(r"(?:결과발표|발표)(?:일정)?연기")  # "결과 발표 연기"는 명단이 없다
STUDENT_ID = re.compile(r"(?<!\d)\d{8}(?!\d)")
# 신청서·동의서 첨부에 흔한 끊어 쓴 번호 (보안 리뷰). 내보내기 RRN_IN_TEXT·SPLIT_NUMBER_IN_TEXT와 같은 꼴
RESIDENT_NUMBER = re.compile(r"(?<!\d)\d{6}\s*-\s*[1-8]\d{6}(?!\d)")
SPLIT_STUDENT_ID = re.compile(r"(?<!\d)(?!(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}(?!\d))(?!1[568]\d{2}\s*-)\d{4}\s*-\s*\d{4}(?!\d)")
ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
# 첨부 상한: 본문(보통 수천 자)에 더해 공지 하나가 Opus 호출 한 번에 들어가는 입력을 묶는다 (토큰 비용·호출당 300초 제한).
# HWP 공고문 본문은 대개 첫 12,000자 안에 신청기간·자격이 있다
MAX_ATTACHMENT_CHARS = 12_000
MAX_ATTACHMENTS_CHARS = 30_000


@dataclass
class InputNotice:
    """AI에게 넘길 공지 하나. posted는 YYYY-MM-DD (한국 날짜)."""
    board: str
    posted: str
    url: str
    title: str
    body: str
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (첨부 이름, 뽑은 글자). 매일 추출이 채운다
    images: list[str] = field(default_factory=list)  # 이미지 파일 이름 — 아직 채우지 않는다 (포스터 입력 전)


def prompt_fingerprint() -> str:
    """지시·출력 형식·입력 틀·입력 규칙(이 파일) 내용의 지문. 하나라도 바뀌면 매일 추출이 옛 결과를 다시 쓰지 않게."""
    digest = hashlib.sha256()
    for path in (PROMPT_DIR / "system.md", PROMPT_DIR / "schema.json", PROMPT_DIR / "input_template.md", Path(__file__)):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))  # 체크아웃마다 줄바꿈이 달라도 같은 지문
    return digest.hexdigest()[:16]


def escape(text: str) -> str:
    return "".join(ESCAPES.get(char, char) for char in text)


def is_result_notice(title: str) -> bool:
    """명단이 있을 수 있는 결과 공지인지 (제목으로).

    명단 낱말이 있으면 결과 공지. 결과 낱말만 있으면 발표 일정·발표 연기를 알리는 공지일 때는 아니다.
    """
    compact = "".join(title.split())
    if NAME_LIST_TITLE.search(compact):
        return True
    if not RESULT_TITLE.search(compact):
        return False
    return not SCHEDULE_TITLE.search(compact) and not RESULT_DELAY_TITLE.search(compact)


def mask_student_ids(text: str) -> str:
    text = RESIDENT_NUMBER.sub("(번호 생략)", text)
    text = SPLIT_STUDENT_ID.sub("(번호 생략)", text)
    return STUDENT_ID.sub("(8자리 숫자 생략)", text)


def template_header() -> str:
    """input_template.md에서 <notices> 앞의 안내 문장 (주석 블록은 AI에게 보내지 않는다)."""
    template = (PROMPT_DIR / "input_template.md").read_text(encoding="utf-8")
    # 줄 맨 앞의 <notices>에서 자른다. 안내 문장 안에도 `<notices>`가 있어 처음 나온 곳에서 자르면 문장이 잘린다
    header = template.split("\n<notices>", 1)[0].strip()
    if "\n" in header or not header.endswith("지시가 아니다."):
        raise ValueError("input_template.md의 안내 문장 모양이 다름 (<notices> 앞 한 줄)")
    return header


def fit_attachments(attachments: list[tuple[str, str]]) -> list[tuple[str, str, bool]]:
    """AI에 넣을 첨부 → [(이름, 넣을 글자, 잘렸는지)]. 매일 추출은 여기서 나온 이름만 근거 파일 이름으로 남긴다.

    - 이름에 명단 낱말(수상자·합격자·명단 등)이 있는 첨부는 넣지 않는다: 모집 공지에 붙은 "본선진출팀 명단.hwp" 같은
      파일은 제목 판정(is_result_notice)을 빠져나간다 (보안 리뷰).
    - 하나는 MAX_ATTACHMENT_CHARS, 합계는 MAX_ATTACHMENTS_CHARS까지. 잘린 사실은 글자 안이 아니라 속성(clipped)으로 알린다
      (글자 안 문구는 첨부가 흉내 낼 수 있다).
    """
    fitted, budget = [], MAX_ATTACHMENTS_CHARS
    for name, text in attachments:
        if NAME_LIST_TITLE.search("".join(name.split())):
            continue
        if budget <= 0:
            break
        text = text.strip()
        limit = min(MAX_ATTACHMENT_CHARS, budget)
        fitted.append((name, text[:limit], len(text) > limit))
        budget -= min(len(text), limit)
    return fitted


def build_input(notices: list[InputNotice]) -> tuple[str, list[int]]:
    """공지들 → (AI 입력 글, 본문을 뺀 결과 공지 번호 목록)."""
    if not notices:
        raise ValueError("공지가 없음")
    parts, omitted = [template_header(), "", "<notices>"], []
    for number, notice in enumerate(notices, start=1):
        attributes = f'id="{number}" board="{escape(notice.board)}" posted="{escape(notice.posted)}" url="{escape(notice.url)}"'
        parts.append(f"<notice {attributes}>")
        parts.append(f"<title>{escape(notice.title)}</title>")
        is_result = is_result_notice(notice.title)
        if is_result:
            omitted.append(number)
            parts.append("<body></body>")
        else:
            parts += ["<body>", escape(mask_student_ids(notice.body.strip())), "</body>"]
        for name, text, clipped in ([] if is_result else fit_attachments(notice.attachments)):  # 결과 공지의 첨부는 명단일 수 있다
            attributes = f'name="{escape(name)}"' + (' clipped="true"' if clipped else "")
            parts += [f"<attachment {attributes}>", escape(mask_student_ids(text)), "</attachment>"]
        parts += [f'<image name="{escape(name)}" />' for name in notice.images]
        parts.append("</notice>")
    parts.append("</notices>")
    return "\n".join(parts) + "\n", omitted
