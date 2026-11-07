"""터미널 명령·방향 제어 문자를 제거하되 본문 줄바꿈과 탭은 유지한다."""
import re
import unicodedata

ESCAPES = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|(?:\x1b\]|\x9d)[^\x07\x1b\x9c\x9d]*(?:\x07|\x1b\\|\x9c)"
    r"|\x1b[@-Z\\-_]")


def safe_terminal(text: str) -> str:
    return "".join(char for char in ESCAPES.sub("", text)
                   if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf", "Cs"))
