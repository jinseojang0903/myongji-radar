"""cards.json 내보내기 실행 (#17). 매일 실행(#19)이 추출(#14)·검증(#15)·병합(#16) 뒤에 부른다.

    python -m exporter --bundles data/card_bundles.json --out ../site/data/cards.json

--bundles 파일 형식 (파이프라인 안에서만 쓰는 중간 파일, data/에 있어 저장소에 올라가지 않는다):
    {"generated_at": "2026-11-05T09:00:00+09:00",
     "bundles": [{"card_id", "notices", "extraction", "statuses", "deadline_history", "meta"}, ...]}
각 항목의 뜻은 exporter.cards.CardBundle 참고.

종료 코드 (#19는 0과 1일 때만 배포한다. 1이면 로그를 사람이 확인한다):
    0  모두 내보냄 (대회가 아닌 추출 결과를 건너뛴 것은 문제가 아님)
    1  내보냈지만 뺀 카드나 확인할 일이 있음
    2  cards.json을 바꾸지 않음: 묶음 파일을 읽을 수 없음, generated_at 형식, 내보낼 카드가 0장(--allow-empty 없이),
       수동 차단 목록(--blocked)을 읽을 수 없거나 형식이 틀림 (차단 없이 내보내면 내려야 할 카드가 공개되므로)
    3  cards.json 쓰기 실패 (기존 파일은 그대로)

수동 차단 목록(#65, `config/blocked_cards.toml`)의 카드는 빼고 내보낸다. 뺀 카드는 [차단]으로 찍고(이유는 찍지 않는다),
목록에 있는데 이번 묶음에 없는 id는 [확인]으로 찍는다 (종료 코드 1).
"""

import argparse
import json
import sys
from pathlib import Path

from collector.config import load_sources
from exporter.blocklist import BlocklistError, load_blocked
from exporter.cards import CARD_ID_PATTERN, SCHEMA_VERSION, CardBundle, _check_generated_at, boards_from_sources, build_cards_json, write_cards_json
from extractor.daily import card_id_of
from merger.groups import Group, validate_groups

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DEFAULTS = (("card_id", ""), ("notices", []), ("extraction", {}), ("statuses", {}), ("deadline_history", []), ("meta", {}))


def expand_blocked_cards(blocked: set[str], state: dict) -> set[str]:
    """현재 차단을 병합 ID와 그 구성 공지 ID에 양방향 적용한다."""
    groups = state.get("cards") if isinstance(state, dict) else None
    if not isinstance(groups, dict):
        raise ValueError("차단 대상 확인에 필요한 병합 상태가 없음")
    definitions = []
    for group_id, entry in groups.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("members"), list):
            raise ValueError("차단 대상의 병합 구성 형식 오류")
        definitions.append(Group(group_id, tuple(entry["members"])))
    validate_groups(definitions)
    removed = set(blocked)
    for group in definitions:
        members = {card_id_of(*pair) for pair in group.members}
        if None in members:
            raise ValueError("차단 대상의 공지 카드 ID 오류")
        if group.id in blocked or not members.isdisjoint(blocked):
            removed.add(group.id)
            removed.update(members)
    return removed


def prune_blocked_cards(path: Path, blocked: set[str]) -> bool:
    """신규 공개가 보류된 경우 기존 공개본의 차단 카드만 제거한다. 기준 시각은 바꾸지 않는다."""
    if not blocked or not path.exists():
        return False
    current = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(current, dict) or current.get("schema_version") != SCHEMA_VERSION
            or not isinstance(current.get("boards"), list) or not isinstance(current.get("cards"), list)):
        raise ValueError("기존 공개 목록 형식 오류")
    _check_generated_at(current.get("generated_at"))
    ids = set()
    for card in current["cards"]:
        card_id = card.get("id") if isinstance(card, dict) else None
        if not isinstance(card_id, str) or not CARD_ID_PATTERN.fullmatch(card_id) or card_id in ids:
            raise ValueError("기존 공개 카드 ID 형식 또는 중복 오류")
        ids.add(card_id)
    kept = [card for card in current["cards"] if card["id"] not in blocked]
    if len(kept) == len(current["cards"]):
        return False
    current["cards"] = kept
    write_cards_json(path, current)
    return True


