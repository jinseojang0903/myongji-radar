"""매일 공개 설정. 계정·경로·추가 명령은 설정에서 받지 않는다."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Publishing:
    enabled: bool
    model: str
    push: bool


def load_publishing(path: Path) -> Publishing:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    if (set(data) != {"version", "enabled", "model", "push"}
            or type(data["version"]) is not int or data["version"] != 1
            or type(data["enabled"]) is not bool or type(data["push"]) is not bool
            or not isinstance(data["model"], str) or data["model"] not in {"haiku", "sonnet", "opus"}):
        raise ValueError("invalid publishing configuration")
    return Publishing(data["enabled"], data["model"], data["push"])
