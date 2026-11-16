"""매일 추출 (#14): 수집 원문(`data/notices.json`)과 규칙 분석(`data/analysis.json`)에서 대회 후보를 골라 AI로 뽑고,
검증기(#15)·내보내기(#17)가 읽는 묶음 파일(`data/card_bundles.json`)을 만든다.

    python -m extractor daily --model sonnet

- 후보: 규칙 분석이 활동 후보(candidate)로 보고 유형에 대회·해커톤이 있는 공지. 결과·취소 공지는 뺀다 (결과 공지는
  명단이 AI로 가지 않게 #14 완료 기준. 내보내기도 결과·취소 공지로는 카드를 만들지 않는다). 취소 공지는 사람이 원래
  모집 카드를 확인하도록 목록으로 알린다. 분석과 원문의 판이 다르거나 게시일이 없는 공지는 부르기 전에 뺀다.
- **공지 하나 = 카드 하나:** 같은 대회의 여러 공지를 묶는 일(#16) 전까지의 임시 방식 (#29의 대안).
  카드 id는 출처 id + 게시판 글 번호라 날마다 같다. 연장 공지는 원래 모집 카드와 별도 카드가 된다 (#16에서 묶는다).
- 같은 공지 판(version_id)을 같은 모델·프롬프트(지문)로 이미 뽑았으면 다시 부르지 않는다 (`data/extractions/<카드 id>.json`).
  새 글·바뀐 글만 부르고, 한 번에 `--max-new`(기본 30)개까지.
- **모델·프롬프트만 바꿨을 때는 다시 뽑는 동안 옛 결과로 카드를 남긴다** (원문이 같으므로).
  **공지가 바뀌면(새 판) 다시 뽑을 때까지 그 카드를 내린다:** 내보내기는 근거 대조에 실패한 항목의 값은 남기므로,
  옛 판의 값(지워진 연락처, 바뀌기 전 마감)이 "확인 필요"를 달고 계속 공개될 수 있기 때문이다.
- 같은 판·입력을 3번 실패한 공지는 더 부르지 않는다 (`_failures.json`. 매일 같은 공지가 호출을 먹지 않게). 판·입력이 바뀌면 다시.
- 묶음 파일에는 뽑아 둔 결과가 있는 후보만 넣는다. 사람 검수 기록(statuses)은 아직 없어 비워 둔다.
- AI 입력은 본문과 글자를 뽑아 둔 첨부다 (INPUTS). `--images`를 주면 내려받은 포스터 이미지도 넣는다 (extractor/images.py,
  기본은 끔). 입력 종류가 다른 옛 결과는 다시 뽑을 대상이다 (그동안은 옛 결과로 남긴다).
  새로 뽑을 공지가 상한보다 많으면 **카드가 없는 공지 먼저, 그다음 최신 공지부터** 부른다 (접수 중일 가능성이 큰
  공지가 먼저 갱신되고, 옛 결과로 공개 중인 카드가 상한을 먼저 쓰지 않게).
- 호출 조건은 모델 비교(eval)와 같다: 확인 호출, 격리 실측, 계정 환경변수 차단, 하루 상한 (`extractor/claude.py`).
  스키마 강제를 거치지 않은 모델 글은 받지 않는다 (공개 경로라서).

종료 코드: 0 처리함 (모델 실패·상한으로 남은 공지는 경고. 다음 실행이 다시), 2 멈춤 (시작하지 못함·확인 호출 실패·
격리 어긋남·같은 설정 문제 반복). 멈추면 묶음 파일을 덮어쓰지 않는다 (어제 묶음이 그대로 남는다).
"""
import json
import re
import sys
from pathlib import Path

from collector.asset_text import attachment_texts
from collector.config import load_sources
from collector.repository import Repository, content_hash
from extractor import claude as claude_cli
from extractor.common import (CallLedger, StopRun, add_usage, after_failure, new_totals, now_text, posted_day, probe,
                              write_json)
from extractor.images import INPUT_MARK as IMAGES_INPUT, image_refs, poster_images
from extractor.prompt import PROMPT_VERSION, InputNotice, build_input, fit_attachments, is_result_notice, prompt_fingerprint

