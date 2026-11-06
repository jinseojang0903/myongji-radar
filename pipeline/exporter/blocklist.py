"""수동 차단 목록 읽기 (#65). 형식은 `config/blocked_cards.toml` 머리 주석.

형식이 조금이라도 틀리면 BlocklistError: 차단이 조용히 무시돼 내려야 할 카드가 공개되는 일이 없게 한다.
내보내기에 연결하는 것은 #65의 다음 PR이다 (그 전에는 목록에 넣어도 카드가 빠지지 않는다).
"""
import tomllib
from datetime import date
from pathlib import Path

from exporter.cards import CARD_ID_PATTERN

ENTRY_KEYS = {"card_id", "reason", "since"}
MAX_REASON_CHARS = 100


class BlocklistError(ValueError):
    """차단 목록 형식 오류. 메시지는 몇 번째 칸·줄의 어느 키인지 (이유 문구·파일 내용은 담지 않는다)."""


def load_blocked(path: Path) -> dict[str, dict]:
    """차단 목록 → {card_id: {"reason", "since"}}.

    Raises:
        OSError: 파일을 읽을 수 없을 때 (없는 파일 포함. 기본 파일은 저장소에 있으므로 없으면 설정 오류다)
        BlocklistError: TOML 문법·형식 오류
    """
    try:
        # utf-8-sig: Windows 메모장이 앞에 붙이는 BOM도 받는다
        raw = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise BlocklistError("UTF-8로 저장된 파일이 아님") from None
    except tomllib.TOMLDecodeError as error:
        line = getattr(error, "lineno", None)  # Python 3.14부터 있다
        raise BlocklistError("TOML 문법 오류" + (f" ({line}번째 줄)" if line else "")) from None
    if "blocked" not in raw:
        raise BlocklistError("blocked 목록이 없음 (비어 있으면 blocked = [])")
    if set(raw) != {"blocked"} or not isinstance(raw["blocked"], list):
        raise BlocklistError("최상위에는 blocked 목록만 둔다")
    blocked: dict[str, dict] = {}
    for number, entry in enumerate(raw["blocked"], start=1):
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise BlocklistError(f"{number}번 칸: card_id·reason·since 세 키만 있어야 함")
        card_id, reason, since = entry["card_id"], entry["reason"], entry["since"]
        if not isinstance(card_id, str) or not CARD_ID_PATTERN.fullmatch(card_id):
            raise BlocklistError(f"{number}번 칸: card_id 형식이 아님 (영문 소문자·숫자·-, 64자 이하)")
        # 허용 목록: 보이는 글자와 띄어쓰기만 (줄바꿈·탭·제어 문자·글 방향 뒤집기 등이 기록·화면을 꾸미지 못하게)
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason or len(reason) > MAX_REASON_CHARS or not reason.isprintable():
            raise BlocklistError(f"{number}번 칸: reason은 {MAX_REASON_CHARS}자 이하 한 줄 (보이는 글자만)")
        if type(since) is not date:  # TOML 날짜(2026-11-06)만. 시각이 붙은 값·시각만·문자열은 받지 않는다
            raise BlocklistError(f"{number}번 칸: since는 2026-11-06 같은 날짜")
        if card_id in blocked:
            raise BlocklistError(f"{number}번 칸: 같은 card_id가 이미 있음")
        blocked[card_id] = {"reason": reason, "since": since.isoformat()}
    return blocked
