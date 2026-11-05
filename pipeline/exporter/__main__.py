"""cards.json 내보내기 실행 (#17). 매일 실행(#19)이 추출(#14)·검증(#15)·병합(#16) 뒤에 부른다.

    python -m exporter --bundles data/card_bundles.json --out ../site/data/cards.json

--bundles 파일 형식 (파이프라인 안에서만 쓰는 중간 파일, data/에 있어 저장소에 올라가지 않는다):
    {"generated_at": "2026-11-05T09:00:00+09:00",
     "bundles": [{"card_id", "notices", "extraction", "statuses", "deadline_history", "meta"}, ...]}
각 항목의 뜻은 exporter.cards.CardBundle 참고.

종료 코드 (#19는 0과 1일 때만 배포한다. 1이면 로그를 사람이 확인한다):
    0  모두 내보냄 (대회가 아닌 추출 결과를 건너뛴 것은 문제가 아님)
    1  내보냈지만 뺀 카드나 확인할 일이 있음
    2  cards.json을 바꾸지 않음: 묶음 파일을 읽을 수 없음, generated_at 형식, 내보낼 카드가 0장(--allow-empty 없이)
    3  cards.json 쓰기 실패 (기존 파일은 그대로)
"""

import argparse
import json
import sys
from pathlib import Path

from collector.config import load_sources
from exporter.cards import CardBundle, boards_from_sources, build_cards_json, write_cards_json

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DEFAULTS = (("card_id", ""), ("notices", []), ("extraction", {}), ("statuses", {}), ("deadline_history", []), ("meta", {}))


def read_bundles(path: Path) -> tuple[list[CardBundle], str]:
    """묶음 파일 → (CardBundle 목록, generated_at). 값이 null인 키는 기본값으로.

    Raises:
        OSError, ValueError: 파일을 읽을 수 없거나 최상위 형식이 다를 때
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("bundles"), list):
        raise ValueError("최상위에 bundles 배열이 없음")
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
    args = parser.parse_args(argv)
    try:
        bundles, generated_at = read_bundles(args.bundles)
        boards = boards_from_sources(load_sources(args.config))
        data, report = build_cards_json(bundles, boards, generated_at)
    except Exception as error:  # 예상 못 한 오류도 1(배포 가능)로 끝나지 않게 2로
        print(f"[오류] {args.bundles}를 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

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
    print(f"[내보냄] 카드 {len(data['cards'])}장 → {args.out} (뺌 {len(report['rejected'])}, 건너뜀 {len(report['skipped'])}, 확인 {len(report['warnings'])})",
          file=sys.stderr)
    return 1 if report["rejected"] or report["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
