"""추출 실행(모델 비교 eval, 매일 daily)이 함께 쓰는 것: 하루 호출 장부, 원자적 쓰기, 확인 호출, 사용량 합계, 멈춤 조건."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from extractor import claude as claude_cli

KST = timezone(timedelta(hours=9))
REPEATED_FAILURES = 3


class StopRun(Exception):
    """실행 도중 멈춤 (확인 호출 실패·격리 어긋남·같은 실패 반복·상한). 그때까지의 기록은 저장한다."""


class CallLedger:
    """하루 호출 수. 날짜(한국)별로 센다. 상한은 실수로 수백 번 부르는 것을 막는 안전장치다.
    한 번에 한 터미널에서만 돌린다 (읽고 쓰는 방식이라 동시에 돌리면 수가 어긋난다)."""

    def __init__(self, path: Path, limit: int):
        self.path, self.limit = path, limit
        self.counts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(self.counts, dict):
            raise ValueError(f"{path} 형식이 다름")

    @property
    def today(self) -> str:
        return datetime.now(KST).date().isoformat()

    def remaining(self) -> int:
        return self.limit - int(self.counts.get(self.today, 0))

    def add(self) -> None:
        self.counts[self.today] = int(self.counts.get(self.today, 0)) + 1
        write_json(self.path, self.counts)


def write_json(path: Path, data) -> None:
    """임시 파일에 다 쓴 뒤 바꿔 끼운다 (중간에 끊긴 반쪽 파일이 "이미 뽑음"으로 남지 않게)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def now_text() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def posted_day(value, fallback: str) -> str:
    """게시 시각 → 한국 날짜. 못 읽으면 fallback."""
    try:
        moment = datetime.fromisoformat(str(value))
        return (moment.astimezone(KST) if moment.tzinfo else moment).date().isoformat()
    except ValueError:
        return fallback


def new_totals() -> dict:
    return {"calls": 0, "calls_failed": 0, "probe_calls": 0, "cost_usd_estimate": 0.0, **{key: 0 for key in claude_cli.USAGE_KEYS}}


def add_usage(meta: dict, usage: dict, cost_usd, models: list[str], succeeded: bool, counter: str | None = None) -> None:
    """실패한 호출·확인 호출의 토큰·비용도 더한다 (재시도가 많은 모델이 싸 보이지 않게)."""
    totals = meta["totals"]
    counter = counter or ("calls" if succeeded else "calls_failed")
    totals[counter] = totals.get(counter, 0) + 1
    for key, value in usage.items():
        totals[key] = totals.get(key, 0) + value
    if cost_usd is not None:
        # 구독 로그인이면 실제 청구가 아니라 공개 단가로 계산한 추정치다 (월 비용 환산에 쓴다)
        totals["cost_usd_estimate"] = round(totals.get("cost_usd_estimate", 0.0) + cost_usd, 6)
    if models:
        meta["models_seen"] = sorted(set(meta.get("models_seen", [])) | set(models))


def probe(claude: str, model: str, timeout: int, allow_env_account: bool, ledger: CallLedger, meta: dict) -> None:
    """공지를 보내기 전에 한 번: 도구가 모두 꺼졌는지, 로그인 계정으로 도는지 (주입된 공지가 열린 환경으로 가지 않게).

    Raises: StopRun (상한·확인 실패)
    """
    if ledger.remaining() <= 0:
        raise StopRun(f"오늘 호출 상한 {ledger.limit}번에 닿음")
    ledger.add()
    try:
        usage, cost = claude_cli.probe(claude, model, timeout, allow_env_account)
    except claude_cli.ClaudeError as error:
        add_usage(meta, error.usage, error.cost_usd, [], succeeded=False)
        raise StopRun(f"공지를 보내기 전 확인에서 멈춤: {error} (격리 옵션을 빼지 말고 claude update 뒤 다시, 안 되면 팀에 알리기)") from None
    add_usage(meta, usage, cost, [], succeeded=True, counter="probe_calls")
    print("[확인] 도구가 모두 꺼진 채 로그인 계정으로 도는 것을 확인함", file=sys.stderr)


def after_failure(error: "claude_cli.ClaudeError", last_errors: list[str]) -> list[str]:
    """실패 하나를 받아 멈출지 정한다. 격리 어긋남은 바로, 실행·설정 문제는 같은 이유 3번 연속이면 멈춘다.
    모델이 형식에 맞게 못 답한 것은 세지 않는다 (모델 비교에서 재는 대상이고, 매일 실행에서는 그 공지만 다음에 다시).

    Returns: 새 최근 실패 목록. Raises: StopRun
    """
    if error.kind == "isolation":
        raise StopRun(f"{error} (격리 옵션을 빼지 말고 팀에 알리기)")
    recent = (last_errors + [str(error)])[-REPEATED_FAILURES:] if error.kind == "setup" else []
    if len(recent) == REPEATED_FAILURES and len(set(recent)) == 1:
        raise StopRun(f"같은 이유로 {REPEATED_FAILURES}번 연달아 실패: {recent[0]} "
                      "(격리 옵션을 빼지 말고 Claude Code를 업데이트하거나 팀에 알리기)")
    return recent
