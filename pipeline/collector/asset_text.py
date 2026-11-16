"""수집기가 뽑아 둔 첨부 글자 읽기. 매일 추출(AI 입력)과 검증기(근거 대조)가 같이 쓴다.

네트워크 라이브러리를 가져오지 않는다 (검증기가 수집 의존을 지지 않게). 저장·변환은 `collector.assets`.
"""
import re
from pathlib import Path

MAX_TEXT_BYTES = 800_000
# 수집기가 쓰는 꼴(collector.assets: asset_text/<sha256>.txt)만 읽는다. 형식을 먼저 보고 파일시스템에 손댄다
# (UNC 경로 \\host\share는 resolve()만으로도 네트워크 접속이 일어난다, 보안 리뷰)
TEXT_PATH = re.compile(r"asset_text/[0-9a-f]{64}\.txt")
# 첨부 이름에서 바꾸는 글자: 프롬프트가 & < > "를 이스케이프하면 AI가 근거의 file에 "R&amp;D.hwp"처럼 적어
# 이름 대조가 어긋난다. 처음부터 비슷한 전각 글자로 바꿔 이스케이프할 것이 없게 한다
NAME_REPLACEMENTS = str.maketrans({"&": "＆", "<": "＜", ">": "＞", '"': "＂"})


def private_path(data_dir: Path, relative: str) -> Path:
    root = data_dir.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("첨부 경로가 data 밖을 가리킵니다")
    return path


def attachment_names(notice: dict) -> list[str]:
    """공지의 모든 첨부(글자를 뽑았는지와 상관없이)에 매기는 고유 이름. 첨부 순서와 같다.

    이름이 비면 "첨부 N", 같은 이름이 또 나오면 " (2)". 글자를 뽑은 첨부만으로 매기면, 나중에 다른 첨부의 변환이
    성공했을 때 같은 이름이 다른 첨부를 가리키게 된다 (첨부 이름·순서는 공지 판의 content_hash에 들어 있다).
    """
    names, seen = [], set()
    for number, item in enumerate(notice.get("attachments") or [], start=1):
        raw = item.get("name") if isinstance(item, dict) else None
        name = raw.strip().translate(NAME_REPLACEMENTS) if isinstance(raw, str) and raw.strip() else f"첨부 {number}"
        unique, copy = name, 2
        while unique in seen:
            unique, copy = f"{name} ({copy})", copy + 1
        seen.add(unique)
        names.append(unique)
    return names


def attachment_texts(notice: dict, data_dir: Path) -> list[tuple[str, str]]:
    """수집 원문(notices.json의 공지 하나)에서 글자를 뽑아 둔 첨부 → [(첨부 이름, 글자)].

    글자를 뽑지 못한 첨부(이미지만 있는 PDF, 암호 등)·읽을 수 없는 파일·data 밖 경로는 건너뛴다 (사람이 원문에서 본다).
    """
    result = []
    for name, item in zip(attachment_names(notice), notice.get("attachments") or []):
        if (not isinstance(item, dict) or item.get("text_status") != "extracted" or not isinstance(item.get("text_path"), str)
                or not TEXT_PATH.fullmatch(item["text_path"])):
            continue
        try:
            path = private_path(data_dir, item["text_path"])
            if path.stat().st_size > MAX_TEXT_BYTES:
                continue
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        if text:
            result.append((name, text))
    return result
