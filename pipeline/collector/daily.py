"""하루 한 번 수집→분석. 운영 로그에는 원문·제목·외부 오류 문자열을 쓰지 않는다."""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tomllib
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .__main__ import main as collector_main
from .config import load_sources
from .locking import AlreadyRunning, CollectorLock

ROOT = Path(__file__).resolve().parent.parent
SEOUL = timezone(timedelta(hours=9))


def current_time():
    return datetime.now(SEOUL)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def latest_run(data_dir):
    path = data_dir / "collector.sqlite3"
    if not path.exists():
        return None
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
            return None
        row = db.execute("SELECT id, finished_at, result_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"id": row[0], "finished_at": row[1], "sources": json.loads(row[2]) if row[2] else []} if row else None


def call_collector(argv):
    # 외부 제목·본문·오류 출력은 메모리에서 버리고 운영 로그에는 요약만 남긴다.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return collector_main(argv, lock_held=True)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1


def run_daily(data_dir, config, rules, *, clock=current_time):
    data_dir, config, rules = Path(data_dir), Path(config), Path(rules)
    started = clock().astimezone(SEOUL)
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{started.date().isoformat()}.jsonl"
    state_path = data_dir / "daily_status.json"
    invocation = uuid.uuid4().hex

    def emit(event, **values):
        row = {"timestamp": clock().astimezone(SEOUL).isoformat(), "invocation_id": invocation,
               "event": event, **values}
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")

    try:
        with CollectorLock(data_dir):
            # 손상된 상태를 '처음 실행'으로 간주하면 같은 날 다시 요청할 수 있다.
            if state_path.exists():
                try:
                    previous = json.loads(state_path.read_text(encoding="utf-8"))
                    if (not isinstance(previous, dict) or not isinstance(previous.get("local_date"), str)
                            or type(previous.get("collection_attempted")) is not bool
                            or previous.get("status") not in ("collecting", "success", "partial_failure", "failed")
                            or (previous.get("status") != "failed" and not previous["collection_attempted"])):
                        raise ValueError("invalid state")
                    previous_date = date.fromisoformat(previous["local_date"])
                    if previous["local_date"] != previous_date.isoformat():
                        raise ValueError("daily date is not canonical")
                    if previous_date > started.date():
                        raise ValueError("daily state is in the future")
                except (ValueError, OSError):
                    emit("failed", reason="invalid_daily_state", exit_code=1)
                    return 1
                if previous_date == started.date() and previous["collection_attempted"]:
                    emit("skipped", reason="already_attempted_today", exit_code=0)
                    return 0
            state = {"invocation_id": invocation, "local_date": started.date().isoformat(),
                     "started_at": started.isoformat(), "finished_at": None, "status": "starting",
                     "collection_attempted": False, "collection_run_id": None,
                     "collection_exit_code": None, "analysis_exit_code": None, "sources": []}
            emit("started")
            exit_code = 1
            try:
                # 요청 전에 설정 오류를 발견하면 날짜를 소모하지 않아 고친 뒤 실행할 수 있다.
                load_sources(config)
                rule_config = tomllib.loads(rules.read_text(encoding="utf-8"))
                for key in ("version", "activity_types", "non_activity"):
                    if key not in rule_config:
                        raise ValueError("invalid rules")
                before = latest_run(data_dir)
                state.update(status="collecting", collection_attempted=True)
                atomic_json(state_path, state)
                # 네트워크 요청 전에 시도 시작을 저장한다. 비정상 종료 후 같은 날 재시도하지 않는다.
                collected = call_collector(["run", "--config", str(config), "--data-dir", str(data_dir)])
                state["collection_exit_code"] = collected
                run = latest_run(data_dir)
                if run and (before is None or run["id"] > before["id"]):
                    state["collection_run_id"] = run["id"]
                    state["sources"] = [{"source": s["source"], "new": s["new"], "changed": s["changed"],
                        "unchanged": s["unchanged"], "errors": len(s["errors"])} for s in run["sources"]]
                processed = sum(s["new"] + s["changed"] + s["unchanged"] for s in state["sources"])
                emit("collection_finished", run_id=state["collection_run_id"], exit_code=collected, sources=state["sources"])
                # main이 정상 반환했고 이번 실행이 끝났으며 1건 이상 저장한 경우만 분석한다.
                if state["collection_run_id"] is not None and run["finished_at"] and processed > 0 and collected in (0, 1):
                    analyzed = call_collector(["analyze", "--rules", str(rules), "--data-dir", str(data_dir)])
                    state["analysis_exit_code"] = analyzed
                    emit("analysis_finished", exit_code=analyzed)
                    exit_code = 0 if collected == 0 and analyzed == 0 else 1
                    state["status"] = "success" if exit_code == 0 else "partial_failure"
                else:
                    state["status"] = "failed"
                    emit("analysis_skipped", reason="no_completed_collection_with_saved_notices")
            except Exception as exc:
                # 예외 문자열에는 원문이 들어갈 수 있어 종류만 남긴다.
                state.update(status="failed", error_type=type(exc).__name__)
                emit("failed", error_type=type(exc).__name__)
            finally:
                state["finished_at"] = clock().astimezone(SEOUL).isoformat()
                state["exit_code"] = exit_code
                atomic_json(state_path, state)
                emit("finished", status=state["status"], exit_code=exit_code)
            return exit_code
    except AlreadyRunning:
        emit("skipped", reason="another_operation_running", exit_code=0)
        return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="하루 한 번 수집·규칙 분석과 비공개 운영 로그")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--rules", type=Path, default=ROOT / "config/rules.toml")
    parser.add_argument("--dry-run", action="store_true", help="설정만 확인. 수집·DB·날짜 상태 변경 없음")
    args = parser.parse_args()
    if args.dry_run:
        try:
            sources = load_sources(args.config)
            rules = tomllib.loads(args.rules.read_text(encoding="utf-8"))
            if not all(key in rules for key in ("version", "activity_types", "non_activity")):
                raise ValueError("잘못된 규칙 설정")
        except (ValueError, OSError) as exc:
            parser.exit(1, f"설정 확인 실패: {type(exc).__name__}\n")
        print(f"설정 확인 완료: 출처 {len(sources)}곳, 데이터 경로 {args.data_dir}")
        return 0
    code = run_daily(args.data_dir, args.config, args.rules)
    print(f"일일 작업 종료 코드: {code}. 상태: {args.data_dir / 'daily_status.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
