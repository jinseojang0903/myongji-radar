"""잠금을 보유한 채 코드를 갱신하고 새 프로세스에서 운영 작업을 시작한다."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.locking import CollectorLock
from publisher.push import prepare

ROOT = Path(__file__).resolve().parent
CHILD = (
    "import importlib,sys; "
    "module={'daily':'collector.daily','publisher':'publisher.__main__'}[sys.argv[1]]; "
    "sys.exit(importlib.import_module(module).main(sys.argv[2:],lock_held=True,synchronized=True))"
)


def launch(mode, argv, data_dir):
    """기존 카드 전용 Git 정책을 유지한다. 자식이 끝날 때까지 같은 잠금을 보유한다."""
    if mode not in ("daily", "publisher"):
        raise ValueError("지원하지 않는 실행 모드")
    data_dir = Path(data_dir).resolve()
    with CollectorLock(data_dir):
        phase, code, error_type = "prepare", 1 if mode == "daily" else 2, None
        try:
            prepare(ROOT.parent)
            phase = "worker"
            # 원격 코드로 갱신된 뒤에 import한다. 부모가 미리 읽은 단계 함수는 실행하지 않는다.
            code = subprocess.run(
                [sys.executable, "-c", CHILD, mode, *argv, "--data-dir", str(data_dir)],
                cwd=ROOT, check=False,
            ).returncode
            return code
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            now = datetime.now(timezone(timedelta(hours=9)))
            try:
                folder = data_dir / "logs"
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / f"runtime-{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"timestamp": now.isoformat(), "mode": mode, "phase": phase,
                                          "exit_code": code, "error_type": error_type}) + "\n")
            except OSError as error:
                print(f"[주의] 실행 기록을 쓰지 못함 ({type(error).__name__})", file=sys.stderr)
