"""하루 한 번 수집→분석. 운영 로그에는 원문·제목·외부 오류 문자열을 쓰지 않는다."""
import argparse
import contextlib
import io
import json
import math
import sqlite3
import sys
import tomllib
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .__main__ import RUN_TIMEOUT, main as collector_main
from .config import load_sources
from .locking import AlreadyRunning, CollectorLock
from .publish_config import load_publishing, unset_local_options

ROOT = Path(__file__).resolve().parent.parent
SEOUL = timezone(timedelta(hours=9))


def current_time():
    return datetime.now(SEOUL)


def previous_exit_code(previous):
    """정상 완료만 0이며 중단·실패는 같은 날에도 실패로 보존한다."""
    saved = previous.get('exit_code')
    if previous['status'] == 'success' and (saved is None or type(saved) is int and saved == 0):
        return 0
    if type(saved) is int and saved in (1, 2, 3):
        return saved
    published = previous.get('publish_exit_code')
    if saved is None and previous.get('failure_phase') == 'publish' and type(published) is int and published in (2, 3):
        return published
    return 1


def log_skipped(data_dir, reason, code):
    """코드 갱신 전의 날짜·잠금 건너뜀도 공지 내용 없이 기록한다."""
    now = current_time()
    folder = Path(data_dir) / 'logs'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / (now.date().isoformat() + '.jsonl')).open('a', encoding='utf-8') as file:
            file.write(json.dumps({'timestamp': now.isoformat(), 'invocation_id': uuid.uuid4().hex,
                                   'event': 'skipped', 'reason': reason, 'exit_code': code}) + '\n')
    except OSError as error:
        print(f'[주의] 건너뜀 기록을 쓰지 못함 ({type(error).__name__})', file=sys.stderr)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_daily_state(path, today):
    previous = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(previous, dict) or not isinstance(previous.get("local_date"), str)
            or type(previous.get("collection_attempted")) is not bool
            or previous.get("status") not in ("collecting", "success", "partial_failure", "failed")
            or (previous.get("status") != "failed" and not previous["collection_attempted"])):
        raise ValueError("invalid state")
    previous_date = date.fromisoformat(previous["local_date"])
    if previous["local_date"] != previous_date.isoformat() or previous_date > today:
        raise ValueError("invalid daily date")
    return previous


def recover_state(data_dir, *, clock=current_time):
    """손상 원본을 보존하고 오늘은 차단한다. 같은 날 재수집을 허용하는 초기화가 아니다."""
    data_dir = Path(data_dir)
    state_path = data_dir / "daily_status.json"
    if not state_path.is_file():
        return 1
    now = clock().astimezone(SEOUL)
    try:
        with CollectorLock(data_dir):
            try:
                read_daily_state(state_path, now.date())
                return 0  # 정상 상태는 변경하지 않는다.
            except ValueError:
                pass
            original = state_path.read_bytes()
            backups = data_dir / "state_backups"
            backups.mkdir(exist_ok=True)
            backup = backups / f"daily_status-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.bin"
            with backup.open("xb") as stream:
                stream.write(original)
            # 백업에 실패하면 여기까지 오지 않으므로 원본 상태를 덮어쓰지 않는다.
            state = {"invocation_id": uuid.uuid4().hex, "local_date": now.date().isoformat(),
                     "started_at": now.isoformat(), "finished_at": now.isoformat(), "status": "failed",
                     "collection_attempted": True, "collection_run_id": None, "sources": [],
                     "collection_exit_code": None, "analysis_exit_code": None, "exit_code": 1,
                     "reason": "manual_state_recovery_skip_today", "recovery_backup": str(backup.relative_to(data_dir))}
            atomic_json(state_path, state)
            logs = data_dir / "logs"
            logs.mkdir(exist_ok=True)
            with (logs / f"{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"timestamp": now.isoformat(), "event": "state_recovered",
                    "reason": "skip_today_resume_next_day", "backup": state["recovery_backup"]}, ensure_ascii=False) + "\n")
            return 0
    except AlreadyRunning:
        return 2
    except OSError:
        return 1


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


def call_publisher(argv):
    """호출자는 수집부터 이어지는 CollectorLock을 보유한다. 외부 출력은 운영 로그에 넣지 않는다."""
    from publisher.__main__ import main as publisher_main
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return publisher_main(argv, lock_held=True, synchronized=True)
        except SystemExit as exc:
            return exc.code if type(exc.code) is int else 2


