"""AI 추출 실행 (#14). 두 가지: 모델 비교(#9)용 "정답표 대회 추출"(eval)과 매일 실행용 추출(daily, `extractor/daily.py`).

    python -m extractor eval --model haiku --run-dir ../eval/extraction/runs/2026-11-06_haiku_v0.2

- 정답표 `eval/answer_key/sources.csv`의 대회마다, 그 공지들을 수집 원문(`data/notices.json`)에서 찾아 v0.2 입력을 만들고
  `claude -p`를 한 번 부른다. 결과는 채점기(`eval/extraction/score.py`)가 읽는 실행 폴더에 쓴다.
- 실행하는 사람의 Claude 로그인으로 돈다. ANTHROPIC_API_KEY 같은 환경변수가 있으면 그 계정으로 가므로 멈춘다
  (본인 API 키로 돌리려면 `--use-env-account`).
- 하루 호출 상한(`--max-calls`, 기본 60)을 `data/extract_calls.json`에 센다. 한 번에 한 터미널에서만 돌린다 (동시에 돌리면 수가 어긋난다).
- 이미 출력이 있는 대회는 건너뛴다 (중간에 멈춰도 이어서 돌릴 수 있다). 다시 뽑으려면 `--force` (옛 출력은 .bak으로 옮긴다).
- 공지를 보내기 전에 공지 없이 한 번 불러(probe) 도구가 모두 꺼졌는지·로그인 계정인지 확인한다. 어긋나면 멈춘다.
- 실행·설정 문제로 같은 이유가 3번 연달아 나면 멈춘다 (상한만 쓰지 않게). 모델이 형식에 맞게 못 답한 것은 세지 않는다.
- 실행 폴더(`eval/extraction/runs/`)는 저장소에 올리지 않는다 (공지 인용문이 들어 있다).

먼저 `--dry-run`으로 정답표 공지가 수집 원문에 다 있는지 본다 (claude를 부르지 않는다).

실행 폴더:
    meta.json                 모델·프롬프트 버전·넣은 입력·토큰 합계 (호출마다 저장. 채점기 보고서 제목에 쓰인다)
    outputs/<contest_id>.json AI 출력 (schema.json 모양)
    logs/<contest_id>.json    호출 기록: 걸린 시간, 토큰, 추정 비용, 실패 이유 (공지 내용은 없다)

종료 코드: 0 모두 뽑음(또는 --dry-run에서 모두 준비됨), 1 일부 실패·빠짐·상한 도달 (다시 돌리면 남은 것만),
          2 시작하지 못함·같은 실패 반복으로 멈춤 (입력·설정 오류)
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from extractor import claude as claude_cli
from extractor.common import (CallLedger, StopRun, add_usage, after_failure, new_totals, now_text, posted_day, probe,
                              write_json)
from extractor.daily import DEFAULT_MAX_NEW, run_daily
from extractor.prompt import PROMPT_VERSION, InputNotice, build_input

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DEFAULT_MAX_CALLS = 60  # 정답표 17건 × 모델 3개 = 51번에 여유
EXAMPLE_ID = "example-id"
CONTEST_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # 파일 이름이 된다 (경로 문자 금지)


def normalize_url(url: str) -> str:
    """정답표(사람이 적음)와 수집 원문의 주소를 맞춘다: 앞뒤 공백, 스킴·호스트 대소문자, 끝의 /."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def read_sources(answers: Path) -> dict[str, list[dict]]:
    """sources.csv → {contest_id: [행]} (공지 번호 순).

    Raises: ValueError (contest_id가 파일 이름으로 쓸 수 없는 모양)
    """
    contests = defaultdict(list)
    with open(answers / "sources.csv", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            contest_id = (row.get("contest_id") or "").strip()
            if not contest_id or contest_id == EXAMPLE_ID:
                continue
            if not CONTEST_ID.fullmatch(contest_id):
                raise ValueError(f"sources.csv의 contest_id 모양이 다름: {contest_id[:40]!r} (영문 소문자·숫자·-만)")
            contests[contest_id].append(row)
    return {contest_id: sorted(rows, key=lambda row: int(row["notice"])) for contest_id, rows in contests.items()}


def read_originals(path: Path) -> tuple[dict[str, dict], set[str]]:
    """수집 원문 → ({정규화한 url: 공지}, 두 번 나온 url). 두 번 나오면 어느 판인지 몰라 쓰지 않는다."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    notices = raw.get("notices") if isinstance(raw, dict) else None
    if not isinstance(notices, list):
        raise ValueError(f"{path} 최상위에 notices 배열이 없음")
    by_url, duplicated = {}, set()
    for notice in notices:
        if not isinstance(notice, dict) or not isinstance(notice.get("url"), str):
            continue
        url = normalize_url(notice["url"])
        if url in by_url:
            duplicated.add(url)
        by_url[url] = notice
    return {url: notice for url, notice in by_url.items() if url not in duplicated}, duplicated


def contest_input(rows: list[dict], originals: dict[str, dict], duplicated: set[str]) -> tuple[list[InputNotice], list[str]]:
    """정답표 공지 행 → (AI 입력 공지들, 문제 목록 "없음: url" / "중복: url")."""
    notices, problems = [], []
    for row in rows:
        url = normalize_url(row.get("url") or "")
        original = originals.get(url)
        if original is None:
            problems.append(f"{'중복' if url in duplicated else '없음'}: {url}")
            continue
        notices.append(InputNotice(board=(row.get("board") or "").strip() or str(original.get("source_id") or ""),
                                   posted=posted_day(original.get("published_at"), (row.get("posted") or "").strip()),
                                   url=str(original["url"]), title=str(original.get("title") or ""),
                                   body=str(original.get("body_text") or "")))
    return notices, problems


def check_inputs(selected: dict[str, list[dict]], originals: dict[str, dict], duplicated: set[str]) -> int:
    """부르지 않고 입력만 확인한다 (--dry-run): 대회마다 공지가 수집 원문에 다 있는지, 입력 길이."""
    ready = 0
    for contest_id, rows in selected.items():
        notices, problems = contest_input(rows, originals, duplicated)
        if problems:
            print(f"[빠짐] {contest_id}: {len(problems)}/{len(rows)}개 공지 ({'; '.join(problems)})")
            continue
        text, omitted = build_input(notices)
        ready += 1
        print(f"[준비] {contest_id}: 공지 {len(notices)}개, 입력 {len(text):,}자" + (f", 결과 공지 본문 뺌 {omitted}" if omitted else ""))
    print(f"[확인] {ready}/{len(selected)}건을 부를 수 있음 (claude는 부르지 않음). "
          "'없음'은 수집되지 않은 공지, '중복'은 수집 원문에 같은 주소가 두 번 있는 공지")
    return 0 if ready == len(selected) else 1


def run_eval(args) -> int:
    sources = read_sources(args.answers)
    unknown = sorted(set(args.contest or []) - set(sources))
    if unknown:
        raise ValueError(f"정답표에 없는 대회: {', '.join(unknown)}")
    selected = {contest_id: rows for contest_id, rows in sources.items() if not args.contest or contest_id in args.contest}
    originals, duplicated = read_originals(args.notices)
    if args.dry_run:
        return check_inputs(selected, originals, duplicated)
    in_use = claude_cli.account_env_in_use()
    if in_use and not args.use_env_account:
        raise ValueError(f"환경변수 {', '.join(in_use)}가 있어 구독 로그인 대신 그 계정으로 갈 수 있음. "
                         "지우고 다시 돌리거나, 본인 API 키가 맞으면 --use-env-account")
    claude = claude_cli.resolve_claude(args.claude)
    claude_cli.build_command(claude, args.model)  # 모델 이름·명령줄 길이를 부르기 전에 확인
    ledger = CallLedger(args.ledger, args.max_calls)
    outputs, logs = args.run_dir / "outputs", args.run_dir / "logs"
    meta = _load_meta(args.run_dir, args.model)
    meta["claude_version"] = claude_cli.version(claude)
    meta["account"] = "env" if in_use else "login"
    try:
        if _needs_calls(args, selected, outputs, originals, duplicated):
            probe(claude, args.model, args.timeout, args.use_env_account, ledger, meta)
        failed = _extract_all(args, selected, originals, duplicated, claude, ledger, meta, outputs, logs)
    except StopRun as stop:
        print(f"[멈춤] {stop}", file=sys.stderr)
        return 2
    finally:
        try:
            _write_meta(args.run_dir, meta)
        except OSError as error:  # 원래 예외를 덮지 않는다
            print(f"[주의] meta.json을 쓰지 못함 ({type(error).__name__})", file=sys.stderr)
    done = sum(1 for contest_id in selected if (outputs / f"{contest_id}.json").exists())
    print(f"[끝] {done}/{len(selected)}건 출력 → {args.run_dir} (오늘 남은 호출 {ledger.remaining()}번)", file=sys.stderr)
    return 1 if failed else 0


def _needs_calls(args, selected, outputs, originals, duplicated) -> bool:
    """실제로 부를 대회가 있는지 (출력이 없거나 --force이고, 수집 원문 문제가 없는 대회). 없으면 확인 호출도 하지 않는다."""
    return any((args.force or not (outputs / f"{contest_id}.json").exists()) and not contest_input(rows, originals, duplicated)[1]
               for contest_id, rows in selected.items())


def _extract_all(args, selected, originals, duplicated, claude, ledger, meta, outputs, logs) -> int:
    failed, last_errors = 0, []
    for contest_id, rows in selected.items():
        target = outputs / f"{contest_id}.json"
        if target.exists() and not args.force:
            continue
        notices, problems = contest_input(rows, originals, duplicated)
        if problems:
            # 공지 일부만으로 뽑으면 모델 성능이 아니라 수집 누락을 재게 된다 (채점기는 출력 실패로 센다)
            write_json(logs / f"{contest_id}.json", _log({"error": "수집 원문 문제로 부르지 않음", "problems": problems}))
            print(f"[건너뜀] {contest_id}: {'; '.join(problems)}", file=sys.stderr)
            failed += 1
            continue
        if ledger.remaining() <= 0:
            print(f"[멈춤] 오늘 호출 상한 {ledger.limit}번에 닿음 (내일 다시 돌리면 남은 대회만 부른다)", file=sys.stderr)
            return failed + 1
        text, omitted = build_input(notices)
        meta.setdefault("result_bodies_omitted", {})[contest_id] = omitted
        if target.exists():
            os.replace(target, target.with_suffix(".json.bak"))  # --force: 실패해도 옛 출력이 새 결과처럼 채점되지 않게
        ledger.add()  # 부르기 직전에 센다 (실패한 호출도 사용량을 쓴다)
        try:
            result = claude_cli.call(text, claude, args.model, args.timeout, args.use_env_account)
        except claude_cli.ClaudeError as error:
            add_usage(meta, error.usage, error.cost_usd, [], succeeded=False)
            write_json(logs / f"{contest_id}.json", _log({"error": str(error), "usage": error.usage, "cost_usd": error.cost_usd,
                                                          "result_bodies_omitted": omitted}))
            _write_meta(args.run_dir, meta)
            print(f"[실패] {contest_id}: {error}", file=sys.stderr)
            failed += 1
            last_errors = after_failure(error, last_errors)
            continue
        last_errors = []
        write_json(target, result.output)
        write_json(logs / f"{contest_id}.json", _log({"seconds": round(result.seconds, 1), "usage": result.usage,
                                                      "cost_usd": result.cost_usd, "notices": len(notices),
                                                      "result_bodies_omitted": omitted, "input_chars": len(text)}))
        add_usage(meta, result.usage, result.cost_usd, result.models, succeeded=True)
        if result.from_text:
            # 매일 추출은 이런 결과를 받지 않는다: 0이 아니면 daily를 돌리기 전에 알 수 있게 센다
            meta["totals"]["text_results"] = meta["totals"].get("text_results", 0) + 1
        _write_meta(args.run_dir, meta)  # 호출마다 저장: 중간에 끊겨도 합계가 남는다
        print(f"[뽑음] {contest_id} ({result.seconds:.0f}초)", file=sys.stderr)
    return failed


def _load_meta(run_dir: Path, model: str) -> dict:
    path = run_dir / "meta.json"
    if path.exists():
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or meta.get("run_type") != "ai_extraction":
            raise ValueError(f"{run_dir}는 AI 추출 실행 폴더가 아님 (새 이름을 쓰기)")
        if meta.get("model") != model or meta.get("prompt_version") != PROMPT_VERSION:
            raise ValueError(f"{run_dir}는 {meta.get('model')} / {meta.get('prompt_version')} 실행이라 섞지 않음 (새 이름을 쓰기)")
        return meta
    return {"model": model, "prompt_version": PROMPT_VERSION, "run_type": "ai_extraction",
            "started_at": now_text(),
            # 지금 AI에게 넣는 것. 이미지·첨부는 #12 뒤에 넣는다 (그 전 점수는 포스터에만 있는 정보를 놓친다)
            "inputs": {"body": True, "images": False, "attachments": False, "result_notice_bodies": False,
                       "student_ids_masked": True},
            "totals": new_totals()}


def _write_meta(run_dir: Path, meta: dict) -> None:
    meta["updated_at"] = now_text()
    write_json(run_dir / "meta.json", meta)


def _log(entry: dict) -> dict:
    return {"at": now_text(), **entry}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 AI 추출 (#14)")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("eval", help="정답표 대회를 뽑아 채점용 실행 폴더를 만든다 (모델 비교 #9)")
    evaluate.add_argument("--model", required=True,
                          help="claude --model 값. 별칭 haiku·sonnet·opus 또는 정확한 모델 ID (실제로 쓰인 ID는 meta.json models_seen에 남는다)")
    evaluate.add_argument("--run-dir", type=Path, required=True, help="실행 폴더 (eval/extraction/runs/<날짜>_<모델>_v0.2)")
    evaluate.add_argument("--answers", type=Path, default=REPO / "eval" / "answer_key", help="정답표 폴더")
    evaluate.add_argument("--notices", type=Path, default=ROOT / "data" / "notices.json", help="수집 원문 (python -m collector export)")
    evaluate.add_argument("--contest", action="append", help="이 대회만 (여러 번 줄 수 있음. 첫 시험은 하나로)")
    evaluate.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"하루 호출 상한 (기본 {DEFAULT_MAX_CALLS})")
    evaluate.add_argument("--ledger", type=Path, default=ROOT / "data" / "extract_calls.json", help=argparse.SUPPRESS)
    evaluate.add_argument("--claude", default="claude", help="claude 실행 파일 (기본: PATH의 claude)")
    evaluate.add_argument("--timeout", type=int, default=300, help="호출 하나의 제한 시간(초)")
    evaluate.add_argument("--force", action="store_true", help="이미 출력이 있는 대회도 다시 뽑는다 (옛 출력은 .bak)")
    evaluate.add_argument("--dry-run", action="store_true", help="claude를 부르지 않고 공지가 다 수집됐는지·입력 길이만 본다")
    evaluate.add_argument("--use-env-account", action="store_true",
                          help="ANTHROPIC_API_KEY 등 환경변수의 계정으로 돌린다 (본인 키일 때만)")
    daily = commands.add_parser("daily", help="수집 원문·규칙 분석에서 대회 후보를 뽑아 카드 묶음 파일을 만든다 (매일 실행)")
    daily.add_argument("--model", required=True, help="claude --model 값 (모델 비교 #9로 정한 모델)")
    daily.add_argument("--notices", type=Path, default=ROOT / "data" / "notices.json", help="수집 원문 (python -m collector export)")
    daily.add_argument("--analysis", type=Path, default=ROOT / "data" / "analysis.json", help="규칙 분석 (python -m collector analyze)")
    daily.add_argument("--out", type=Path, default=ROOT / "data" / "card_bundles.json", help="카드 묶음 (검증기·내보내기 입력)")
    daily.add_argument("--cache", type=Path, default=ROOT / "data" / "extractions", help="공지 판별 추출 결과 (다시 부르지 않게)")
    daily.add_argument("--config", type=Path, default=ROOT / "config" / "sources.toml", help=argparse.SUPPRESS)
    daily.add_argument("--max-new", type=int, default=DEFAULT_MAX_NEW, help=f"이번 실행에서 새로 부를 공지 수 상한 (기본 {DEFAULT_MAX_NEW})")
    daily.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"하루 호출 상한 (기본 {DEFAULT_MAX_CALLS})")
    daily.add_argument("--ledger", type=Path, default=ROOT / "data" / "extract_calls.json", help=argparse.SUPPRESS)
    daily.add_argument("--claude", default="claude", help="claude 실행 파일 (기본: PATH의 claude)")
    daily.add_argument("--timeout", type=int, default=300, help="호출 하나의 제한 시간(초)")
    daily.add_argument("--force", action="store_true", help="뽑아 둔 결과가 있어도 다시 부른다")
    daily.add_argument("--use-env-account", action="store_true",
                       help="ANTHROPIC_API_KEY 등 환경변수의 계정으로 돌린다 (본인 키일 때만)")
    args = parser.parse_args(argv)
    try:
        return run_eval(args) if args.command == "eval" else run_daily(args)
    except (OSError, ValueError, KeyError, TypeError, csv.Error, sqlite3.Error, claude_cli.ClaudeError) as error:
        print(f"[오류] 시작하지 못함 ({type(error).__name__}): {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
