"""cards.json 내보내기 실행 (#17). 매일 실행(#19)이 추출(#14)·검증(#15)·병합(#16) 뒤에 부른다.

    python -m exporter --bundles data/card_bundles.verified.json --out ../site/data/cards.json

--bundles 파일 형식 (파이프라인 안에서만 쓰는 중간 파일, data/에 있어 저장소에 올라가지 않는다):
    {"generated_at": "2026-11-05T09:00:00+09:00",
     "bundles": [{"card_id", "notices", "extraction", "statuses", "deadline_history", "meta", "verified"}, ...]}
각 항목의 뜻은 exporter.cards.CardBundle 참고. `verified`는 검증기(`python -m verifier`)가 붙이는 표시다
(verifier/stamp.py). 표시가 없거나 지금 내용과 맞지 않는 묶음은 [뺌]으로 찍고 공개하지 않는다 (#19·#49).

종료 코드 (#19는 0과 1일 때만 배포한다. 1이면 로그를 사람이 확인한다):
    0  모두 내보냄 (대회가 아닌 추출 결과·게시 1년이 지난 카드(#153)를 건너뛴 것은 문제가 아님. 다만 그렇게 빼서
       0장이 되면 아래 2와 같이 멈춘다: 상류 장애와 구별하지 않고 어제 cards.json을 둔다. 사람이 검수한 카드가
       1년 지나 빠지면 검수 기록이 남아 [확인]이 나오므로(종료 코드 1) human_reviews.toml에서 그 칸을 지운다)
    1  내보냈지만 뺀 카드(검증 표시가 맞지 않는 묶음 포함)나 확인할 일이 있음
    2  cards.json을 바꾸지 않음: 묶음 파일을 읽을 수 없음, generated_at 형식, 내보낼 카드가 0장(--allow-empty 없이),
       검증 표시가 맞는 묶음이 하나도 없음(--allow-empty여도),
       수동 차단 목록(--blocked)을 읽을 수 없거나 형식이 틀림 (차단 없이 내보내면 내려야 할 카드가 공개되므로),
       같은 대회 묶음(--groups)을 읽을 수 없는데 차단 목록이 비어 있지 않음 (묶음 단위 차단을 할 수 없으므로)
    3  cards.json 쓰기 실패 (기존 파일은 그대로)

수동 차단 목록(#65, `config/blocked_cards.toml`)의 카드는 빼고 내보낸다. 뺀 카드는 [차단]으로 찍고(이유는 찍지 않는다),
목록에 있는데 이번 묶음에 없는 id는 [확인]으로 찍는다 (종료 코드 1).

`--report <경로>`면 위 [뺌]·[건너뜀]·[확인]·[차단]을 카드 id·사유로 JSON에 남긴다 (매일 무인 공개는 stderr를 보는
사람이 없다). 보고를 쓰지 못하면 cards.json을 바꾸지 않고 2. site/ 안에는 쓰지 않는다.

같은 대회 묶음(#16, `config/contest_groups.toml`, 사람이 확인한 묶음만)은 카드를 만든 뒤 한 장으로 합치고 [묶음]으로
찍는다 (`exporter/contest_groups.py`). 묶음 설정을 읽지 못하거나 합치다 실패하면 합치지 않고 공개하며 [확인]으로
남긴다 (합치지 않은 카드도 각자 검증을 거친 카드라 공개해도 틀린 값이 늘지 않는다). 다만 설정을 읽지 못했는데 차단
목록이 비어 있지 않으면 2로 멈춘다.
"""

import argparse
import json
import sys
from pathlib import Path

from collector.config import load_sources
from exporter.blocklist import BlocklistError, load_blocked
from exporter.cards import (CARD_ID_PATTERN, CardBundle, boards_from_sources, build_cards_json, drop_old_cards,
                            write_cards_json)
from exporter.contest_groups import group_aliases, merge_groups
from exporter.reviews import ReviewsError, apply_all, load_reviews
from merger.groups import load_groups
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


