import argparse
import json
import math
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from .adapters import ADAPTERS
from .analysis import analyze_file
from .assets import AssetCollector, source_order
from .config import load_sources
from .fetcher import Fetcher, RunDeadlineExceeded
from .models import NoticeRef
from .locking import AlreadyRunning, CollectorLock
from .poster_recovery import priority_refs
from .repository import Repository
from .text_safety import safe_terminal

ROOT = Path(__file__).resolve().parent.parent
RUN_TIMEOUT = 20 * 60


def main(argv=None, *, lock_held=False):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 RSS 기반 수집 프로토타입")
    parser.add_argument("command", choices=["run", "export", "analyze"])
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--rules", type=Path, default=ROOT / "config/rules.toml")
    parser.add_argument("--source", help="출처 ID 하나만 실행")
    parser.add_argument("--recover-posters", action="store_true", help="마감일 없는 공개 카드의 누락 포스터 공지만 다시 수집")
    parser.add_argument("--limit", type=int, default=50, help="출처별 상세 수집 상한 (1~50, 기본 50; 시험 시 줄여서 실행)")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="수집 전체 요청 시간 예산(초, 기본 1200)")
    args = parser.parse_args(argv)
    if args.recover_posters and args.command != "run":
        parser.error("--recover-posters는 run에서만 사용할 수 있습니다")
    if not 1 <= args.limit <= 50:
        parser.error("--limit은 1~50이어야 합니다")
    if not math.isfinite(args.run_timeout) or args.run_timeout <= 0:
        parser.error("--run-timeout은 유한한 양수여야 합니다")
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
    if not lock_held:
        try:
            with CollectorLock(args.data_dir):
                return main(argv, lock_held=True)
        except AlreadyRunning as exc:
            print(f"작업 건너뜀: {exc}")
            return 2
    if args.command == "analyze":
        try:
            summary = analyze_file(args.data_dir / "notices.json", args.rules, args.data_dir)
        except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
            parser.exit(1, f"분석 실패: {exc}\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"검수용 JSON: {args.data_dir / 'analysis.json'}")
        print(f"검수 목록: {args.data_dir / 'analysis_review.md'}")
        return 0
    deadline = time.monotonic() + args.run_timeout if args.command == "run" else None
    repo = Repository(args.data_dir)
    fetcher = Fetcher(run_deadline=deadline)
    assets = AssetCollector(args.data_dir, fetcher)
    failed = False
    try:
        if args.command == "run":
            if not args.source:
                sources = source_order(sources, repo.latest_full_run([s.id for s in sources]))
            run_id = repo.start_run()
            results = []
            exhausted = False
            def check_budget():
                if time.monotonic() >= deadline:
                    raise RunDeadlineExceeded("수집 실행 전체 시간 예산을 초과했습니다")

            def collect_ref(source, adapter, ref, stats, *, poster_first=False):
                check_budget()
                previous = repo.latest_payload(source.id, ref.external_id)
                html = fetcher.get(ref.url, source.allowed_hosts)
                notice = adapter.parse_detail(html, ref, source)
                options = {"poster_first": True} if poster_first else {}
                events = assets.enrich(notice, source.allowed_hosts, previous, **options)
                stats["errors"].extend(e for e in events if e["severity"] == "error")
                stats["warnings"].extend(e for e in events if e["severity"] == "warning")

                def reparse_previous(old_html, old_payload):
                    old_ref = NoticeRef(old_payload["external_id"], old_payload["url"],
                                        old_payload["title"], old_payload["published_at"])
                    return adapter.parse_detail(old_html, old_ref, source)

                status = repo.save(notice, html, reparse_previous)
                stats[status] += 1
                print(safe_terminal(f"[{status}] {source.id} | {notice.title} | 본문 {len(notice.body_text)}자 / 이미지 {len(notice.images)} / 첨부 {len(notice.attachments)}"))
                check_budget()

            scope = "single" if args.source or args.recover_posters else "all"
            stats_by_source = {
                s.id: {"source": s.id, "discovery": s.discovery, "new": 0, "changed": 0, "unchanged": 0,
                       "errors": [], "warnings": [], "run_scope": scope}
                for s in sources
            }
            by_id = {source.id: source for source in sources}
            recovered = set()
            attempted_sources = set()
            attempts = Counter()
            # 출처별 목록을 읽기 전에 마감일이 비어 있는 포스터 공지에 먼저 예산을 배정한다.
            for key, ref in priority_refs(ROOT.parent / "site/data/cards.json", args.data_dir / "notices.json", sources):
                source = by_id[key[0]]
                if attempts[source.id] >= args.limit:
                    continue
                attempts[source.id] += 1
                stats = stats_by_source[source.id]
                attempted_sources.add(source.id)
                try:
                    collect_ref(source, ADAPTERS[source.adapter](), ref, stats, poster_first=True)
                    recovered.add(key)
                except RunDeadlineExceeded:
                    stats["errors"].append({"url": ref.url, "error": "run_deadline_exceeded"})
                    exhausted = True
                    break
                except Exception as exc:
                    level = "errors" if args.recover_posters else "warnings"
                    stats[level].append({"url": ref.url, "error": "poster_recovery: " + type(exc).__name__})
            for source in sources:
                stats = stats_by_source[source.id]
                if args.recover_posters or exhausted:
                    if source.id in attempted_sources:
                        results.append(stats)
                    continue
                adapter = ADAPTERS[source.adapter]()
                discovery_url = source.list_url if source.discovery == "html" else source.rss_url
                try:
                    check_budget()
                    refs = adapter.discover(fetcher.get(discovery_url, source.allowed_hosts), source)
                    stats["errors"].extend(getattr(adapter, "discovery_errors", []))
                    stats["discovered"] = len(refs)
                    # 미수집 글·일반 글 우선은 유지한다. 저장된 같은 그룹에서는 미수집 첨부를 먼저,
                    # 같은 우선순위에서는 원래 발견 순서를 유지한다.
                    def priority(ref):
                        previous = repo.latest_payload(source.id, ref.external_id)
                        return previous is not None, ref.is_pinned, not assets.has_pending(previous)
                    refs = sorted(refs, key=priority)
                    already = {external_id for sid, external_id in recovered if sid == source.id}
                    remaining = (ref for ref in refs if ref.external_id not in already)
                    for index, ref in enumerate(remaining):
                        if index >= args.limit - attempts[source.id]:
                            break
                        try:
                            collect_ref(source, adapter, ref, stats)
                        except RunDeadlineExceeded:
                            stats["errors"].append({"url": ref.url, "error": "run_deadline_exceeded"})
                            exhausted = True
                            break
                        except Exception as exc:
                            stats["errors"].append({"url": ref.url, "error": str(exc)})
                except RunDeadlineExceeded:
                    stats["errors"].append({"url": discovery_url, "error": "run_deadline_exceeded"})
                    exhausted = True
                except Exception as exc:
                    stats["errors"].append({"url": discovery_url, "error": str(exc)})
                if not exhausted and time.monotonic() >= deadline:
                    stats["errors"].append({"url": discovery_url, "error": "run_deadline_exceeded"})
                    exhausted = True
                results.append(stats)
            failed = any(row["errors"] for row in results)
            repo.finish_run(run_id, results)
            print(safe_terminal(json.dumps(results, ensure_ascii=False, indent=2)))
        count = repo.export(args.data_dir / "notices.json")
        print(f"로컬 확인용 JSON: {args.data_dir / 'notices.json'} ({count}건)")
    finally:
        fetcher.close()
        repo.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
