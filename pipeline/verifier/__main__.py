"""검증 실행 (#15). 매일 실행(#19)이 추출(#14)·병합(#16) 뒤, 내보내기(#17) 전에 부른다.

    python -m verifier --bundles data/card_bundles.json --notices data/notices.json --out data/card_bundles.verified.json

--bundles: 내보내기와 같은 묶음 파일 ({"generated_at", "bundles": [...]}, exporter/__main__.py 참고).
--notices: 수집기가 내보낸 원문 (python -m collector export → data/notices.json). 묶음 공지의 url로 원문을 찾는다.
출력: 묶음 파일과 같은 형식에 statuses만 고친 것. 다른 키는 그대로 옮긴다.
보고: 검증 실패·사람 확인 근거를 항목 이름과 이유만 출력한다 (근거 문장·연락처는 출력하지 않는다).

종료 코드:
    0  검증함 (실패 항목이 있어도 needs_check로 내렸으면 정상)
    2  입력을 읽지 못해 출력하지 않음
    3  출력 쓰기 실패 (기존 파일은 그대로)
"""
import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from exporter.cards import CARD_ID_PATTERN
from verifier.checks import check_extraction, fail_all, merge_statuses

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent / "site"  # 공개 폴더. 묶음에는 정리 전 원문 인용·연락처가 있어 여기에 쓰지 않는다


def read_originals(path: Path) -> dict[str, dict]:
    """수집 원문 → {url: 원문}. 같은 url이 여러 번이면 쓰지 않는다 (어느 판으로 추출했는지 모름 → 대조 실패로 둔다).

    Raises:
        OSError, ValueError: 읽을 수 없거나 최상위 형식이 다를 때
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("notices"), list):
        raise ValueError("원문 파일 최상위에 notices 배열이 없음")
    counts = Counter(notice.get("url") for notice in raw["notices"] if isinstance(notice, dict))
    return {notice["url"]: notice for notice in raw["notices"]
            if isinstance(notice, dict) and isinstance(notice.get("url"), str) and counts[notice["url"]] == 1}


def verify_bundles(data: dict, originals: dict[str, dict]) -> tuple[dict, list[dict]]:
    """묶음 파일 → (statuses를 고친 묶음 파일, 보고 [{card_id, field, result, reasons}]).

    Raises:
        ValueError: 최상위 형식이 다를 때
    """
    if not isinstance(data, dict) or not isinstance(data.get("bundles"), list):
        raise ValueError("최상위에 bundles 배열이 없음")
    report, bundles = [], []
    for number, bundle in enumerate(data["bundles"], start=1):
        if not isinstance(bundle, dict):
            raise ValueError(f"bundles {number}번이 객체가 아님")
        card_id = _printable_id(bundle.get("card_id"), number)
        extraction = bundle.get("extraction") if isinstance(bundle.get("extraction"), dict) else {}
        try:
            # 근거의 공지 번호가 이 순서를 가리키므로 형식이 틀린 공지도 빼지 않고 빈 공지로 둔다
            notices = [notice if isinstance(notice, dict) else {} for notice in bundle.get("notices") or []]
            checks = check_extraction(extraction, notices, [originals.get(notice.get("url")) for notice in notices])
            statuses = merge_statuses(bundle.get("statuses"), extraction, checks)
        except Exception as error:  # 카드 하나의 이상한 값이 전체 검증을 멈추지 않게. 그 카드는 검증 없이 통과하지 않는다
            reason = f"검증 중 오류 ({type(error).__name__})"
            bundles.append({**bundle, "statuses": fail_all(extraction, bundle.get("statuses"), reason)})
            report.append({"card_id": card_id, "field": "*", "result": "fail", "reasons": [reason]})
            continue
        bundles.append({**bundle, "statuses": statuses})
        for name, check in checks.items():
            if check.result != "pass":
                report.append({"card_id": card_id, "field": name, "result": check.result,
                               "reasons": list(dict.fromkeys(check.failures or check.unverifiable))})
    return {**data, "bundles": bundles}, report


def _printable_id(value, number: int) -> str:
    """로그에 찍는 카드 id. 형식이 맞을 때만 그대로 (줄바꿈·제어 문자로 로그를 꾸미거나 다른 내용을 찍지 못하게)."""
    return value if isinstance(value, str) and CARD_ID_PATTERN.fullmatch(value) else f"(형식이 틀린 id, {number}번째 묶음)"


def is_inside_site(path: Path) -> bool:
    return path.resolve().is_relative_to(SITE.resolve())


def write_json(path: Path, data: dict) -> None:
    """임시 파일에 다 쓴 뒤 바꿔 끼운다 (내보내기가 반쯤 쓴 파일을 읽지 않게).

    Raises:
        OSError: 쓰기 실패 (기존 파일은 그대로, 임시 파일은 지운다)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 추출 결과 검증 (근거 대조·상식 검사)")
    parser.add_argument("--bundles", type=Path, required=True, help="카드 묶음 JSON (추출·병합 결과)")
    parser.add_argument("--notices", type=Path, required=True, help="수집 원문 JSON (collector export)")
    parser.add_argument("--out", type=Path, required=True, help="검증 결과를 넣은 묶음 JSON")
    args = parser.parse_args(argv)
    try:
        if is_inside_site(args.out):
            print("[오류] --out이 공개 폴더 site/ 안이라 쓰지 않음 (묶음에는 정리 전 원문 인용이 있다)", file=sys.stderr)
            return 2
        originals = read_originals(args.notices)
        data, report = verify_bundles(json.loads(args.bundles.read_text(encoding="utf-8")), originals)
    except (OSError, ValueError) as error:  # 파일 없음·JSON 깨짐·최상위 형식 (메시지에 입력 내용이 들어가지 않는 오류)
        print(f"[오류] 입력을 읽지 못해 출력하지 않음: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # 예상 못 한 오류도 "검증 없이 통과"가 되지 않게 출력하지 않고 2로. 메시지는 찍지 않는다
        print(f"[오류] 검증기 오류로 출력하지 않음: {type(error).__name__}", file=sys.stderr)
        return 2
    for item in report:  # 쓰기에 실패해도 무엇이 걸렸는지 남게 먼저 찍는다
        label = "[확인 필요]" if item["result"] == "fail" else "[사람 확인]"
        print(f"{label} {item['card_id']} {item['field']}: {'; '.join(item['reasons'])}", file=sys.stderr)
    try:
        write_json(args.out, data)
    except Exception as error:
        print(f"[오류] 출력 쓰기 실패, 기존 파일 그대로: {type(error).__name__}", file=sys.stderr)
        return 3
    failed = sum(item["result"] == "fail" for item in report)
    print(f"[검증] 카드 {len(data['bundles'])}장, 확인 필요로 내린 항목 {failed}개 → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