def run_daily(data_dir, config, rules, *, clock=current_time, run_timeout=RUN_TIMEOUT, publish_config=None,
              lock_held=False):
    if not math.isfinite(run_timeout) or run_timeout <= 0:
        raise ValueError("수집 시간 예산은 유한한 양수여야 합니다")
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
        with contextlib.nullcontext() if lock_held else CollectorLock(data_dir):
            # 손상된 상태를 '처음 실행'으로 간주하면 같은 날 다시 요청할 수 있다.
            if state_path.exists():
                try:
                    previous = read_daily_state(state_path, started.date())
                    previous_date = date.fromisoformat(previous["local_date"])
                except (ValueError, OSError) as exc:
                    emit("failed", reason="invalid_daily_state", phase="state_check",
                         error_type=type(exc).__name__, recovery="collector.daily --recover-state", exit_code=1)
                    return 1
                if previous_date == started.date() and previous["collection_attempted"]:
                    # 수집·분석·공개 실패와 중단 상태를 재실행으로 성공으로 덮지 않는다.
                    code = previous_exit_code(previous)
                    emit("skipped", reason="already_attempted_today", exit_code=code)
                    return code
            state = {"invocation_id": invocation, "local_date": started.date().isoformat(),
                     "started_at": started.isoformat(), "finished_at": None, "status": "starting",
                     "collection_attempted": False, "collection_run_id": None,
                     "collection_exit_code": None, "analysis_exit_code": None, "sources": []}
            state.update(publish_enabled=False, publish_exit_code=None, publish_skipped_reason=None)
            emit("started")
            exit_code = 1
            phase = "preflight"
            try:
                # 요청 전에 설정 오류를 발견하면 날짜를 소모하지 않아 고친 뒤 실행할 수 있다.
                load_sources(config)
                rule_config = tomllib.loads(rules.read_text(encoding="utf-8"))
                for key in ("version", "activity_types", "non_activity"):
                    if key not in rule_config:
                        raise ValueError("invalid rules")
                publishing = load_publishing(publish_config) if publish_config is not None else None
                state["publish_enabled"] = bool(publishing and publishing.enabled)
                unset = unset_local_options(publish_config) if publishing is not None else []
                if unset:
                    # 실행은 막지 않는다. 무인 실행이라 기록과 표준 오류에 남겨 운영자가 로컬 설정을 고치게 한다.
                    state["publish_unset_local_options"] = unset
                    emit("publish_config_notice", reason="local_config_missing_options", options=unset)
                    print(f"[설정 확인] 공개 설정에 있는 {', '.join(unset)} 칸이 로컬 설정"
                          f"(publishing.local.toml)에 없어 꺼진 채로 돈다. 켜려면 로컬 설정에 적는다", file=sys.stderr)
                before = latest_run(data_dir)
                state.update(status="collecting", collection_attempted=True)
                atomic_json(state_path, state)
                # 네트워크 요청 전에 시도 시작을 저장한다. 비정상 종료 후 같은 날 재시도하지 않는다.
                phase = "collection"
                collected = call_collector(["run", "--config", str(config), "--data-dir", str(data_dir),
                                            "--run-timeout", str(run_timeout)])
                state["collection_exit_code"] = collected
                phase = "collection_result"
                run = latest_run(data_dir)
                if run and (before is None or run["id"] > before["id"]):
                    state["collection_run_id"] = run["id"]
                    state["sources"] = [{"source": s["source"], "new": s["new"], "changed": s["changed"],
                        "unchanged": s["unchanged"], "errors": len(s["errors"]),
                        "warnings": len(s.get("warnings", []))} for s in run["sources"]]
                processed = sum(s["new"] + s["changed"] + s["unchanged"] for s in state["sources"])
                emit("collection_finished", run_id=state["collection_run_id"], exit_code=collected, sources=state["sources"])
                if collected:
                    state.update(failure_phase="collection", diagnostic="collector.sqlite3:runs")
                # main이 정상 반환했고 이번 실행이 끝났으며 1건 이상 저장한 경우만 분석한다.
                if state["collection_run_id"] is not None and run["finished_at"] and processed > 0 and collected in (0, 1):
                    phase = "analysis"
                    analyzed = call_collector(["analyze", "--rules", str(rules), "--data-dir", str(data_dir)])
                    state["analysis_exit_code"] = analyzed
                    emit("analysis_finished", exit_code=analyzed)
                    exit_code = 0 if collected == 0 and analyzed == 0 else 1
                    state["status"] = "success" if exit_code == 0 else "partial_failure"
                    if analyzed:
                        state.update(failure_phase="analysis", diagnostic="collector analyze (offline)")
                    if publishing and publishing.enabled:
                        if collected == 0 and analyzed == 0 and not any(s["errors"] for s in state["sources"]):
                            phase = "publish"
                            # Ctrl+C 같은 BaseException도 finally에서 성공으로 기록하지 않는다.
                            exit_code = 1
                            state.update(status="failed", failure_phase="publish", diagnostic="data/publish_logs")
                            state["exit_code"] = exit_code
                            atomic_json(state_path, state)
                            # 교차·이미지는 호출·시간·토큰이 늘어 로그로 구분
                            emit("publish_started", cross_model=publishing.cross_model, images=publishing.images)
                            publish_args = ["--model", publishing.model, "--data-dir", str(data_dir),
                                            "--max-new", str(publishing.max_new)]
                            if publishing.cross_model:
                                publish_args += ["--cross-model", publishing.cross_model]
                            if publishing.images:
                                publish_args.append("--images")
                            if publishing.push:
                                publish_args.append("--push")
                            published = call_publisher(publish_args)
                            if type(published) is not int or published not in (0, 1, 2, 3):
                                raise ValueError("invalid publisher exit code")
                            state["publish_exit_code"] = published
                            emit("publish_finished", exit_code=published,
                                 reason={0: "published", 1: "published_with_warnings",
                                         2: "publish_stopped", 3: "push_failed"}[published])
                            if published in (0, 1):
                                exit_code = 0
                                state["status"] = "success"
                                state.pop("failure_phase", None)
                                state.pop("diagnostic", None)
                            else:
                                exit_code = published
                                state.update(status="partial_failure" if published == 3 else "failed",
                                             failure_phase="publish", diagnostic="data/publish_logs")
                        else:
                            state["publish_skipped_reason"] = "collection_or_analysis_failed"
                            emit("publish_skipped", reason=state["publish_skipped_reason"])
                else:
                    state["status"] = "failed"
                    emit("analysis_skipped", reason="no_completed_collection_with_saved_notices")
                    if publishing and publishing.enabled:
                        state["publish_skipped_reason"] = "no_completed_collection_with_saved_notices"
                        emit("publish_skipped", reason=state["publish_skipped_reason"])
            except Exception as exc:
                # 예외 문자열에는 원문이 들어갈 수 있어 종류만 남긴다.
                exit_code = 1
                state.update(status="failed", error_type=type(exc).__name__, failure_phase=phase)
                emit("failed", phase=phase, error_type=type(exc).__name__)
            finally:
                state["finished_at"] = clock().astimezone(SEOUL).isoformat()
                state["exit_code"] = exit_code
                atomic_json(state_path, state)
                emit("finished", status=state["status"], exit_code=exit_code,
                     failure_phase=state.get("failure_phase"), run_id=state["collection_run_id"],
                     diagnostic=state.get("diagnostic"))
            return exit_code
    except AlreadyRunning:
        emit("skipped", reason="another_operation_running", exit_code=0)
        return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="하루 한 번 수집·규칙 분석과 비공개 운영 로그")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--rules", type=Path, default=ROOT / "config/rules.toml")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="수집 전체 요청 예산(초, 기본 1200)")
    parser.add_argument("--publish-config", type=Path, help="공개 단계 설정 TOML. 생략하면 수집·분석만 실행")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="설정만 확인. 수집·DB·날짜 상태 변경 없음")
    mode.add_argument("--recover-state", action="store_true", help="손상 상태 백업·복구. 오늘 수집은 차단하고 다음 날 재개")
    args = parser.parse_args(argv)
    if not math.isfinite(args.run_timeout) or args.run_timeout <= 0:
        parser.error("--run-timeout은 유한한 양수여야 합니다")
    return parser, args


