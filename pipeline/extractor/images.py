"""포스터 이미지를 AI 입력으로 (#14). 기본은 꺼져 있다 (`python -m extractor daily --images`, 공개 설정 `images = true`).

- 이 파일은 프롬프트 지문(prompt_fingerprint)에 넣지 않는다: 이미지가 없는 공지까지 다시 뽑게 되므로, 규칙이 바뀌면
  INPUT_MARK의 번호를 올려 이미지가 있는 공지만 다시 뽑는다.
- **도구는 계속 모두 끈다.** 이미지는 Claude의 파일 읽기 도구로 읽히지 않고, 파이프라인이 직접 읽어 표준 입력
  (`--input-format stream-json`)의 image 블록으로 넣는다 (`extractor/claude.py`). 모델이 파일시스템에 닿는 길이 없어
  공지 속 "~/.ssh를 읽어라" 같은 주입이 따를 도구가 없다.
- 수집기가 내려받은 파일(`data/assets/<sha256>.bin`)만 읽는다: 경로 모양을 먼저 보고, data 밖이면 읽지 않고, 바이트의
  sha256이 수집 기록과 같아야 한다 (다른 파일로 바뀌었으면 넣지 않는다).
- 형식은 파일 앞부분 바이트로 본다 (PNG·JPEG·GIF·WebP. 확장자·서버가 준 형식은 믿지 않는다).
- 공지 하나에 MAX_IMAGES장, 한 장 MAX_IMAGE_BYTES까지 (토큰 비용·호출당 시간. 포스터는 대개 한두 장이다).
- 이름은 "이미지 N.확장자"로 매긴다 (N은 공지의 이미지 순서). 내려받은 원래 이름·alt 글은 공지가 정하는 글이라 쓰지 않는다.
  AI가 근거의 file에 이 이름을 적고, 검증기는 이미지 근거를 글자 대조 없이 "사람 확인"으로 돌린다 (verifier/checks.py).
- 결과 공지(수상자 명단 등)의 이미지는 넣지 않는다 (호출하는 쪽이 is_result_notice로 거른다. 명단 사진일 수 있다).
  **남은 위험:** 모집 공지의 이미지에 이름·학번이 찍혀 있으면 가리지 못하고 AI로 간다 (글자와 달리 코드로 가릴 수 없다).
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from collector.asset_text import private_path

# 입력 종류 표시 (daily.INPUTS 뒤에 붙인다). 이 파일의 규칙(상한·이름·형식)이 바뀌면 번호를 올려 옛 결과를 다시 뽑게 한다
INPUT_MARK = "images-v1"
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ASSET_PATH = re.compile(r"assets/[0-9a-f]{64}\.bin")
SHA256 = re.compile(r"[0-9a-f]{64}")
SIGNATURES = (  # (앞부분 바이트, 형식, 확장자)
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


@dataclass(frozen=True)
class PosterImage:
    name: str
    media_type: str
    data: bytes


def image_refs(notice: dict) -> list[tuple[int, dict]]:
    """넣을 수 있어 보이는 이미지 기록 → [(공지 안 순서 1부터, 기록)]. 파일은 읽지 않는다 (수집 기록만 본다).

    매일 추출이 "이 공지에 이미지 입력이 있는가"(입력 종류 표시)를 정할 때도 쓴다.
    """
    refs = []
    for number, item in enumerate(notice.get("images") or [], start=1):
        if (isinstance(item, dict) and item.get("download_status") == "downloaded"
                and isinstance(item.get("local_path"), str) and ASSET_PATH.fullmatch(item["local_path"])
                and isinstance(item.get("sha256"), str) and SHA256.fullmatch(item["sha256"])
                and type(item.get("size_bytes")) is int and 0 < item["size_bytes"] <= MAX_IMAGE_BYTES):
            refs.append((number, item))
        if len(refs) == MAX_IMAGES:
            break
    return refs


def media_type_of(data: bytes) -> tuple[str, str] | None:
    """(형식, 확장자). 넣을 수 없는 형식이면 None."""
    for signature, media_type, extension in SIGNATURES:
        if data.startswith(signature):
            return media_type, extension
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def poster_images(notice: dict, data_dir: Path) -> list[PosterImage]:
    """수집 원문(notices.json의 공지 하나) → AI에 넣을 이미지. 읽을 수 없거나 바뀐 파일·모르는 형식은 건너뛴다."""
    images = []
    for number, item in image_refs(notice):
        try:
            path = private_path(data_dir, item["local_path"])
            if path.stat().st_size > MAX_IMAGE_BYTES:
                continue
            data = path.read_bytes()
        except (OSError, ValueError):
            continue
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            continue
        kind = media_type_of(data)
        if kind is None:
            continue
        images.append(PosterImage(f"이미지 {number}.{kind[1]}", kind[0], data))
    return images
