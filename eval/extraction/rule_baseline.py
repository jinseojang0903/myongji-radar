"""규칙 분석기 기준선 (#9): 수집기 `analysis.json`을 채점기가 읽는 실행 폴더로 바꾼다.

"AI 없이 규칙만 쓰면 몇 점인가"를 AI 실행과 같은 채점기·같은 표로 재기 위한 것이다.
규칙 분석기는 참가 조건을 단어 언급만 찾고 허용·제외를 정하지 않으므로(사람 검수 전제), 그 결과를 그대로 옮긴다.
없는 능력을 더해 주지 않는다: 규칙이 정하지 않은 값은 확인 필요(needs_check), 찾지 못한 값은 missing.

출력은 채점용 부분 형식이다: v0.2 모양을 따르지만 규칙이 다루는 항목(SCORED_ITEMS)만 있고, 근거 인용은 공백을 정리해
원문과 글자 단위로 같지 않다. 스키마 검증·내보내기 입력으로 쓰지 않는다.

    python eval/extraction/rule_baseline.py pipeline/data/analysis.json eval/extraction/runs/<날짜>_rules

입력: analysis.json (pipeline/collector/analysis.py), 정답표 sources.csv (대회 ↔ 공지 URL)·answers.csv (출처 없는 대회 경고용).
출력: <실행 폴더>/meta.json, outputs/<contest_id>.json. 실행 폴더는 저장소에 올리지 않는다 (공지 인용문이 들어 있다).
실패: 입력을 읽지 못하거나 실행 폴더가 다른 실행의 것이면 종료 코드 2.
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import DEFAULT_ANSWERS, EXAMPLE_ID, read_answer_key  # noqa: E402

RUN_TYPE = "rule_baseline"
# 규칙 분석기가 다루는 항목만 채점한다. 다루지 않는 항목(문의처·혜택 등)을 missing으로 내면
# 정답도 missing인 칸에서 공짜로 정답을 얻어 기준선이 부풀기 때문이다.
# stages는 뺀다: 규칙은 신청·제출·행사 기간만 찾고 일정표를 읽지 못해(STOP에서 끊음) 단계 목록이 완전한지 알 수 없다.
SCORED_ITEMS = ["kind", "apply", "campus", "grades", "enrollment", "team"]
CONTEST_TYPES = {"competition", "hackathon"}  # rules.toml activity_types 중 대회에 해당하는 것
# result_delayed(결과 발표 연기)·unknown은 정하지 않는다 (확인 필요)
NOTICE_KINDS = {"recruitment": "모집", "extension": "연장", "change": "변경", "cancellation": "취소", "results": "결과"}
UPDATE_KINDS = {"extension", "change"}  # system.md 4절 (가): 나중 공지가 바꾼다고 밝힌 값은 갱신
CONDITION_FIELDS = {"campus": ["eligible_campuses"], "grades": ["grades"], "enrollment": ["student_status"],
                    "team": ["team_size", "participation_mode"]}
IMAGE_CODES = {"image_only", "images_present"}
LOCATIONS = {"title": "제목", "body": "본문"}
MAX_QUOTE = 200


def read_sources(folder: Path) -> dict[str, list[tuple[int, str]]]:
    """sources.csv → {contest_id: [(공지 번호, URL)]} (공지 번호 순)."""
    sources = defaultdict(list)
    with open(folder / "sources.csv", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            contest_id = (row.get("contest_id") or "").strip()
            if not contest_id or contest_id == EXAMPLE_ID:
                continue
            sources[contest_id].append((int(row["notice"]), (row.get("url") or "").strip()))
    return {contest_id: sorted(notices) for contest_id, notices in sources.items()}


def build_output(notices: list[tuple[int, dict]]) -> dict:
    """한 대회의 공지별 규칙 분석 결과 [(공지 번호, 분석)] (번호 순) → 채점용 출력."""
    types = {kind for _, notice in notices for kind in notice["classification"]["types"]["value"] or []}
    if not types & CONTEST_TYPES:
        return {"is_contest": {"value": False, "reason": "규칙 분석기 유형에 대회·해커톤 없음"}, "warnings": []}
    has_images = any(item["code"] in IMAGE_CODES for _, notice in notices for item in notice["notices"])
    fields = {"kind": _kind(notices), "apply": _period(notices, "application_period", has_images)}
    for name, sources in CONDITION_FIELDS.items():
        fields[name] = _condition(notices, sources, has_images)
    return {"is_contest": {"value": True, "reason": "규칙 분석기 유형: " + ", ".join(sorted(types))},
            "fields": fields, "warnings": []}


def _kind(notices):
    number, latest = notices[-1]  # system.md: 가장 늦게 게시된 공지의 성격
    kind = latest["classification"]["notice_kind"]
    value = NOTICE_KINDS.get(kind["value"])
    return _result(value, "found" if value else "needs_check", _evidence([(number, kind)]),
                   None if value else f"규칙 분류 {kind['value']}는 공지 성격으로 정하지 않음")


def _period(notices, name, has_images):
    """공지들의 같은 종류 기간 → 하나의 값.

    - 가장 늦게 기간을 적은 공지가 연장·변경 공지이고 규칙이 확정했으면 그 값 (system.md 4절 (가), 이전 값은 alternatives)
    - 규칙이 확정하지 못한 공지(연도 없음·복수 일정 등)가 하나라도 있으면 확인 필요 (그 공지가 값을 바꿨을 수 있다)
    - 확정한 값이 하나로 모이면 found, 서로 다르면 conflict (4절 (나))
    """
    stated = [(number, notice, notice["fields"][name]) for number, notice in notices if notice["fields"][name]["status"] != "not_stated"]
    if not stated:
        if has_images:
            return _result(None, "needs_check", [], "본문에서 못 찾음: 이미지에 있을 수 있음")
        return _result(None, "missing", [])
    evidence = _evidence([(number, rule_field) for number, _, rule_field in stated])
    settled = {number: _period_value(rule_field) for number, _, rule_field in stated if _is_settled(rule_field)}
    last_number, last_notice, _ = stated[-1]
    if last_notice["classification"]["notice_kind"]["value"] in UPDATE_KINDS and last_number in settled:
        alternatives = [{"text": _display(value), "notice": number} for number, value in settled.items()
                        if number != last_number and value != settled[last_number]]
        return _result(settled[last_number], "found", _evidence([(last_number, stated[-1][2])]),
                       "연장·변경 공지의 값", alternatives)
    if len(settled) < len(stated):
        return _result(None, "needs_check", evidence, "규칙이 기간 역할·연도를 정하지 못한 공지가 있음")
    values = {json.dumps(value, sort_keys=True) for value in settled.values()}
    if len(values) == 1:
        return _result(next(iter(settled.values())), "found", evidence)
    return _result(None, "conflict", evidence, "공지마다 규칙이 찾은 기간이 다름")


def _is_settled(rule_field) -> bool:
    value = rule_field["value"] or {}
    return rule_field["status"] == "extracted" and "candidates" not in value and bool(value.get("end"))


def _period_value(rule_field):
    return {"start": _moment(rule_field["value"].get("start")), "end": _moment(rule_field["value"]["end"])}


def _display(value):
    part = value["end"]
    return f"{part['date']} {part['time']}" if part["time"] else part["date"]


def _condition(notices, names, has_images):
    """규칙은 조건 단어 언급만 찾고 허용·제외를 정하지 않는다 (needs_review) → 값 없이 확인 필요."""
    rule_fields = [(number, notice["fields"][name]) for number, notice in notices for name in names]
    if all(rule_field["status"] == "not_stated" for _, rule_field in rule_fields):
        if has_images:
            return _result(None, "needs_check", [], "본문에서 못 찾음: 이미지에 있을 수 있음")
        return _result(None, "missing", [])
    mentions = sorted({mention for _, rule_field in rule_fields for mention in (rule_field["value"] or {}).get("mentions", [])})
    note = "규칙이 찾은 언급: " + ", ".join(mentions) if mentions else None
    return _result(None, "needs_check", _evidence(rule_fields), note)


def _moment(entry):
    if not entry or not entry.get("date"):
        return {"date": None, "time": None, "year_inferred": False}
    return {"date": entry["date"], "time": entry.get("time"), "year_inferred": False}


def _evidence(rule_fields):
    return [{"quote": " ".join(item["quote"].split())[:MAX_QUOTE], "notice": number,
             "location": LOCATIONS.get(item["location"], item["location"]), "file": None}
            for number, rule_field in rule_fields for item in rule_field.get("evidence", [])]


def _result(value, flag, evidence, note=None, alternatives=None):
    return {"value": value, "flag": flag, "evidence": evidence, "note": note, "alternatives": alternatives or []}


def _prepare(run_dir: Path) -> Path:
    """실행 폴더를 비운다. 옛 출력이 남으면 이번에 빠진 대회가 옛 값으로 채점되기 때문이다.
    이 변환기가 만든 폴더(meta.json run_type)나 빈 폴더만 비우고, 다른 실행(AI 출력)은 건드리지 않는다."""
    outputs = run_dir / "outputs"
    if run_dir.exists() and any(run_dir.iterdir()):
        meta_path = run_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except ValueError:
            meta = {}
        if not isinstance(meta, dict) or meta.get("run_type") != RUN_TYPE:
            raise ValueError(f"{run_dir}는 규칙 기준선 실행 폴더가 아니라 덮어쓰지 않음 (빈 폴더나 새 이름을 쓰기)")
        for path in outputs.glob("*.json"):
            path.unlink()
    outputs.mkdir(parents=True, exist_ok=True)
    return outputs


def convert(analysis_path: Path, run_dir: Path, answers: Path = DEFAULT_ANSWERS) -> list[str]:
    """실행 폴더를 만들고 경고 목록을 돌려준다 (정답표 공지를 analysis.json에서 못 찾음 등).

    Raises:
        OSError, ValueError, KeyError: 입력을 읽지 못하거나 실행 폴더가 다른 실행의 것일 때
    """
    raw = analysis_path.read_bytes()
    analysis = json.loads(raw.decode("utf-8"))
    sources = read_sources(answers)
    warnings = []
    url_counts = Counter(notice["source_url"].strip() for notice in analysis["notices"])
    # 같은 URL이 두 번 있으면 어느 쪽이 최신인지 analysis.json만으로 알 수 없다 → 그 공지를 쓰는 대회는 뺀다
    by_url = {notice["source_url"].strip(): notice for notice in analysis["notices"] if url_counts[notice["source_url"].strip()] == 1}
    outputs = _prepare(run_dir)
    for contest_id in sorted(read_answer_key(answers).contests.keys() - sources.keys()):
        warnings.append(f"{contest_id}: sources.csv에 공지가 없어 출력하지 않음")
    for contest_id, contest_sources in sources.items():
        duplicated = [url for _, url in contest_sources if url_counts[url] > 1]
        missing = [url for _, url in contest_sources if url_counts[url] == 0]
        if duplicated or missing:
            # 공지 일부만으로 만든 값은 규칙 성능이 아니라 수집 누락을 재게 되므로 출력하지 않는다 (출력 실패로 채점)
            reason = "; ".join(part for part in (
                f"analysis.json에 없는 공지 {len(missing)}개 ({', '.join(missing)})" if missing else "",
                f"analysis.json에 두 번 있는 공지 {len(duplicated)}개 ({', '.join(duplicated)})" if duplicated else "") if part)
            warnings.append(f"{contest_id}: {reason}라 출력하지 않음")
            continue
        output = build_output([(number, by_url[url]) for number, url in contest_sources])
        (outputs / f"{contest_id}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"model": "규칙 분석기", "prompt_version": analysis.get("rules_version", "?"), "run_type": RUN_TYPE,
            "scored_items": SCORED_ITEMS, "analysis_sha256": hashlib.sha256(raw).hexdigest(),
            "analyzed_at": analysis.get("analyzed_at"), "ran_at": datetime.now(timezone.utc).isoformat()}
    # meta.json은 마지막에 쓴다: 중간에 실패하면 새 meta가 없어 "완료된 실행"처럼 보이지 않는다
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="규칙 분석 결과를 채점용 실행 폴더로 바꾼다 (#9 기준선)")
    parser.add_argument("analysis", type=Path, help="수집기가 만든 analysis.json")
    parser.add_argument("run_dir", type=Path, help="만들 실행 폴더 (eval/extraction/runs/<이름>)")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="정답표 폴더 (sources.csv, answers.csv)")
    args = parser.parse_args(argv)
    try:
        warnings = convert(args.analysis, args.run_dir, args.answers)
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as error:
        print(f"[오류] 변환하지 못함 ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"[주의] {warning}", file=sys.stderr)
    print(f"실행 폴더: {args.run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