def cli_main(argv=None, *, args=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = args if args is not None else parse_args(argv)[1]
    if args.dry_run or args.recover_state or args.publish_config is None:
        return main(argv)
    from runtime import launch
    from publisher.push import PushError
    try:
        paths = ["--config", str(args.config.resolve()), "--rules", str(args.rules.resolve()),
                 "--publish-config", str(args.publish_config.resolve())]
        return launch("daily", [*argv, *paths], args.data_dir)
    except AlreadyRunning:
        log_skipped(args.data_dir, 'another_operation_running', 0)
        print("[건너뜀] 같은 데이터로 다른 작업이 실행 중임")
        return 0
    except (PushError, OSError) as error:
        print(f"[멈춤] 일일 작업 준비 실패 ({type(error).__name__})", file=sys.stderr)
        return 1


def main(argv=None, *, lock_held=False, synchronized=False):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser, args = parse_args(argv)
    if args.publish_config is not None and not synchronized and not (args.dry_run or args.recover_state):
        return cli_main(argv, args=args)
    if args.recover_state:
        code = recover_state(args.data_dir)
        print(f"상태 복구 종료 코드: {code}. 정상 상태는 유지하며, 손상 상태 복구 시 오늘 재수집은 차단됩니다.")
        return code
    if args.dry_run:
        try:
            sources = load_sources(args.config)
            rules = tomllib.loads(args.rules.read_text(encoding="utf-8"))
            if not all(key in rules for key in ("version", "activity_types", "non_activity")):
                raise ValueError("잘못된 규칙 설정")
            unset = []
            if args.publish_config is not None:
                load_publishing(args.publish_config)
                unset = unset_local_options(args.publish_config)
        except (ValueError, OSError) as exc:
            parser.exit(1, f"설정 확인 실패: {type(exc).__name__}\n")
        if unset:
            print(f"[설정 확인] 공개 설정에 있는 {', '.join(unset)} 칸이 로컬 설정"
                  f"(publishing.local.toml)에 없어 꺼진 채로 돈다. 켜려면 로컬 설정에 적는다", file=sys.stderr)
        print(f"설정 확인 완료: 출처 {len(sources)}곳, 데이터 경로 {args.data_dir}")
        return 0
    code = run_daily(args.data_dir, args.config, args.rules, run_timeout=args.run_timeout,
                     publish_config=args.publish_config, lock_held=lock_held)
    print(f"일일 작업 종료 코드: {code}. 상태: {args.data_dir / 'daily_status.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(cli_main())
