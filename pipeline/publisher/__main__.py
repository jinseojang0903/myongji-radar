"""매일 공개 (#19 뒷단). 수집·규칙 분석(#31의 매일 수집) 다음에 부른다.

    python -m publisher --model sonnet

1. 확인: 규칙 분석(`data/analysis.json`)이 지금 수집 원문(`data/notices.json`)으로 만든 것인지 (analysis.json의 input_sha256)
2. 매일 추출 (`python -m extractor daily`) → `data/card_bundles.json`
3. 근거 대조 (`python -m verifier`) → `data/card_bundles.verified.json` (부르기 직전에 1을 한 번 더 확인)
4. 내보내기 (`python -m exporter`) → `site/data/cards.json`

**한 단계라도 실패하면 거기서 멈춘다** (앞 단계가 0이 아니면 다음을 돌리지 않는다. 옛 묶음을 오늘 원문으로 내보내면
바뀐 공지의 옛 값이 공개될 수 있다). 그래서 멈추면 cards.json은 어제 그대로다.
예약 등록·동시 실행 잠금은 매일 수집(#31, `scripts/`)이 맡는다: 수집 → 규칙 분석 → 이 명령을 **같은 잠금 안에서** 부른다
(확인과 사용 사이에 다음 수집이 원문을 바꾸지 않게). cards.json을 커밋·푸시하는 배포(Pages)는 인증을 정한 뒤 붙인다.

단계별 기록: `data/publish_logs/<날짜>.jsonl` (단계·종료 코드·걸린 시간·건수·추출 요약. 공지 내용·오류 메시지 없음).

종료 코드 (부르는 쪽 계약: 0과 1은 정상 종료):
    0  cards.json을 새로 씀
    1  새로 썼지만 내보내기가 뺀 카드·확인할 일이 있음 (실행 로그 확인. 실패로 다루지 않는다)
    2  멈춤 (cards.json은 그대로)
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from exporter.__main__ import main as export_main
from extractor.__main__ import main as extract_main
from extractor.common import now_text
from verifier.__main__ import main as verify_main

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent / "site"  # 공개 폴더. 데이터 폴더(정리 전 원문 인용·연락처)를 이 안에 두지 않는다
EXTRACT_SUMMARY_KEYS = ("candidates", "to_extract", "given_up", "excluded", "cancellations", "bundles", "stale_bundles",
                        "waiting_new_version")


class Stop(Exception):
    """단계 실패. 메시지는 단계 이름과 종료 코드·이유 (공지 내용 없음)."""


def check_analysis(notices: Path, analysis: Path) -> None:
    """규칙 분석이 지금 수집 원문으로 만든 것인지. 아니면 분류는 옛 원문, 본문은 새 원문으로 섞인다."""
    for path in (notices, analysis):
        if not path.exists():
            raise Stop(f"{path.name}이 없음 (매일 수집·규칙 분석을 먼저)")
    try:
        recorded = json.loads(analysis.read_text(encoding="utf-8")).get("input_sha256")
    except (ValueError, AttributeError):
        raise Stop(f"{analysis.name}을 읽지 못함") from None
    if recorded != hashlib.sha256(notices.read_bytes()).hexdigest():
        raise Stop(f"{analysis.name}이 지금 {notices.name}으로 만든 것이 아님 (collector analyze를 다시)")


def run_stage(name: str, entry, argv: list[str], log: list[dict], ok_codes=(0,)) -> int:
    """단계 하나를 돌린다. 종료 코드가 ok_codes 밖이거나 예외면 Stop."""
    started = time.monotonic()
    try:
        code = entry(argv)
    except SystemExit as exit_:  # argparse 오류 등
        code = exit_.code if isinstance(exit_.code, int) else 2
    except Exception as error:  # 예상 못 한 오류도 다음 단계로 넘어가지 않게 (메시지는 찍지 않는다)
        log.append({"stage": name, "code": None, "error": type(error).__name__, "seconds": round(time.monotonic() - started, 1)})
        raise Stop(f"{name} 단계 오류 ({type(error).__name__})") from None
    log.append({"stage": name, "code": code, "seconds": round(time.monotonic() - started, 1)})
    if code not in ok_codes:
        raise Stop(f"{name} 단계가 종료 코드 {code}로 끝남")
    return code


def publish(args) -> int:
    data = args.data_dir
    if data.resolve().is_relative_to(SITE.resolve()):
        # 추출 묶음·캐시에는 정리 전 인용문·연락처가 있다. 검증기의 site 검사보다 먼저 막는다
        print("[멈춤] --data-dir이 공개 폴더 site/ 안이라 시작하지 않음", file=sys.stderr)
        return 2
    notices, analysis = data / "notices.json", data / "analysis.json"
    bundles, verified = data / "card_bundles.json", data / "card_bundles.verified.json"
    log: list[dict] = []
    started_at = now_text()
    outcome = {"stopped": "중단됨 (예외·Ctrl+C)"}  # 끝까지 가면 바뀐다
    try:
        try:
            check_analysis(notices, analysis)
            log.append({"stage": "check", "code": 0})
            extract_argv = ["daily", "--model", args.model, "--notices", str(notices), "--analysis", str(analysis),
                            "--out", str(bundles), "--cache", str(data / "extractions"),
                            "--ledger", str(data / "extract_calls.json"), "--claude", args.claude,
                            "--max-new", str(args.max_new), "--max-calls", str(args.max_calls), "--timeout", str(args.timeout)]
            if args.use_env_account:
                extract_argv.append("--use-env-account")
            run_stage("extract", extract_main, extract_argv, log)
            outcome["extract"] = _extract_summary(data / "extractions")
            check_analysis(notices, analysis)  # 추출하는 동안 다음 수집이 원문을 바꾸지 않았는지 (잠금 밖에서 불렸을 때 대비)
            run_stage("verify", verify_main, ["--bundles", str(bundles), "--notices", str(notices), "--out", str(verified)], log)
            code = run_stage("export", export_main, ["--bundles", str(verified), "--out", str(args.out)], log, ok_codes=(0, 1))
        except Stop as stop:
            outcome["stopped"] = str(stop)
            print(f"[멈춤] {stop} — cards.json은 그대로 둠", file=sys.stderr)
            return 2
        outcome.update(stopped=None, cards=_count(args.out, "cards"), bundles=_count(bundles, "bundles"))
        print(f"[공개 준비] cards.json 새로 씀 → {args.out}" + (" (내보내기 경고 있음, 로그 확인)" if code == 1 else ""),
              file=sys.stderr)
        return code
    finally:
        try:
            _write_log(data, started_at, log, outcome)
        except OSError as error:  # 기록을 못 써도 결과(종료 코드)는 그대로 알린다
            print(f"[주의] 단계 기록을 쓰지 못함 ({type(error).__name__})", file=sys.stderr)


def _extract_summary(cache: Path) -> dict | None:
    """매일 추출의 마지막 실행 기록에서 건수만 (모두 실패한 날도 단계 기록에서 보이게)."""
    try:
        last = (cache / "_runs.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        entry = json.loads(last)
    except (OSError, ValueError, IndexError):
        return None
    totals = entry.get("totals") if isinstance(entry.get("totals"), dict) else {}
    summary = {key: entry.get(key) for key in EXTRACT_SUMMARY_KEYS}
    summary.update(calls=totals.get("calls"), calls_failed=totals.get("calls_failed"))
    return summary


def _count(path: Path, key: str) -> int | None:
    try:
        return len(json.loads(path.read_text(encoding="utf-8"))[key])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_log(data: Path, started_at: str, stages: list[dict], outcome: dict) -> None:
    folder = data / "publish_logs"
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"started_at": started_at, "finished_at": now_text(), "stages": stages, **outcome}
    with open(folder / f"{started_at[:10]}.jsonl", "a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 매일 공개 (추출 → 근거 대조 → cards.json)")
    parser.add_argument("--model", required=True, help="claude --model 값 (모델 비교 #9로 정한 모델)")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="수집 데이터 폴더 (site/ 밖)")
    parser.add_argument("--out", type=Path, default=SITE / "data" / "cards.json", help="사이트 카드 파일")
    parser.add_argument("--claude", default="claude", help="claude 실행 파일 (기본: PATH의 claude)")
    parser.add_argument("--max-new", type=int, default=30, help="이번 실행에서 새로 뽑을 공지 수 상한")
    parser.add_argument("--max-calls", type=int, default=60, help="하루 호출 상한")
    parser.add_argument("--timeout", type=int, default=300, help="호출 하나의 제한 시간(초)")
    parser.add_argument("--use-env-account", action="store_true", help="ANTHROPIC_API_KEY 등 환경변수 계정으로 (본인 키일 때만)")
    args = parser.parse_args(argv)
    try:
        return publish(args)
    except OSError as error:
        print(f"[오류] 실행하지 못함 ({type(error).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
