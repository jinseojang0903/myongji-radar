"""검증된 두 시점의 마감 차이를 비공개 검토 보고서로만 저장한다."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys

from collector.locking import CollectorLock
from extractor.common import now_text, write_json
from .groups import load_groups
from .history import compare_snapshots

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SITE = REPO / "site"
PRIVATE_PREVIEW = ROOT / "preview-data"
OUTPUT_NAME = "deadline-review.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="검증된 두 추출 시점의 마감 변경 후보 검토")
    parser.add_argument("--before", type=Path, required=True, help="이전 시점 검증 묶음 JSON")
    parser.add_argument("--after", type=Path, required=True, help="이후 시점 검증 묶음 JSON")
    parser.add_argument("--groups", type=Path, default=ROOT / "config/contest_groups.toml")
    parser.add_argument("--out", type=Path, required=True, help="별도 비공개 폴더의 deadline-review.json")
    args = parser.parse_args(argv)
    try:
        before, after, groups_path = (path.resolve() for path in (args.before, args.after, args.groups))
        target = args.out.resolve()
        inputs = (before, after, groups_path)
        if (before == after or target.name != OUTPUT_NAME or target in inputs
                or target.is_relative_to(SITE.resolve())
                or any(path.is_relative_to(SITE.resolve()) for path in inputs)
                or (target.is_relative_to(REPO.resolve()) and not target.is_relative_to(PRIVATE_PREVIEW.resolve()))
                or any(target.is_relative_to(path.parent) for path in (before, after))):
            raise ValueError("검토 보고서는 입력과 분리된 비공개 폴더에 저장해야 합니다")
        # 두 시점이 같은 폴더에 있을 때는 한 번만 잠그며, 여러 폴더는 일정한 순서로 잡는다.
        with ExitStack() as stack:
            for directory in sorted({before.parent, after.parent}, key=str):
                stack.enter_context(CollectorLock(directory))
            old = json.loads(before.read_text(encoding="utf-8"))
            new = json.loads(after.read_text(encoding="utf-8"))
            result = compare_snapshots(old, new, load_groups(groups_path), checked_at=now_text())
            if (not isinstance(result, dict) or not isinstance(result.get("proposals"), list)
                    or not isinstance(result.get("skipped"), list)):
                raise ValueError("검토 보고서 형식 오류")
            proposal_count, skipped_count = len(result["proposals"]), len(result["skipped"])
            write_json(target, result)
    except Exception as exc:
        print(f"[중단] 마감 비교 실패 ({type(exc).__name__}). 기존 출력은 유지합니다.", file=sys.stderr)
        return 2
    print(f"[검토] 마감 변경 후보 {proposal_count}개, 비교 보류 {skipped_count}개", file=sys.stderr)
    return 1 if proposal_count or skipped_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
