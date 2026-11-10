"""수동 병합 미리보기. 운영 데이터나 공개 파일을 갱신하지 않는다."""
import argparse
import json
import sys
from pathlib import Path

from collector.locking import AlreadyRunning, CollectorLock
from exporter.blocklist import load_blocked
from extractor.common import write_json
from .core import preview
from .groups import load_groups

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent / "site"
OUTPUT_NAME = "contest-preview.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="운영 경로와 분리된 공지 병합 미리보기")
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--groups", type=Path, default=ROOT / "config/contest_groups.toml")
    parser.add_argument("--blocked", type=Path, default=ROOT / "config/blocked_cards.toml")
    parser.add_argument("--out", type=Path, required=True, help="별도 폴더의 contest-preview.json")
    args = parser.parse_args(argv)
    try:
        target = args.out.resolve()
        inputs = [path.resolve() for path in (args.bundles, args.notices, args.groups, args.blocked)]
        if (target.name != OUTPUT_NAME or target.is_relative_to(SITE.resolve())
                or target in inputs or any(target.is_relative_to(path.parent) for path in inputs)
                or any(path.is_relative_to(SITE.resolve()) for path in inputs)):
            raise ValueError("미리보기는 site 밖 별도 폴더의 고정 파일명으로 저장해야 합니다")
        with CollectorLock(args.notices.parent):
            data = json.loads(args.bundles.read_text(encoding="utf-8"))
            raw = json.loads(args.notices.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("notices"), list):
                raise ValueError("원문 notices 배열 없음")
            result = preview(data, raw["notices"], load_groups(args.groups), set(load_blocked(args.blocked)))
            write_json(args.out, result)
    except (AlreadyRunning, OSError, ValueError, TypeError, KeyError):
        print("[중단] 미리보기 입력 또는 경로를 확인하세요. 기존 출력은 유지합니다.", file=sys.stderr)
        return 2
    fallback = sum(group["action"] == "fallback" for group in result["report"]["groups"])
    print(f"[미리보기] 후보 {len(result['candidates'])}개, 개별 카드 유지 {fallback}그룹", file=sys.stderr)
    return 1 if fallback or result["report"]["blocked"]["unmatched_requested_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
