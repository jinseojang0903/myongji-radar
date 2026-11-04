import argparse
import json
import sys
from pathlib import Path

from .adapters import ADAPTERS
from .analysis import analyze_file
from .config import load_sources
from .fetcher import Fetcher
from .models import NoticeRef
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
    parser.add_argument("--limit", type=int, default=50, help="출처별 상세 수집 상한 (1~50, 기본 50; 시험 시 줄여서 실행)")
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
        try:
            sources = load_sources(args.config)
        except (ValueError, OSError) as exc:
            parser.error(f"출처 설정 오류: {exc}")
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
                    # 미수집 글을 먼저, 같은 그룹에서는 일반 글을 고정 글보다 먼저.
                    # stable sort로 각 그룹의 원래 발견 순서는 유지한다.
                    refs = sorted(refs, key=lambda ref: (repo.has_notice(source.id, ref.external_id), ref.is_pinned))
                    for ref in refs[:args.limit]:
                        try:
                            html = fetcher.get(ref.url, source.allowed_hosts)
                            notice = adapter.parse_detail(html, ref, source)
                            def reparse_previous(old_html, old_payload):
                                old_ref = NoticeRef(old_payload["external_id"], old_payload["url"],
                                                    old_payload["title"], old_payload["published_at"])
                                return adapter.parse_detail(old_html, old_ref, source)

                            status = repo.save(notice, html, reparse_previous)
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