def write_report(path: Path, generated_at, data: dict, report: dict, unverified: list[str]) -> None:
    """내보내기 보고를 파일로 남긴다. 매일 무인 공개는 stderr를 보는 사람이 없어, 어떤 카드가 왜 빠지거나
    확인할 일이 생겼는지(차단 목록 오타 포함) 여기 말고는 남지 않는다 (#109 보안 리뷰).
    사유에는 AI 값이 짧게(80자) 들어갈 수 있어 공개 폴더 site/에는 쓰지 않는다.

    Raises:
        ValueError: path가 site/ 안일 때
        OSError: 쓰기 실패
    """
    if path.resolve().is_relative_to((ROOT.parent / "site").resolve()):
        raise ValueError("보고 파일을 공개 폴더 site/ 안에 쓰지 않는다")
    # cards_to_write: 이 보고는 cards.json을 쓰기 전에 남긴다 (보고를 못 쓰면 cards.json도 바꾸지 않으려고).
    # 실제로 썼는지는 내보내기 종료 코드(3이면 못 씀)로 본다
    body = {"generated_at": generated_at, "cards_to_write": len(data["cards"]),
            "counts": {"rejected": len(report["rejected"]) + len(unverified), "skipped": len(report["skipped"]),
                       "warnings": len(report["warnings"]), "blocked": len(report["blocked"]),
                       "merged": len(report.get("merged", []))},
            "unverified": unverified, "blocked": [item["id"] for item in report["blocked"]],
            **{key: [{"id": item["id"], "reason": item["reason"]} for item in report.get(key, [])]
               for key in ("rejected", "skipped", "warnings", "merged")}}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _to_bundle(item: dict) -> CardBundle:
    return CardBundle(**{key: default if item.get(key) is None else item[key] for key, default in BUNDLE_DEFAULTS})