CONTEST_TYPES = {"competition", "hackathon"}  # pipeline/config/rules.toml activity_types 중 대회
SKIPPED_NOTICE_KINDS = {"results", "cancellation"}
CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # 내보내기 CARD_ID_PATTERN과 같다
DEFAULT_MAX_NEW = 30
# AI에 넣는 것. 바뀌면 옛 결과는 다시 뽑을 대상. 넣는 방식(첨부 상한·이름·번호 가림)이 바뀌는 것은
# prompt_fingerprint가 prompt.py·asset_text.py·personal_numbers.py 내용으로 잡는다. 포스터 이미지(--images)는 넣을
# 이미지가 있는 공지에만 images.INPUT_MARK를 더 붙인다 (expected_inputs. 이미지 규칙이 바뀌면 그 표시의 번호를 올린다)
INPUTS = ["body", "attachments"]
MAX_FAILURES = 3
FAILURES_FILE = "_failures.json"
RUNS_FILE = "_runs.jsonl"


def card_id_of(source_id: str, external_id: str) -> str | None:
    """출처 id + 글 번호 → 카드 id (영문 소문자·숫자·-). 모양이 안 되면 None.
    수집기의 글 번호는 "ctl/792/235972"(게시판/메뉴/글) 모양이라 카드 id는 "mju-ctl-ctl-792-235972"가 된다."""
    raw = re.sub(r"[^a-z0-9]+", "-", f"{source_id}-{external_id}".lower()).strip("-")[:64].rstrip("-")
    return raw if CARD_ID.fullmatch(raw) else None


def select_candidates(analysis: dict, originals: dict[tuple[str, str], dict]) -> tuple[list[dict], list[str], list[str]]:
    """규칙 분석 → ([{card_id, notice(원문)}], 경고, 취소 공지 목록)."""
    candidates, warnings, cancellations, seen = [], [], [], {}
    for item in analysis.get("notices") or []:
        if not isinstance(item, dict):
            continue
        classification = item.get("classification") or {}
        activity = (classification.get("activity") or {}).get("value")
        types = set((classification.get("types") or {}).get("value") or [])
        kind = (classification.get("notice_kind") or {}).get("value")
        title = str(item.get("title") or "")
        key = (str(item.get("source_id") or ""), str(item.get("external_id") or ""))
        if activity != "candidate" or not types & CONTEST_TYPES:
            continue
        if kind == "cancellation":
            cancellations.append(f"{key[0]}/{key[1]} {item.get('source_url') or ''}")
            continue
        if kind in SKIPPED_NOTICE_KINDS or is_result_notice(title):
            continue
        original = originals.get(key)
        card_id = card_id_of(*key)
        if original is None:
            warnings.append(f"{key[0]}/{key[1]}: 수집 원문에 없어 뺌 (collector export를 다시)")
            continue
        if item.get("source_version_id") is not None and item.get("source_version_id") != original.get("version_id"):
            warnings.append(f"{key[0]}/{key[1]}: 규칙 분석과 원문의 판이 달라 뺌 (collector analyze를 다시)")
            continue
        if not posted_day(original.get("published_at"), ""):
            warnings.append(f"{key[0]}/{key[1]}: 게시일이 없어 뺌 (내보내기가 받지 않는다)")
            continue
        if card_id is None:
            warnings.append(f"{key[0]}/{key[1]}: 카드 id를 만들지 못해 뺌")
            continue
        if card_id in seen:
            warnings.append(f"{key[0]}/{key[1]}: 카드 id {card_id}가 {seen[card_id]}와 겹쳐 뺌")
            continue
        seen[card_id] = f"{key[0]}/{key[1]}"
        candidates.append({"card_id": card_id, "notice": original})
    return candidates, warnings, cancellations


