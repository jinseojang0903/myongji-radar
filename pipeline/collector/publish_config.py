"""매일 공개 설정. 계정·경로·추가 명령은 설정에서 받지 않는다."""
from dataclasses import dataclass
from pathlib import Path
import tomllib

MODELS = {"haiku", "sonnet", "opus"}
REQUIRED_KEYS = {"version", "enabled", "model", "push", "max_new"}
# cross_model: 교차 검증 모델 (#15). 없으면 교차 검증 안 함. images: 포스터 이미지도 AI에 넣기 (#14). 없으면 끔
OPTIONAL_KEYS = {"cross_model", "images"}


@dataclass(frozen=True)
class Publishing:
    enabled: bool
    model: str
    push: bool
    max_new: int
    cross_model: str | None = None
    images: bool = False


def load_publishing(path: Path, *, use_local: bool = True) -> Publishing:
    path = Path(path)
    local = path.with_suffix('.local.toml')
    data = tomllib.loads((local if use_local and local.is_file() else path).read_text(encoding="utf-8-sig"))
    cross_model = data.get("cross_model")
    if (not REQUIRED_KEYS <= set(data) or not set(data) <= REQUIRED_KEYS | OPTIONAL_KEYS
            or type(data["version"]) is not int or data["version"] != 1
            or type(data["enabled"]) is not bool or type(data["push"]) is not bool
            or not isinstance(data["model"], str) or data["model"] not in MODELS
            or type(data["max_new"]) is not int or data["max_new"] < 1
            # 교차 검증 모델은 허용 모델이고 주 모델과 달라야 한다 (같은 모델끼리는 교차 검증이 아니다)
            or ("cross_model" in data and (not isinstance(cross_model, str) or cross_model not in MODELS
                                           or cross_model == data["model"]))
            or ("images" in data and type(data["images"]) is not bool)):
        raise ValueError("invalid publishing configuration")
    return Publishing(data["enabled"], data["model"], data["push"], data["max_new"], cross_model, data.get("images", False))


def unset_local_options(path: Path) -> list[str]:
    """추적 설정에는 켜져 있는데 로컬 설정에는 칸이 없는 선택 칸. 로컬 설정이 통째로 우선하므로
    이런 칸은 main에서 켜도 운영에서는 기본값(끔)으로 돈다 (#168 이관본에 #158의 images가 없던 경우).
    로컬에 칸을 적어 두면(false 포함) 운영자의 선택으로 보고 알리지 않는다.
    알림일 뿐이라 실행을 막지 않는다: 로컬이 있으면 load_publishing은 추적 파일을 읽지 않으므로, 추적 파일을 읽지
    못하면 알리지 않고 넘어간다."""
    path = Path(path)
    local = path.with_suffix('.local.toml')
    if not local.is_file():
        return []
    try:
        tracked = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        mine = tomllib.loads(local.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return []
    # 추적 값이 켜는 값일 때만 (images = false처럼 기본값과 같으면 로컬에 없어도 차이가 없다)
    return sorted(key for key in OPTIONAL_KEYS if tracked.get(key) not in (None, False) and key not in mine)
