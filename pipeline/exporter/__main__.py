"""cards.json 내보내기 실행 (#17). 매일 실행(#19)이 추출(#14)·검증(#15)·병합(#16) 뒤에 부른다.

    python -m exporter --bundles data/card_bundles.verified.json --out ../site/data/cards.json

--bundles 파일 형식 (파이프라인 안에서만 쓰는 중간 파일, data/에 있어 저장소에 올라가지 않는다):
    {"generated_at": "2026-11-05T09:00:00+09:00",
     "bundles": [{"card_id", "notices", "extraction", "statuses", "deadline_history", "meta", "verified"}, ...]}
각 항목의 뜻은 exporter.cards.CardBundle 참고. `verified`는 검증기(`python -m verifier`)가 붙이는 표시다
(verifier/stamp.py). 표시가 없거나 지금 내용과 맞지 않는 묶음은 [뺌]으로 찍고 공개하지 않는다 (#19·#49).

종료 코드 (#19는 0과 1일 때만 배포한다. 1이면 로그를 사람이 확인한다):
    0  모두 내보냄 (대회가 아닌 추출 결과를 건너뛴 것은 문제가 아님)
    1  내보냈지만 뺀 카드(검증 표시가 맞지 않는 묶음 포함)나 확인할 일이 있음
    2  cards.json을 바꾸지 않음: 묶음 파일을 읽을 수 없음, generated_at 형식, 내보낼 카드가 0장(--allow-empty 없이),
       검증 표시가 맞는 묶음이 하나도 없음(--allow-empty여도),
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
from exporter.cards import CARD_ID_PATTERN, CardBundle, boards_from_sources, build_cards_json, write_cards_json
from verifier.stamp import has_valid_stamp

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DEFAULTS = (("card_id", ""), ("notices", []), ("extraction", {}), ("statuses", {}), ("deadline_history", []), ("meta", {}))


def read_verified_bundles(path: Path) -> tuple[list[CardBundle], str, list[str]]:
    """검증 표시(verifier/stamp.py)가 지금 내용과 맞는 묶음만 → (CardBundle 목록, generated_at, 뺀 묶음의 id).
    값이 null인 키는 기본값으로. 표시를 보지 않고 읽는 함수는 두지 않는다 (공개 경로에서 실수로 쓰지 않게).

    표시가 없거나 맞지 않으면 검증기를 거치지 않았거나 검증 뒤에 바뀐 것이라 공개하지 않는다 (#19·#49).
    뺀 id는 로그용이라 형식이 틀리면 순번으로 바꾼다.

    Raises:
        OSError, ValueError: 파일을 읽을 수 없거나 최상위 형식이 다를 때
    """
    items, generated_at = _read_items(path)
    bundles, unverified = [], []
    for number, item in enumerate(items, start=1):
        if has_valid_stamp(item, generated_at):
            bundles.append(_to_bundle(item))
        else:
            card_id = item.get("card_id")
            unverified.append(card_id if isinstance(card_id, str) and CARD_ID_PATTERN.fullmatch(card_id)
                              else f"(형식이 틀린 id, {number}번째 묶음)")
    return bundles, generated_at, unverified


def _read_items(path: Path) -> tuple[list[dict], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("bundles"), list):
        raise ValueError("최상위에 bundles 배열이 없음")
    for number, item in enumerate(raw["bundles"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"bundles {number}번이 객체가 아님")
    return raw["bundles"], raw.get("generated_at")


def _to_bundle(item: dict) -> CardBundle:
    return CardBundle(**{key: default if item.get(key) is None else item[key] for key, default in BUNDLE_DEFAULTS})


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 cards.json 내보내기")
    parser.add_argument("--bundles", type=Path, required=True, help="검증기를 거친 카드 묶음 JSON (python -m verifier의 --out)")
    parser.add_argument("--out", type=Path, default=ROOT.parent / "site/data/cards.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--allow-empty", action="store_true", help="카드가 0장이어도 덮어쓴다 (기본은 상류 장애로 보고 멈춤)")
    parser.add_argument("--blocked", type=Path, default=ROOT / "config/blocked_cards.toml", help="수동 차단 목록 (#65)")
    args = parser.parse_args(argv)
    try:
        blocked = load_blocked(args.blocked)
    except (OSError, BlocklistError) as error:  # 차단 없이 내보내지 않는다
        print(f"[오류] 수동 차단 목록을 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    try:
        bundles, generated_at, unverified = read_verified_bundles(args.bundles)
        if unverified and not bundles:
            # 파일 전체가 검증 전이면 --allow-empty여도 비운 cards.json을 쓰지 않는다 (검증기를 빠뜨린 실행)
            print("[오류] 검증기를 거친 묶음이 하나도 없어 cards.json을 바꾸지 않음 (python -m verifier의 --out 파일을 넣기)",
                  file=sys.stderr)
            return 2
        boards = boards_from_sources(load_sources(args.config))
        data, report = build_cards_json(bundles, boards, generated_at, blocked=frozenset(blocked))
    except Exception as error:  # 예상 못 한 오류도 1(배포 가능)로 끝나지 않게 2로
        print(f"[오류] {args.bundles}를 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    for card_id in unverified:
        print(f"[뺌] {card_id}: 검증기를 거치지 않았거나 검증 뒤에 바뀐 묶음 (다시 검증)", file=sys.stderr)
    for item in report["blocked"]:
        print(f"[차단] {item['id']}", file=sys.stderr)
    for label, key in (("[뺌]", "rejected"), ("[건너뜀]", "skipped"), ("[확인]", "warnings")):
        for item in report[key]:
            print(f"{label} {item['id']}: {item['reason']}", file=sys.stderr)
    if not data["cards"] and not args.allow_empty:
        print("[오류] 내보낼 카드가 0장이라 cards.json을 바꾸지 않음 (의도한 것이면 --allow-empty)", file=sys.stderr)
        return 2
    try:
        write_cards_json(args.out, data)
    except Exception as error:
        print(f"[오류] cards.json 쓰기 실패, 기존 파일 그대로: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    print(f"[내보냄] 카드 {len(data['cards'])}장 → {args.out} (뺌 {len(report['rejected']) + len(unverified)}, "
          f"건너뜀 {len(report['skipped'])}, 확인 {len(report['warnings'])}, 차단 {len(report['blocked'])})",
          file=sys.stderr)
    return 1 if report["rejected"] or report["warnings"] or unverified else 0


if __name__ == "__main__":
    sys.exit(main())