def read_originals(path: Path) -> dict[tuple[str, str], dict]:
    """collector export → {(출처 id, 글 번호): 공지} (공지마다 최신 판 하나)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    notices = raw.get("notices") if isinstance(raw, dict) else None
    if not isinstance(notices, list):
        raise ValueError(f"{path} 최상위에 notices 배열이 없음")
    return {(str(notice.get("source_id") or ""), str(notice.get("external_id") or "")): notice
            for notice in notices if isinstance(notice, dict)}


def read_cache(cache_dir: Path, card_id: str, notice: dict) -> dict | None:
    """그 카드로 뽑아 둔 결과 (판·모델이 달라도). 다른 공지의 결과면(카드 id 충돌) 쓰지 않는다."""
    path = cache_dir / f"{card_id}.json"
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("output"), dict):
        return None
    if (entry.get("source_id"), entry.get("external_id")) != (notice.get("source_id"), notice.get("external_id")):
        return None
    return entry


def is_same_version(entry: dict | None, notice: dict) -> bool:
    """지금 원문과 같은 판(수집 원문 HTML이 같음)에서 뽑은 결과인지. 묶음에는 같은 판의 결과만 넣는다.

    판 번호만 본다. 원문이 바뀌면 수집기가 새 판(새 번호)을 만들고, 같은 번호에서 내용 해시만 바뀌는 것은 파서를
    고쳐 저장된 HTML을 다시 읽었을 때뿐이다(collector.repository.save). 그 경우 옛 결과로 카드를 남기고 다시 뽑는다
    (is_current가 해시까지 본다). 옛 결과의 근거도 새 본문으로 검증기 대조를 거친다 (대조에 실패한 항목은 근거를 싣지 않는다).
    """
    return (entry is not None and entry.get("version_id") is not None
            and entry.get("version_id") == notice.get("version_id"))


def expected_inputs(notice: dict, use_images: bool) -> list[str]:
    """이 공지를 지금 설정으로 뽑을 때의 입력 종류. 이미지 입력을 켰고 넣을 이미지가 있는 공지만 이미지 표시를 붙인다
    (켤 때 포스터가 있는 공지만 다시 뽑고, 끄면 이미지로 뽑은 결과를 이미지 없이 다시 뽑는다)."""
    if use_images and image_refs(notice) and not is_result_notice(str(notice.get("title") or "")):
        return INPUTS + [IMAGES_INPUT]
    return INPUTS


def is_current(entry: dict | None, notice: dict, model: str, fingerprint: str, use_images: bool = False) -> bool:
    """같은 판·같은 입력(내용 해시)을 같은 모델·같은 프롬프트로 뽑은 결과인지 (아니면 다시 부를 대상)."""
    return (is_same_version(entry, notice) and entry.get("input_hash") == content_hash(notice) and entry.get("model") == model
            and entry.get("prompt_version") == PROMPT_VERSION and entry.get("prompt_fingerprint") == fingerprint
            and entry.get("inputs") == expected_inputs(notice, use_images))


def bundle_of(card_id: str, notice: dict, entry: dict, cross: dict | None = None) -> dict:
    """뽑은 결과 → 내보내기 묶음 하나 (exporter.cards.CardBundle 모양).

    cross: 같은 판·같은 입력을 교차 검증 모델로 뽑은 결과 (#15). 검증기가 값이 같은 핵심 항목을 자동 검증 통과로 올린다.
    내보내기는 cross_extraction을 공개하지 않는다 (CardBundle 키가 아님)."""
    # 이전 호출부의 최소 공지 입력도 받되, 완전한 수집 원문의 해시 계산은 바꾸지 않는다.
    hash_input = {"title": "", "body_text": "", "images": [], "attachments": [], **notice}
    return {"card_id": card_id,
            "notices": [{"source_id": notice.get("source_id"), "url": notice.get("url"), "title": notice.get("title"),
                         "published_at": notice.get("published_at"),
                         "last_seen_at": notice.get("last_seen_at"),
                         "external_id": notice.get("external_id"), "version_id": notice.get("version_id"),
                         "input_hash": content_hash(hash_input), "version_number": notice.get("version_number"),
                         # AI에 넣은 첨부·이미지 이름. 검증기·내보내기가 근거의 file 이름을 이 목록으로 확인한다
                         "files": [name for name in entry.get("files") or [] if isinstance(name, str)]}],
            "extraction": entry["output"],
            "cross_extraction": cross["output"] if cross else None,
            "statuses": {},  # 사람 검수 기록만 넣는다 (AI 출력에서 채우지 않는다)
            "deadline_history": [],  # 같은 대회 공지 묶기(#16) 뒤에
            "meta": {"prompt_version": entry.get("prompt_version"), "extract_model": entry.get("model_id") or entry.get("model"),
                     "merge_model": entry.get("model"), "merge_model_id": entry.get("model_id"),
                     "merge_prompt_fingerprint": entry.get("prompt_fingerprint"),
                     "cross_model": (cross.get("model_id") or cross.get("model")) if cross else None, "checked_at": None}}


def run_daily(args) -> int:
    if args.max_new < 1:
        raise ValueError("--max-new는 1 이상")
    args.cross_model = cross_model = getattr(args, "cross_model", None)  # 이 함수를 직접 부르는 쪽(시험)은 안 줄 수 있다
    args.images = bool(getattr(args, "images", False))
    if cross_model is not None and cross_model == args.model:
        raise ValueError("--cross-model은 --model과 다른 모델이어야 한다 (같은 모델끼리는 교차 검증이 아니다)")
    boards = {source.id: source.name for source in load_sources(args.config)}
    originals = read_originals(args.notices)
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    if not isinstance(analysis, dict):
        raise ValueError(f"{args.analysis} 형식이 다름")
    candidates, warnings, cancellations = select_candidates(analysis, originals)
    for warning in warnings:
        print(f"[뺌] {warning}", file=sys.stderr)
    for cancelled in cancellations:
        # 공지 하나 = 카드 하나라 취소가 원래 모집 카드에 반영되지 않는다 (#16 전). 사람이 확인한다
        print(f"[취소 공지] {cancelled}: 같은 대회의 모집 카드를 사람이 확인", file=sys.stderr)
    fingerprint = prompt_fingerprint()
    failures = _read_failures(args.cache)
    pending, given_up = [], []
    has_card = {}
    for item in candidates:
        notice = item["notice"]
        entry = read_cache(args.cache, item["card_id"], notice)
        if not args.force and is_current(entry, notice, args.model, fingerprint, args.images):
            continue
        if not args.force and _failure_count(failures, item["card_id"], notice, args.model, fingerprint) >= MAX_FAILURES:
            given_up.append(item["card_id"])
            continue
        has_card[item["card_id"]] = is_same_version(entry, notice)  # 옛 결과로라도 카드가 공개 중인지
        pending.append(item)
    # 카드가 없는 공지(새 글·새 판을 기다려 내린 카드)를 먼저, 그다음 최신 공지부터 (같으면 원래 순서).
    # 입력이 바뀌어 모두 다시 뽑을 때 옛 결과로 공개 중인 카드가 하루 상한을 먼저 쓰지 않게 한다
    pending.sort(key=lambda item: (not has_card[item["card_id"]], posted_day(item["notice"].get("published_at"), "")),
                 reverse=True)
    for card_id in given_up:
        print(f"[포기] {card_id}: 같은 판을 {MAX_FAILURES}번 실패해 부르지 않음 (공지·모델·프롬프트가 바뀌거나 --force면 다시)",
              file=sys.stderr)
    meta = {"model": args.model, "cross_model": cross_model, "prompt_version": PROMPT_VERSION, "prompt_fingerprint": fingerprint,
            "started_at": now_text(), "totals": new_totals(), "candidates": len(candidates), "to_extract": len(pending),
            "given_up": len(given_up), "excluded": len(warnings), "cancellations": len(cancellations)}
    cross_failures = _read_failures(_cross_dir(args)) if cross_model else {}
    if cross_model:
        meta["cross_totals"] = new_totals()  # 교차 호출의 토큰·비용·성공 수는 따로 센다 (월 비용 환산이 섞이지 않게)
    stopped, completed = None, False
    try:
        if pending or (cross_model and _cross_pending(args, candidates, fingerprint, cross_failures)):
            stopped = _call_pending(args, pending, candidates, boards, fingerprint, failures, cross_failures, meta)
        completed = True
    finally:
        _write_failures(args.cache, failures)
        if cross_model:
            _write_failures(_cross_dir(args), cross_failures)
        try:
            if completed and stopped is None:  # 멈추거나 예외로 끝나면 묶음을 덮어쓰지 않는다 (어제 묶음이 남는다)
                entries = [(item, read_cache(args.cache, item["card_id"], item["notice"])) for item in candidates]
                kept = [(item, entry) for item, entry in entries if is_same_version(entry, item["notice"])]
                for item, entry in kept:
                    _persist_entry(args.notices.parent, item["notice"], entry)
                meta["bundles"] = len(kept)
                meta["stale_bundles"] = sum(not is_current(entry, item["notice"], args.model, fingerprint, args.images) for item, entry in kept)
                meta["waiting_new_version"] = sum(entry is not None and not is_same_version(entry, item["notice"]) for item, entry in entries)
                crosses = [_current_cross(args, item, entry, fingerprint) for item, entry in kept]
                meta["cross_bundles"] = sum(cross is not None for cross in crosses)
                write_json(args.out, {"generated_at": now_text(),
                                      "bundles": [bundle_of(item["card_id"], item["notice"], entry, cross)
                                                  for (item, entry), cross in zip(kept, crosses)]})
        finally:
            _append_run_log(args.cache, {**meta, "stopped": str(stopped) if stopped else None})
    if stopped is not None:
        print(f"[멈춤] {stopped} — 묶음 파일은 그대로 둠 (어제 묶음)", file=sys.stderr)
        return 2
    totals = meta["totals"]
    if totals["calls_failed"] and not totals["calls"]:
        print(f"[경고] 이번에 부른 {totals['calls_failed']}건이 모두 실패함 (모델 출력 형식 확인: _failures.json)", file=sys.stderr)
    print(f"[끝] 후보 {len(candidates)}건, 이번에 뽑을 것 {len(pending)}건, 묶음 {meta['bundles']}건 "
          f"(모델·프롬프트가 달라 옛 결과로 넣은 것 {meta['stale_bundles']}건, 새 판을 기다려 내린 것 "
          f"{meta['waiting_new_version']}건) → {args.out}", file=sys.stderr)
    return 0


def _cross_dir(args) -> Path:
    return args.cache / "cross"


def _current_cross(args, item: dict, entry: dict, fingerprint: str) -> dict | None:
    """그 카드의 주 결과와 같은 판·같은 입력·같은 프롬프트로 교차 검증 모델이 뽑은 결과. 없으면 None.
    주 결과가 옛 입력·옛 프롬프트면 교차 결과와 비교할 수 없으므로 쓰지 않는다."""
    if not args.cross_model or not is_current(entry, item["notice"], args.model, fingerprint, args.images):
        return None
    cross = read_cache(_cross_dir(args), item["card_id"], item["notice"])
    return cross if is_current(cross, item["notice"], args.cross_model, fingerprint, args.images) else None


def _cross_pending(args, candidates: list[dict], fingerprint: str, cross_failures: dict) -> list[dict]:
    """주 결과는 지금 것인데 교차 결과가 없거나 낡은 카드 (최신 공지부터). 같은 판을 교차 모델로 MAX_FAILURES번
    실패한 카드는 뺀다 (계속 실패하는 카드가 매일 맨 앞에서 상한을 먹지 않게)."""
    items = []
    for item in candidates:
        entry = read_cache(args.cache, item["card_id"], item["notice"])
        if not is_current(entry, item["notice"], args.model, fingerprint, args.images) or _current_cross(args, item, entry, fingerprint):
            continue
        if _failure_count(cross_failures, item["card_id"], item["notice"], args.cross_model, fingerprint) >= MAX_FAILURES:
            continue
        items.append(item)
    return sorted(items, key=lambda item: posted_day(item["notice"].get("published_at"), ""), reverse=True)


def _call_pending(args, pending, candidates, boards, fingerprint, failures, cross_failures, meta) -> StopRun | None:
    """남은 공지를 부른다 (주 모델 먼저, 그다음 교차 검증 모델). 멈춰야 하면 StopRun을 돌려준다
    (예외로 올리지 않고 기록을 마저 쓰게)."""
    in_use = claude_cli.account_env_in_use()
    if in_use and not args.use_env_account:
        raise ValueError(f"환경변수 {', '.join(in_use)}가 있어 구독 로그인 대신 그 계정으로 갈 수 있음. "
                         "지우고 다시 돌리거나, 본인 API 키가 맞으면 --use-env-account")
    claude = claude_cli.resolve_claude(args.claude)
    claude_cli.build_command(claude, args.model)
    cross_model = args.cross_model
    if cross_model:
        claude_cli.build_command(claude, cross_model)
    ledger = CallLedger(args.ledger, args.max_calls)
    if ledger.remaining() <= 0:
        waiting = f"{len(pending)}건" + (" (교차 검증 포함)" if cross_model else "")
        print(f"[남음] 오늘 호출 상한 {ledger.limit}번에 이미 닿아 {waiting}은 다음 실행에서", file=sys.stderr)
        return None
    try:
        if pending:
            probe(claude, args.model, args.timeout, args.use_env_account, ledger, meta, with_image=args.images)
            _extract_pending(args, pending[:args.max_new], claude, ledger, meta, boards, fingerprint, failures)
            left = max(0, len(pending) - args.max_new)
            if left:
                print(f"[남음] {left}건은 --max-new {args.max_new}를 넘어 다음 실행에서", file=sys.stderr)
    except StopRun as stop:
        return stop
    # 교차 검증은 주 결과가 지금 것인 카드만 (방금 뽑은 것 포함). 주 모델 다음이라 상한에 걸리면 이쪽이 남는다.
    # --max-new는 주 모델과 교차 모델에 따로 적용된다 (둘을 합친 호출은 하루 상한 --max-calls가 막는다)
    cross_items = _cross_pending(args, candidates, fingerprint, cross_failures) if cross_model else []
    if cross_items and ledger.remaining() > 0:
        meta["cross_to_extract"] = len(cross_items)
        cross_meta = {"totals": meta["cross_totals"]}  # 사용량을 교차 합계에 센다
        try:
            probe(claude, cross_model, args.timeout, args.use_env_account, ledger, cross_meta, with_image=args.images)
            _extract_pending(args, cross_items[:args.max_new], claude, ledger, cross_meta, boards, fingerprint, cross_failures,
                             model=cross_model, cache=_cross_dir(args))
        except StopRun as stop:
            # 교차 검증은 덧붙이는 단계다. 멈춰도 주 결과의 공개는 막지 않는다 (교차 결과가 없는 카드는 "AI 정리"로 남는다)
            meta["cross_stopped"] = str(stop)
            print(f"[교차 검증 멈춤] {stop} — 주 결과로 묶음은 만든다", file=sys.stderr)
    elif cross_items:
        print(f"[남음] 교차 검증 {len(cross_items)}건은 오늘 호출 상한에 닿아 다음 실행에서", file=sys.stderr)
    return None


def _extract_pending(args, pending, claude, ledger, meta, boards, fingerprint, failures, model=None, cache=None) -> None:
    """공지마다 한 번씩 부른다. model·cache를 주면 교차 검증 호출이다: 실패는 교차 쪽 기록(cross/_failures.json)에
    남기고(같은 판을 MAX_FAILURES번 실패하면 더 부르지 않음), DB에는 보관하지 않는다 (공개 값은 주 결과다)."""
    model, cache = model or args.model, cache or args.cache
    is_cross = model != args.model
    last_errors = []
    for item in pending:
        card_id, notice = item["card_id"], item["notice"]
        if ledger.remaining() <= 0:
            print(f"[남음] 오늘 호출 상한 {ledger.limit}번에 닿음 (다음 실행이 남은 공지를 부른다)", file=sys.stderr)
            return
        source_id = str(notice.get("source_id") or "")
        attachments = attachment_texts(notice, args.notices.parent)  # 검증기도 같은 함수로 읽어 대조한다
        inputs = expected_inputs(notice, args.images)
        images = poster_images(notice, args.notices.parent) if IMAGES_INPUT in inputs else []
        text, _ = build_input([InputNotice(board=boards.get(source_id, source_id),
                                           posted=posted_day(notice.get("published_at"), ""),
                                           url=str(notice.get("url") or ""), title=str(notice.get("title") or ""),
                                           body=str(notice.get("body_text") or ""), attachments=attachments,
                                           images=[image.name for image in images])])
        ledger.add()
        try:
            result = claude_cli.call(text, claude, model, args.timeout, args.use_env_account, allow_text_result=False,
                                     images=images)
        except claude_cli.ClaudeError as error:
            add_usage(meta, error.usage, error.cost_usd, [], succeeded=False)
            if error.kind == "model" and failures is not None:
                failures[card_id] = {"version_id": notice.get("version_id"), "model": model, "prompt_fingerprint": fingerprint,
                                     "input_hash": content_hash(notice),
                                     "count": _failure_count(failures, card_id, notice, model, fingerprint) + 1,
                                     "last_error": str(error)[:120]}
            print(f"[실패{' 교차' if is_cross else ''}] {card_id}: {error}", file=sys.stderr)
            last_errors = after_failure(error, last_errors)
            continue
        last_errors = []
        if failures is not None:
            failures.pop(card_id, None)
        add_usage(meta, result.usage, result.cost_usd, result.models, succeeded=True)
        entry = {"source_id": notice.get("source_id"), "external_id": notice.get("external_id"),
                    "version_id": notice.get("version_id"), "model": model,
                    "model_id": result.models[0] if result.models else None, "prompt_version": PROMPT_VERSION,
                    # 입력 종류는 기대한 것으로 남긴다 (파일이 바뀌어 이미지를 못 넣었어도 매일 다시 부르지 않게. 넣은 것은 files)
                    "prompt_fingerprint": fingerprint, "input_hash": content_hash(notice), "inputs": inputs,
                    # 실제로 AI에 넣은 첨부·이미지 이름만 (명단 이름 첨부·합계 상한으로 빠진 것은 근거가 될 수 없다)
                    "files": [name for name, _, _ in fit_attachments(attachments)] + [image.name for image in images],
                    "extracted_at": now_text(), "output": result.output}
        if not is_cross:
            _persist_entry(args.notices.parent, notice, entry)
        write_json(cache / f"{card_id}.json", entry)
        print(f"[뽑음{' 교차' if is_cross else ''}] {card_id} ({result.seconds:.0f}초)", file=sys.stderr)


def _persist_entry(data_dir: Path, notice: dict, entry: dict) -> None:
    """운영 DB가 있으면 성공한 분석을 캐시 덮어쓰기 전에 보관한다. JSON 전용 시험은 DB를 만들지 않는다."""
    if not (data_dir / "collector.sqlite3").exists():
        return
    repo = Repository(data_dir)
    try:
        repo.save_analyses([{"notice": notice, "generator_kind": "ai", "generator_version": entry["prompt_version"],
                             "model": entry.get("model"), "model_id": entry.get("model_id"),
                             "prompt_version": entry["prompt_version"], "prompt_fingerprint": entry["prompt_fingerprint"],
                             "result": entry["output"], "created_at": entry.get("extracted_at")}])
    finally:
        repo.close()


def _failure_count(failures: dict, card_id: str, notice: dict, model: str, fingerprint: str) -> int:
    """같은 판·입력·모델·프롬프트로 실패한 횟수 (어느 하나라도 바뀌면 0부터)."""
    record = failures.get(card_id)
    if not isinstance(record, dict):
        return 0
    same = (record.get("version_id") == notice.get("version_id") and record.get("model") == model
            and record.get("prompt_fingerprint") == fingerprint and record.get("input_hash") == content_hash(notice))
    count = record.get("count")
    return count if same and isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0


def _read_failures(cache: Path) -> dict:
    """실패 기록. 깨졌거나 모양이 다른 항목은 버린다 (다시 시도할 뿐이고 하루 상한이 막는다)."""
    path = cache / FAILURES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}


def _write_failures(cache: Path, failures: dict) -> None:
    write_json(cache / FAILURES_FILE, failures)


def _append_run_log(cache: Path, meta: dict) -> None:
    """실행마다 한 줄 (토큰·비용·건수만, 공지 내용 없음). 월 비용 환산과 실패 추적에 쓴다."""
    cache.mkdir(parents=True, exist_ok=True)
    with open(cache / RUNS_FILE, "a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps({**meta, "finished_at": now_text()}, ensure_ascii=False) + "\n")