def read_bundles(path: Path, *, raw=None) -> tuple[list[CardBundle], str]:
    """묶음 파일 → (CardBundle 목록, generated_at). 값이 null인 키는 기본값으로.

    Raises:
        OSError, ValueError: 파일을 읽을 수 없거나 최상위 형식이 다를 때
    """
    raw = json.loads(path.read_text(encoding="utf-8")) if raw is None else raw
    if not isinstance(raw, dict) or not isinstance(raw.get("bundles"), list):
        raise ValueError("최상위에 bundles 배열이 없음")
    state_path = path.parent / "card_bundles.merged.json"
    if state_path.exists():
        current = json.loads(state_path.read_text(encoding="utf-8"))
        state = current.get("merge_state") if isinstance(current, dict) else None
        if not isinstance(state, dict) or raw.get("merge_state") != state:
            raise ValueError("기존 병합 상태와 일치하는 검증 묶음만 내보낼 수 있음")
        report = raw.get("merge_report")
        if not isinstance(report, dict) or report != current.get("merge_report"):
            raise ValueError("기존 병합 결과와 일치하는 검증 묶음만 내보낼 수 있음")
    bundles = []
    for number, item in enumerate(raw["bundles"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"bundles {number}번이 객체가 아님")
        bundles.append(CardBundle(**{key: default if item.get(key) is None else item[key] for key, default in BUNDLE_DEFAULTS}))
    return bundles, raw.get("generated_at")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 cards.json 내보내기")
    parser.add_argument("--bundles", type=Path, required=True, help="카드 묶음 JSON (추출·검증·병합 결과)")
    parser.add_argument("--out", type=Path, default=ROOT.parent / "site/data/cards.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--allow-empty", action="store_true", help="카드가 0장이어도 덮어쓴다 (기본은 상류 장애로 보고 멈춤)")
    parser.add_argument("--blocked", type=Path, default=ROOT / "config/blocked_cards.toml", help="수동 차단 목록 (#65)")
    args = parser.parse_args(argv)
    target, source_dir = args.out.resolve(), args.bundles.resolve().parent
    if (target in {args.bundles.resolve(), args.config.resolve(), args.blocked.resolve()}
            or (target.is_relative_to(source_dir) and target != source_dir / "cards.json")):
        print("[오류] 공개 출력이 입력·설정·비공개 상태 경로와 겹쳐 cards.json을 바꾸지 않음", file=sys.stderr)
        return 2
    # 별도 이름의 병합 결과를 cards.json으로 저장한 경우에도 그 상태를 덮어쓰지 않는다.
    if target == source_dir / "cards.json" and target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and any(key in existing for key in ("merge_state", "bundles", "notices", "input_sha256")):
            print("[오류] 공개 출력이 비공개 입력 또는 상태와 겹쳐 cards.json을 바꾸지 않음", file=sys.stderr)
            return 2
    try:
        blocked = load_blocked(args.blocked)
    except (OSError, BlocklistError) as error:  # 차단 없이 내보내지 않는다
        print(f"[오류] 수동 차단 목록을 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    try:
        bundle_data = json.loads(args.bundles.read_text(encoding="utf-8"))
        bundles, generated_at = read_bundles(args.bundles, raw=bundle_data)
        blocked_ids = (expand_blocked_cards(set(blocked), bundle_data["merge_state"])
                       if "merge_state" in bundle_data else set(blocked))
        boards = boards_from_sources(load_sources(args.config))
        data, report = build_cards_json(bundles, boards, generated_at, blocked=frozenset(blocked_ids))
    except Exception as error:  # 예상 못 한 오류도 1(배포 가능)로 끝나지 않게 2로
        print(f"[오류] {args.bundles}를 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    for item in report["blocked"]:
        print(f"[차단] {item['id']}", file=sys.stderr)
    for label, key in (("[뺌]", "rejected"), ("[건너뜀]", "skipped"), ("[확인]", "warnings")):
        for item in report[key]:
            print(f"{label} {item['id']}: {item['reason']}", file=sys.stderr)
    if not data["cards"]:
        try:
            if "merge_state" in bundle_data or "merge_report" in bundle_data:
                withheld = bundle_data["merge_report"]["withheld"]
                if type(withheld) is not int or withheld < 0:
                    raise ValueError("병합 보류 건수 오류")
                if withheld:
                    if prune_blocked_cards(args.out, blocked_ids):
                        print("[확인] 그룹 보류로 전체 갱신 대신 기존 공개본에서 차단 카드만 제거함", file=sys.stderr)
                        return 1
                    print("[오류] 확인 대기 중인 그룹이 있어 빈 목록을 공개하지 않음", file=sys.stderr)
                    return 2
        except (OSError, ValueError, TypeError, KeyError):
            print("[오류] 병합 결과를 읽지 못해 빈 목록을 공개하지 않음", file=sys.stderr)
            return 2
        if not args.allow_empty:
            try:
                if prune_blocked_cards(args.out, blocked_ids):
                    print("[확인] 빈 목록 공개 대신 기존 공개본에서 차단 카드만 제거함", file=sys.stderr)
                    return 1
            except (OSError, ValueError, TypeError, KeyError):
                print("[오류] 차단 카드 정리에 실패해 기존 공개본을 바꾸지 않음", file=sys.stderr)
                return 2
            print("[오류] 내보낼 카드가 0장이라 cards.json을 바꾸지 않음 (의도한 것이면 --allow-empty)", file=sys.stderr)
            return 2
    try:
        write_cards_json(args.out, data)
    except Exception as error:
        print(f"[오류] cards.json 쓰기 실패, 기존 파일 그대로: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    print(f"[내보냄] 카드 {len(data['cards'])}장 → {args.out} (뺌 {len(report['rejected'])}, 건너뜀 {len(report['skipped'])}, "
          f"확인 {len(report['warnings'])}, 차단 {len(report['blocked'])})",
          file=sys.stderr)
    return 1 if report["rejected"] or report["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