def build_and_merge(bundles, boards, generated_at, blocked, groups) -> tuple[dict, dict]:
    """카드를 만들고(차단은 묶음 단위로) 같은 대회 묶음을 합친 뒤, 게시 1년이 지난 카드를 뺀다(보고 skipped).
    보고에 "merged"를 더한다.

    Raises:
        build_cards_json·merge_groups의 예외 (호출하는 쪽이 cards.json을 바꾸지 않는다)
    """
    data, report = build_cards_json(bundles, boards, generated_at, blocked=frozenset(blocked),
                                    aliases=group_aliases(groups))
    try:
        data["cards"], report["merged"] = merge_groups(data["cards"], groups)
    except Exception as error:  # 합치기가 깨져도 각자 검증된 카드로 공개한다 (검수 적용 실패와 같은 방식)
        report["merged"] = []
        report["warnings"].append({"id": "(묶음 설정)", "reason": f"같은 대회 묶음을 합치지 못해 합치지 않고 공개: {type(error).__name__}"})
    # 합친 뒤에 거른다: 옛 공지와 새 공지가 묶인 대회는 새 공지 기준으로 남는다 (#153)
    data["cards"], old = drop_old_cards(data["cards"], generated_at)
    report["skipped"] += old
    return data, report


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
    parser.add_argument("--reviews", type=Path, default=ROOT / "config/human_reviews.toml", help="사람 검수 기록 (#99)")
    parser.add_argument("--groups", type=Path, default=ROOT / "config/contest_groups.toml",
                        help="같은 대회 묶음 (#16, 사람이 확인한 묶음만)")
    parser.add_argument("--report", type=Path, default=None,
                        help="뺌·건너뜀·확인·차단을 카드 id·사유로 남길 JSON (site/ 밖. 매일 공개는 data/publish_logs/에)")
    args = parser.parse_args(argv)
    try:
        blocked = load_blocked(args.blocked)
    except (OSError, BlocklistError) as error:  # 차단 없이 내보내지 않는다
        print(f"[오류] 수동 차단 목록을 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    # 검수 기록을 못 읽으면 검수 없이(모두 "확인 전") 공개한다. 더 조심스러운 쪽이라 멈추지 않고 보고에 남긴다
    reviews, review_error, review_warnings = {}, None, []
    try:
        reviews, review_warnings = load_reviews(args.reviews)
    except ReviewsError as error:
        review_error = str(error)
    except Exception as error:  # 파일을 못 읽음·깊은 중첩(RecursionError) 등도 검수 없이 공개하는 쪽으로
        review_error = type(error).__name__
    # 묶음 설정을 못 읽으면 합치지 않고 공개한다. 각 카드는 검증을 거친 카드라 값이 틀려지지 않는다.
    # 단 차단 목록이 비어 있지 않으면 멈춘다: 묶음 id로 건 차단을 풀 수 없어 내려야 할 카드가 공개될 수 있다
    groups, group_error = [], None
    try:
        groups = load_groups(args.groups)
        group_aliases(groups)  # 카드 id를 만들 수 없는 구성 공지도 여기서 걸러 내보내기 전체가 멈추지 않게
    except Exception as error:
        groups, group_error = [], type(error).__name__
        if blocked:
            print(f"[오류] 같은 대회 묶음을 읽지 못해 묶음 단위 차단을 할 수 없어 cards.json을 바꾸지 않음: {group_error}",
                  file=sys.stderr)
            return 2
    try:
        bundles, generated_at, unverified = read_verified_bundles(args.bundles)
        if unverified and not bundles:
            # 파일 전체가 검증 전이면 --allow-empty여도 비운 cards.json을 쓰지 않는다 (검증기를 빠뜨린 실행)
            print("[오류] 검증기를 거친 묶음이 하나도 없어 cards.json을 바꾸지 않음 (python -m verifier의 --out 파일을 넣기)",
                  file=sys.stderr)
            return 2
        boards = boards_from_sources(load_sources(args.config))
        data, report = build_and_merge(bundles, boards, generated_at, blocked, groups)
    except Exception as error:  # 예상 못 한 오류도 1(배포 가능)로 끝나지 않게 2로
        print(f"[오류] {args.bundles}를 읽지 못해 cards.json을 바꾸지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    promoted = 0
    try:
        promoted = apply_all(data, reviews, report)
    except Exception as error:  # 검수 적용이 깨져도 검수 없는 카드로 공개한다. 일부만 올라간 카드가 남지 않게 다시 만든다
        try:
            data, report = build_and_merge(bundles, boards, generated_at, blocked, groups)
        except Exception as again:  # 1(배포 가능)로 끝나지 않게
            print(f"[오류] 카드를 다시 만들지 못해 cards.json을 바꾸지 않음: {type(again).__name__}", file=sys.stderr)
            return 2
        review_error = review_error or f"적용 중 오류 ({type(error).__name__})"
    # 검수 경고는 적용 뒤에 붙인다 (적용이 실패해 보고를 다시 만들어도 남게)
    report["warnings"] += [{"id": "(검수 기록)", "reason": message} for message in review_warnings]
    if review_error:
        report["warnings"].append({"id": "(검수 기록)", "reason": f"검수 기록을 쓰지 못해 검수 없이 공개: {review_error}"})
    if group_error:
        report["warnings"].append({"id": "(묶음 설정)", "reason": f"같은 대회 묶음을 읽지 못해 합치지 않고 공개: {group_error}"})

    if args.report is not None:
        try:
            write_report(args.report, generated_at, data, report, unverified)
        except (OSError, ValueError) as error:  # 보고를 못 남기면 무인 공개에서 경고가 사라지므로 내보내지 않는다
            print(f"[오류] 내보내기 보고를 쓰지 못해 cards.json을 바꾸지 않음: {type(error).__name__}", file=sys.stderr)
            return 2
    for card_id in unverified:
        print(f"[뺌] {card_id}: 검증기를 거치지 않았거나 검증 뒤에 바뀐 묶음 (다시 검증)", file=sys.stderr)
    for item in report["blocked"]:
        print(f"[차단] {item['id']}", file=sys.stderr)
    for item in report["merged"]:
        print(f"[묶음] {item['id']}: {item['reason']}", file=sys.stderr)
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
          f"건너뜀 {len(report['skipped'])}, 확인 {len(report['warnings'])}, 차단 {len(report['blocked'])}, 검수 완료 {promoted}항목)",
          file=sys.stderr)
    return 1 if report["rejected"] or report["warnings"] or unverified else 0


if __name__ == "__main__":
    sys.exit(main())
