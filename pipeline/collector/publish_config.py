"""매일 공개 설정. 계정·경로·추가 명령은 설정에서 받지 않는다."""
from dataclasses import dataclass
from pathlib import Path
import tomllib

MODELS = {"haiku", "sonnet", "opus"}
REQUIRED_KEYS = {"version", "enabled", "model", "push", "max_new"}
OPTIONAL_KEYS = {"cross_model"}  # 교차 검증 모델 (#15). 없으면 교차 검증 안 함


@dataclass(frozen=True)
class Publishing:
    enabled: bool
    model: str
    push: bool
    max_new: int
    cross_model: str | None = None


def load_publishing(path: Path) -> Publishing:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    cross_model = data.get("cross_model")
    if (not REQUIRED_KEYS <= set(data) or not set(data) <= REQUIRED_KEYS | OPTIONAL_KEYS
            or type(data["version"]) is not int or data["version"] != 1
            or type(data["enabled"]) is not bool or type(data["push"]) is not bool
            or not isinstance(data["model"], str) or data["model"] not in MODELS
            or type(data["max_new"]) is not int or data["max_new"] < 1
            # 교차 검증 모델은 허용 모델이고 주 모델과 달라야 한다 (같은 모델끼리는 교차 검증이 아니다)
            or ("cross_model" in data and (not isinstance(cross_model, str) or cross_model not in MODELS
                                           or cross_model == data["model"]))):
        raise ValueError("invalid publishing configuration")
    return Publishing(data["enabled"], data["model"], data["push"], data["max_new"], cross_model)
