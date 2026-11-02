import argparse
import json
import sys
import tomllib
from pathlib import Path

from .adapters import ADAPTERS
from .analysis import analyze_file
from .fetcher import Fetcher
from .models import Source
from .repository import Repository

ROOT = Path(__file__).resolve().parent.parent


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 RSS 기반 수집 프로토타입")
    parser.add_argument("command", choices=["run", "export", "analyze"])
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--rules", type=Path, default=ROOT / "config/rules.toml")
    parser.add_argument("--source", help="출처 ID 하나만 실행")
    parser.add_argument("--limit", type=int, default=3, help="출처별 상세 수집 수 (1~50)")
    args = parser.parse_args()
    if args.command == "analyze":
        try:
            summary = analyze_file(args.data_dir / "notices.json", args.rules, args.data_dir)
        except (ValueError, OSError, KeyError) as exc:
            parser.exit(1, f"분석 실패: {exc}\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"검수용 JSON: {args.data_dir / 'analysis.json'}")
        print(f"검수 목록: {args.data_dir / 'analysis_review.md'}")
        return 0
    if not 1 <= args.limit <= 50:
        parser.error("--limit은 1~50이어야 합니다")
    sources = []
    if args.command == "run":
        entries = tomllib.loads(args.config.read_text(encoding="utf-8"))["sources"]
        sources = sorted((Source(**entry) for entry in entries), key=lambda source: source.priority)
        if len({s.id for s in sources}) != len(sources):
            parser.error("출처 ID 중복")
        for source in sources:
            if source.adapter not in ADAPTERS:
                parser.error(f"미등록 어댑터: {source.adapter}")
            if source.discovery not in ("rss", "html") or (source.discovery == "html" and not source.list_url):
                parser.error(f"잘못된 발견 설정: {source.id}")
        if args.source:
            sources = [s for s in sources if s.id == args.source]
        if not sources:
            parser.error("실행할 출처가 없습니다")
    repo = Repository(args.data_dir)
    fetcher = Fetcher()
    failed = False
    try:
        if args.command == "run":
            run_id = repo.start_run()
            results = []
            for source in sources:
                stats = {"source": source.id, "discovery": source.discovery, "new": 0, "changed": 0, "unchanged": 0, "errors": []}
                adapter = ADAPTERS[source.adapter]()
                discovery_url = source.list_url if source.discovery == "html" else source.rss_url
                try:
                    refs = adapter.discover(fetcher.get(discovery_url, source.allowed_hosts), source)
                    stats["discovered"] = len(refs)
                    for ref in refs[:args.limit]:
                        try:
                            html = fetcher.get(ref.url, source.allowed_hosts)
                            notice = adapter.parse_detail(html, ref, source)
                            status = repo.save(notice, html)
                            stats[status] += 1
                            print(f"[{status}] {source.id} | {notice.title} | 본문 {len(notice.body_text)}자 / 이미지 {len(notice.images)} / 첨부 {len(notice.attachments)}")
                        except Exception as exc:
                            stats["errors"].append({"url": ref.url, "error": str(exc)})
                except Exception as exc:
                    stats["errors"].append({"url": discovery_url, "error": str(exc)})
                failed |= bool(stats["errors"])
                results.append(stats)
            repo.finish_run(run_id, results)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        count = repo.export(args.data_dir / "notices.json")
        print(f"로컬 확인용 JSON: {args.data_dir / 'notices.json'} ({count}건)")
    finally:
        fetcher.close()
        repo.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
