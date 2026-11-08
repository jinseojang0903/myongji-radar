"""검토된 공지 병합 CLI. AI·학교 요청 없이 로컬 추출값만 사용한다."""
import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

from extractor.common import now_text
from extractor.daily import card_id_of
from exporter.blocklist import load_blocked
from collector.locking import AlreadyRunning, CollectorLock
from merger.evidence import deadline_context_is_grounded, update_context_is_grounded, update_is_grounded
from merger.groups import load_groups
from merger.merge import merge_document
from verifier.__main__ import write_json
from verifier.checks import check_extraction

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent / "site"


def merge_files(bundles: Path, notices: Path, analysis: Path, groups: Path, out: Path, blocked: Path):
    if out.resolve().is_relative_to(SITE.resolve()):
        raise ValueError("병합 원본은 site 안에 저장할 수 없습니다")
    if out.resolve().parent != notices.resolve().parent:
        raise ValueError("병합 출력은 수집 입력과 같은 비공개 데이터 폴더여야 합니다")
    if out.resolve() in {path.resolve() for path in (bundles, notices, analysis, groups, blocked)}:
        raise ValueError("병합 출력은 입력과 다른 파일이어야 합니다")
    raw_notices = notices.read_bytes()
    analyzed = json.loads(analysis.read_text(encoding="utf-8"))
    if not isinstance(analyzed, dict) or analyzed.get("input_sha256") != hashlib.sha256(raw_notices).hexdigest():
        raise ValueError("규칙 분석과 수집 입력이 다릅니다")
    raw = json.loads(raw_notices)
    if not isinstance(raw, dict) or not isinstance(raw.get("notices"), list):
        raise ValueError("수집 입력 형식 오류")
    originals = {}
    for item in raw["notices"]:
        if not isinstance(item, dict):
            raise ValueError("수집 공지 형식 오류")
        key = (item["source_id"], item["external_id"])
        if key in originals:
            raise ValueError("수집 공지 식별자가 중복됩니다")
        originals[key] = item
    data = json.loads(bundles.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("bundles"), list):
        raise ValueError("추출 묶음 형식 오류")
    data = deepcopy(data)
    definitions = load_groups(groups)
    grouped_members = {key for group in definitions for key in group.members}
    kinds = {}
    for item in analyzed.get("notices", []):
        key = (item["source_id"], item["external_id"])
        if key in kinds:
            raise ValueError("규칙 분석의 공지 식별자가 중복됩니다")
        kinds[key] = item["classification"]["notice_kind"]["value"]
    for bundle in data["bundles"]:
        refs = bundle.get("notices", [])
        original_refs = [originals.get((ref.get("source_id"), ref.get("external_id"))) for ref in refs]
        checks = check_extraction(bundle.get("extraction", {}), refs, original_refs)
        check = checks.get("apply")
        # 옛 공지가 늦게 재게시된 사례의 과거 마감도 이력 후보로는 허용한다.
        # 그 외 상식 오류·근거 불일치·미대조 이미지는 이력에 넣지 않는다.
        grounded = (check is not None and not check.evidence_failures and not check.unverifiable
                    and all(reason == "신청 마감이 첫 공지 게시일보다 이름" for reason in check.sanity_failures)
                    and deadline_context_is_grounded(bundle.get("extraction", {}).get("fields", {}).get("apply"), original_refs))
        fields = bundle.get("extraction", {}).get("fields", {})
        apply = fields.get("apply")
        if isinstance(apply, dict) and apply.get("flag") == "found" and not grounded:
            apply["flag"] = "needs_check"
        kind = fields.get("kind")
        key = (refs[0].get("source_id"), refs[0].get("external_id")) if len(refs) == 1 else None
        stage = kinds.get(key)
        if key in grouped_members:
            # 세 값은 공개 카드의 최상위에 있어 확인 필요 상태를 표시할 수 없다.
            # 실제 원문 대조가 안 되면 core에서 해당 그룹 전체를 보류한다.
            for name in ("title", "organizer", "scope"):
                field, field_check = fields.get(name), checks.get(name)
                if isinstance(field, dict) and field.get("flag") == "found" and (field_check is None or field_check.result != "pass"):
                    field["flag"] = "needs_check"
        meta = bundle.setdefault("meta", {})
        meta["merge_apply_verified"] = bool(grounded)
        meta["merge_kind_verified"] = (update_is_grounded(kind, stage, checks.get("kind"))
                                       and update_context_is_grounded(kind, original_refs))
        meta.pop("merge_terminal_verified", None)
    previous = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    blocked_ids = load_blocked(blocked)
    merged = merge_document(data, originals, analyzed, definitions, previous=previous, checked_at=now_text())
    blocked_groups = {group.id for group in definitions
                      if group.id in blocked_ids or any(card_id_of(*key) in blocked_ids for key in group.members)}
    suppressed = [bundle["card_id"] for bundle in merged["bundles"]
                  if bundle["card_id"] in blocked_ids or bundle["card_id"] in blocked_groups]
    merged["bundles"] = [bundle for bundle in merged["bundles"] if bundle["card_id"] not in suppressed]
    merged["merge_report"].update(blocked=len(suppressed), blocked_ids=suppressed)
    write_json(out, merged)  # 상태와 묶음을 하나의 원자적 교체로 보존한다.
    return merged.get("merge_report", {})


def main(argv=None, *, lock_held=False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 검토된 공지 병합과 마감 변경 이력")
    parser.add_argument("--bundles", type=Path, default=ROOT / "data/card_bundles.json")
    parser.add_argument("--notices", type=Path, default=ROOT / "data/notices.json")
    parser.add_argument("--analysis", type=Path, default=ROOT / "data/analysis.json")
    parser.add_argument("--groups", type=Path, default=ROOT / "config/contest_groups.toml")
    parser.add_argument("--out", type=Path, default=ROOT / "data/card_bundles.merged.json")
    parser.add_argument("--blocked", type=Path, default=ROOT / "config/blocked_cards.toml")
    args = parser.parse_args(argv)
    if not lock_held:
        if args.out.resolve().is_relative_to(SITE.resolve()) or args.notices.resolve().is_relative_to(SITE.resolve()):
            print("[멈춤] 공개 폴더의 병합 입출력은 허용하지 않습니다", file=sys.stderr)
            return 2
        try:
            with CollectorLock(args.notices.parent):
                return main(argv, lock_held=True)
        except AlreadyRunning:
            print("[멈춤] 다른 수집/분석/병합 작업이 실행 중입니다", file=sys.stderr)
            return 2
    try:
        report = merge_files(args.bundles, args.notices, args.analysis, args.groups, args.out, args.blocked)
    except Exception as exc:
        print(f"[멈춤] 병합 실패 ({type(exc).__name__}), 기존 파일 유지", file=sys.stderr)
        return 2
    print("[병합] " + json.dumps(report, ensure_ascii=False), file=sys.stderr)
    return 1 if report.get("withheld", 0) or report.get("history_review", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
