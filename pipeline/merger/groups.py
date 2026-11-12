"""사람이 검토한 같은 회차의 공지 식별자만 묶는다."""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}", re.ASCII)
SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}", re.ASCII)
EXTERNAL_ID = re.compile(r"[A-Za-z0-9_-]+/[0-9]+/[0-9]+", re.ASCII)


@dataclass(frozen=True)
class Group:
    id: str
    members: tuple[tuple[str, str], ...]


def validate_groups(groups):
    ids, members = set(), set()
    for group in groups:
        if not isinstance(group, Group) or not isinstance(group.id, str) or not CARD_ID.fullmatch(group.id):
            raise ValueError("병합 카드 ID 형식 오류")
        if group.id in ids or not group.members or len(group.members) > 100:
            raise ValueError("병합 ID 중복 또는 공지 수 오류")
        ids.add(group.id)
        for pair in group.members:
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or not isinstance(pair[0], str) or not SOURCE_ID.fullmatch(pair[0])
                    or not isinstance(pair[1], str) or len(pair[1]) > 128 or not EXTERNAL_ID.fullmatch(pair[1])):
                raise ValueError("병합 공지 식별자 형식 오류")
            key = tuple(pair)
            if key in members:
                raise ValueError("병합 공지가 중복 등록됨")
            members.add(key)


def load_groups(path: Path) -> list[Group]:
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    if (set(data) != {"version", "groups"} or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["groups"], list) or len(data["groups"]) > 1000):
        raise ValueError("병합 설정 버전 또는 키 오류")
    result = []
    for entry in data["groups"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "members"} or not isinstance(entry["members"], list):
            raise ValueError("병합 그룹 설정 키 오류")
        pairs = []
        for member in entry["members"]:
            if not isinstance(member, dict) or set(member) != {"source_id", "external_id"}:
                raise ValueError("병합 구성 공지 설정 키 오류")
            pairs.append((member["source_id"], member["external_id"]))
        result.append(Group(entry["id"], tuple(pairs)))
    validate_groups(result)
    return result
