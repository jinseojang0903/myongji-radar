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
- 호출 조건은 모델 비교(eval)와 같다: 확인 호출, 격리 실측, 계정 환경변수 차단, 하루 상한 (`extractor/claude.py`).
  스키마 강제를 거치지 않은 모델 글은 받지 않는다 (공개 경로라서).

종료 코드: 0 처리함 (모델 실패·상한으로 남은 공지는 경고. 다음 실행이 다시), 2 멈춤 (시작하지 못함·확인 호출 실패·
격리 어긋남·같은 설정 문제 반복). 멈추면 묶음 파일을 덮어쓰지 않는다 (어제 묶음이 그대로 남는다).
"""
import json
import re
import sys
from pathlib import Path

from collector.config import load_sources
from collector.repository import Repository, content_hash
from extractor import claude as claude_cli
from extractor.common import (CallLedger, StopRun, add_usage, after_failure, new_totals, now_text, posted_day, probe,
                              write_json)
from extractor.prompt import PROMPT_VERSION, InputNotice, build_input, is_result_notice, prompt_fingerprint

CONTEST_TYPES = {"competition", "hackathon"}  # pipeline/config/rules.toml activity_types 중 대회
SKIPPED_NOTICE_KINDS = {"results", "cancellation"}
CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # 내보내기 CARD_ID_PATTERN과 같다
DEFAULT_MAX_NEW = 30
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
    """지금 원문과 같은 판에서 뽑은 결과인지 (모델·프롬프트는 달라도). 묶음에는 같은 판의 결과만 넣는다."""
    return (entry is not None and entry.get("version_id") is not None
            and entry.get("version_id") == notice.get("version_id") and entry.get("input_hash") == content_hash(notice))


def is_current(entry: dict | None, notice: dict, model: str, fingerprint: str) -> bool:
    """같은 판을 같은 모델·같은 프롬프트로 뽑은 결과인지 (아니면 다시 부를 대상)."""
    return (is_same_version(entry, notice) and entry.get("model") == model
            and entry.get("prompt_version") == PROMPT_VERSION and entry.get("prompt_fingerprint") == fingerprint)


def bundle_of(card_id: str, notice: dict, entry: dict) -> dict:
    """뽑은 결과 → 내보내기 묶음 하나 (exporter.cards.CardBundle 모양)."""
    return {"card_id": card_id,
            "notices": [{"source_id": notice.get("source_id"), "url": notice.get("url"), "title": notice.get("title"),
                         "published_at": notice.get("published_at"),
                         "last_seen_at": notice.get("last_seen_at"),
                         "external_id": notice.get("external_id"), "version_id": notice.get("version_id"),
                         "input_hash": content_hash(notice), "version_number": notice.get("version_number"),
                         "files": []}],  # 이미지·첨부를 AI에 넣지 않으므로(#12 전) 근거 파일 이름도 없다
            "extraction": entry["output"],
            "statuses": {},  # 사람 검수 기록만 넣는다 (AI 출력에서 채우지 않는다)
            "deadline_history": [],  # 같은 대회 공지 묶기(#16) 뒤에
            "meta": {"prompt_version": entry.get("prompt_version"), "extract_model": entry.get("model_id") or entry.get("model"),
                     "merge_model": entry.get("model"), "merge_model_id": entry.get("model_id"),
                     "merge_prompt_fingerprint": entry.get("prompt_fingerprint"),
                     "cross_model": None, "checked_at": None}}


def run_daily(args) -> int:
    if args.max_new < 1:
        raise ValueError("--max-new는 1 이상")
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
    for item in candidates:
        notice = item["notice"]
        if not args.force and is_current(read_cache(args.cache, item["card_id"], notice), notice, args.model, fingerprint):
            continue
        if not args.force and _failure_count(failures, item["card_id"], notice, args.model, fingerprint) >= MAX_FAILURES:
            given_up.append(item["card_id"])
            continue
        pending.append(item)
    for card_id in given_up:
        print(f"[포기] {card_id}: 같은 판을 {MAX_FAILURES}번 실패해 부르지 않음 (공지·모델·프롬프트가 바뀌거나 --force면 다시)",
              file=sys.stderr)
    meta = {"model": args.model, "prompt_version": PROMPT_VERSION, "prompt_fingerprint": fingerprint, "started_at": now_text(),
            "totals": new_totals(), "candidates": len(candidates), "to_extract": len(pending), "given_up": len(given_up),
            "excluded": len(warnings), "cancellations": len(cancellations)}
    stopped, completed = None, False
    try:
        if pending:
            stopped = _call_pending(args, pending, boards, fingerprint, failures, meta)
        completed = True
    finally:
        _write_failures(args.cache, failures)
        try:
            if completed and stopped is None:  # 멈추거나 예외로 끝나면 묶음을 덮어쓰지 않는다 (어제 묶음이 남는다)
                entries = [(item, read_cache(args.cache, item["card_id"], item["notice"])) for item in candidates]
                kept = [(item, entry) for item, entry in entries if is_same_version(entry, item["notice"])]
                for item, entry in kept:
                    _persist_entry(args.notices.parent, item["notice"], entry)
                meta["bundles"] = len(kept)
                meta["stale_bundles"] = sum(not is_current(entry, item["notice"], args.model, fingerprint) for item, entry in kept)
                meta["waiting_new_version"] = sum(entry is not None and not is_same_version(entry, item["notice"]) for item, entry in entries)
                write_json(args.out, {"generated_at": now_text(), "bundles": [bundle_of(item["card_id"], item["notice"], entry)
                                                                             for item, entry in kept]})
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


def _call_pending(args, pending, boards, fingerprint, failures, meta) -> StopRun | None:
    """남은 공지를 부른다. 멈춰야 하면 StopRun을 돌려준다 (예외로 올리지 않고 기록을 마저 쓰게)."""
    in_use = claude_cli.account_env_in_use()
    if in_use and not args.use_env_account:
        raise ValueError(f"환경변수 {', '.join(in_use)}가 있어 구독 로그인 대신 그 계정으로 갈 수 있음. "
                         "지우고 다시 돌리거나, 본인 API 키가 맞으면 --use-env-account")
    claude = claude_cli.resolve_claude(args.claude)
    claude_cli.build_command(claude, args.model)
    ledger = CallLedger(args.ledger, args.max_calls)
    if ledger.remaining() <= 0:
        print(f"[남음] 오늘 호출 상한 {ledger.limit}번에 이미 닿아 {len(pending)}건은 다음 실행에서", file=sys.stderr)
        return None
    try:
        probe(claude, args.model, args.timeout, args.use_env_account, ledger, meta)
        _extract_pending(args, pending[:args.max_new], claude, ledger, meta, boards, fingerprint, failures)
    except StopRun as stop:
        return stop
    left = max(0, len(pending) - args.max_new)
    if left:
        print(f"[남음] {left}건은 --max-new {args.max_new}를 넘어 다음 실행에서", file=sys.stderr)
    return None


def _extract_pending(args, pending, claude, ledger, meta, boards, fingerprint, failures) -> None:
    last_errors = []
    for item in pending:
        card_id, notice = item["card_id"], item["notice"]
        if ledger.remaining() <= 0:
            print(f"[남음] 오늘 호출 상한 {ledger.limit}번에 닿음 (다음 실행이 남은 공지를 부른다)", file=sys.stderr)
            return
        source_id = str(notice.get("source_id") or "")
        text, _ = build_input([InputNotice(board=boards.get(source_id, source_id),
                                           posted=posted_day(notice.get("published_at"), ""),
                                           url=str(notice.get("url") or ""), title=str(notice.get("title") or ""),
                                           body=str(notice.get("body_text") or ""))])
        ledger.add()
        try:
            result = claude_cli.call(text, claude, args.model, args.timeout, args.use_env_account, allow_text_result=False)
        except claude_cli.ClaudeError as error:
            add_usage(meta, error.usage, error.cost_usd, [], succeeded=False)
            if error.kind == "model":
                failures[card_id] = {"version_id": notice.get("version_id"), "model": args.model, "prompt_fingerprint": fingerprint,
                                     "input_hash": content_hash(notice),
                                     "count": _failure_count(failures, card_id, notice, args.model, fingerprint) + 1,
                                     "last_error": str(error)[:120]}
            print(f"[실패] {card_id}: {error}", file=sys.stderr)
            last_errors = after_failure(error, last_errors)
            continue
        last_errors = []
        failures.pop(card_id, None)
        add_usage(meta, result.usage, result.cost_usd, result.models, succeeded=True)
        entry = {"source_id": notice.get("source_id"), "external_id": notice.get("external_id"),
                    "version_id": notice.get("version_id"), "model": args.model,
                    "model_id": result.models[0] if result.models else None, "prompt_version": PROMPT_VERSION,
                    "prompt_fingerprint": fingerprint, "input_hash": content_hash(notice),
                    "extracted_at": now_text(), "output": result.output}
        _persist_entry(args.notices.parent, notice, entry)
        write_json(args.cache / f"{card_id}.json", entry)
        print(f"[뽑음] {card_id} ({result.seconds:.0f}초)", file=sys.stderr)


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
