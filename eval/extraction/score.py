"""AI 추출 결과(프롬프트 v0.2)를 정답표(eval/answer_key)로 채점한다 (#9).

    python eval/extraction/score.py eval/extraction/runs/<실행1> [<실행2> ...] --report eval/extraction/reports/<이름>.md

실행 폴더 (모델 하나 × 프롬프트 하나):
    meta.json            {"model": "...", "prompt_version": "v0.2", ...}  (pipeline/extractor가 쓴다)
    outputs/<contest_id>.json   AI 출력 그대로 (schema.json 형식). 없거나 깨지면 그 대회는 "출력 실패"
사람 판정 (뜻이 같으면 정답인 항목): eval/extraction/judgments/<실행 이름>.csv (run, contest_id, item, judgment=정답|오답).
    아직 판정하지 않은 줄은 실행 폴더의 judgments_todo.csv로 만들어 준다.

채점 규칙은 eval/answer_key/README.md "채점 방식"을 따른다. 숫자를 지어내지 않는다: 잴 대상이 없으면 "-"로 둔다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWERS = ROOT / "eval" / "answer_key"
JUDGMENTS_DIR = Path(__file__).resolve().parent / "judgments"
SEPARATOR = "‖"
EXAMPLE_ID = "example-id"

FIELDS = ["title", "organizer", "scope", "kind", "apply", "stages", "campus", "grades", "enrollment", "team",
          "deliverables", "benefits", "mileage", "how_to_apply", "contact"]
# 채점 단위. 한 항목에 자동 비교 부분과 사람 판정 부분이 함께 있는 것(제출물·마일리지)은 나눈다
AUTO_ITEMS = ["scope", "kind", "apply", "stages", "campus", "grades", "enrollment", "team",
              "deliverables.tags", "mileage.points", "contact.email"]
HUMAN_ITEMS = ["title", "organizer", "benefits", "deliverables.text", "mileage.text", "how_to_apply"]
ITEMS = AUTO_ITEMS + HUMAN_ITEMS
KEY_ITEMS = ["apply", "campus", "grades", "enrollment", "team"]  # 신청 가능 여부 판정에 쓰이는 항목
HOLD_FLAGS = {"needs_check", "conflict"}  # 둘 다 "확인 필요"로 본다
FLAGS = {"found", "missing"} | HOLD_FLAGS

CATEGORY_LABELS = {
    "correct": "정답",
    "wrong": "값 틀림",
    "fabricated": "지어냄",
    "overconfident": "과신",
    "missed": "놓침",
    "over_hold": "과잉 보류",
    "needs_human": "판정 전",
    "failed": "출력 실패",
}
ERROR_CATEGORIES = {"wrong", "fabricated", "overconfident", "missed", "failed"}
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")
ALL_CAMPUSES = frozenset({"인문", "자연"})
ALL_GRADES = frozenset({1, 2, 3, 4})


FORMAT_ERROR = "(AI 출력 형식 오류)"  # 값 없음과 구분한다 (정답이 missing일 때 "정답"으로 숨지 않게)


class AnswerFormatError(ValueError):
    """정답표 한 칸을 읽을 수 없음 (그 항목은 채점에서 빼고 보고한다)."""


# ---- 정답표 ----

@dataclass
class Truth:
    flag: str
    value: object  # 항목별 정규화한 값. 비어 있으면 None


@dataclass
class AnswerKey:
    contests: dict[str, dict[str, Truth]]  # contest_id → item → Truth
    problems: list[str] = field(default_factory=list)


def read_answer_key(folder: Path) -> AnswerKey:
    """answers.csv → 대회별·채점 단위별 정답. 형식이 틀린 칸은 빼고 problems에 남긴다."""
    rows: dict[str, dict[str, dict]] = {}
    problems = []
    with open(folder / "answers.csv", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            line_number = reader.line_num
            contest_id = (row.get("contest_id") or "").strip()
            name = (row.get("field") or "").strip()
            if not contest_id or contest_id == EXAMPLE_ID:
                continue
            if name not in FIELDS:
                problems.append(f"{line_number}줄: 모르는 항목 {name!r}")
                continue
            if name in rows.setdefault(contest_id, {}):
                problems.append(f"{line_number}줄: {contest_id} {name}이 두 번 나옴 (앞 줄을 씀)")
                continue
            rows[contest_id][name] = row | {"_line": line_number}
    contests = {}
    for contest_id, by_field in rows.items():
        missing_fields = [name for name in FIELDS if name not in by_field]
        if missing_fields:
            problems.append(f"{contest_id}: 항목 줄 없음 {', '.join(missing_fields)}")
        truths = {}
        for name, row in by_field.items():
            flag = (row.get("flag") or "").strip()
            if flag not in FLAGS:
                problems.append(f"{row['_line']}줄: {contest_id} {name}의 flag {flag!r}")
                continue
            text = (row.get("value") or "").strip()
            try:
                parsed = parse_answer(name, text) if text else {item: None for item in items_of(name)}
            except AnswerFormatError as error:
                problems.append(f"{row['_line']}줄: {contest_id} {name} 값 형식 ({error})")
                continue
            if flag == "found" and not text:
                problems.append(f"{row['_line']}줄: {contest_id} {name}이 found인데 값이 비어 있음")
                continue
            for item, value in parsed.items():
                # 항목은 found인데 이 칸만 빈 경우(문의처에 이메일이 없음 등)는 그 칸을 "공지에 없음"으로 본다
                truths[item] = Truth("missing" if flag == "found" and value is None else flag, value)
        contests[contest_id] = truths
    return AnswerKey(contests, problems)


def items_of(name: str) -> list[str]:
    return [item for item in ITEMS if item == name or item.startswith(name + ".")]


def parse_answer(name: str, text: str) -> dict[str, object]:
    """정답표 값 → {채점 단위: 정규화한 값}. 표기는 eval/answer_key/README.md "값 쓰는 법"."""
    parts = [part.strip() for part in text.split(SEPARATOR)]
    if name in ("title", "organizer", "benefits", "scope", "kind"):
        return {name: _text(text)}
    if name == "apply":
        return {"apply": _range(text)}
    if name == "stages":
        stages = []
        for part in parts:
            stage_name, colon, rest = part.partition(":")
            if not colon or not stage_name.strip():
                raise AnswerFormatError(f"단계 {part!r}에 '이름:'이 없음")
            stages.append((stage_name.strip(), *_range(rest)))
        return {"stages": tuple(sorted(stages, key=_stage_key))}
    if name == "campus":
        return {"campus": _campus(text.split(","))}
    if name == "grades":
        try:
            grades = [int(item) for item in text.split(",")] if text != "any" else "any"
        except ValueError as error:
            raise AnswerFormatError(f"학년 {text!r}") from error
        return {"grades": _grades(grades)}
    if name == "enrollment":
        match = re.fullmatch(r"허용\s*:(.*)/\s*제외\s*:(.*)", text.replace("：", ":"), re.S)
        if not match:
            raise AnswerFormatError("'허용: … / 제외: …' 꼴이 아님")
        return {"enrollment": (_terms(match.group(1)), _terms(match.group(2)))}
    if name == "team":
        return {"team": _team(text)}
    if name == "deliverables":
        tags = frozenset(tag.strip() for tag in parts[0].split(",") if tag.strip())
        return {"deliverables.tags": tags or None, "deliverables.text": _text(parts[1]) if len(parts) > 1 else None}
    if name == "mileage":
        parts += [""] * (3 - len(parts))
        number = re.fullmatch(r"(\d+(?:\.\d+)?)\s*점?", parts[1])
        if parts[1] and not number:
            raise AnswerFormatError(f"마일리지 점수 {parts[1]!r} (숫자만)")
        points = float(number.group(1)) if number else None
        return {"mileage.points": points, "mileage.text": _text(" ‖ ".join(part for part in (parts[0], parts[2]) if part))}
    if name == "how_to_apply":
        return {"how_to_apply": _text(" ‖ ".join(part for part in parts if part))}
    if name == "contact":
        parts += [""] * (3 - len(parts))
        return {"contact.email": parts[1].lower() or None}
    raise AnswerFormatError(f"모르는 항목 {name}")


def _text(text: str | None) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text or None


def _datetime(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if not DATETIME.fullmatch(text):
        raise AnswerFormatError(f"날짜 {text!r}")
    return text


def _range(text: str) -> tuple[str | None, str | None]:
    start, tilde, end = text.partition("~")
    if not tilde:
        raise AnswerFormatError(f"기간 {text!r}에 '~'가 없음")
    return _datetime(start), _datetime(end)


def _stage_key(stage):
    name, start, end = stage
    return (end or start or "", start or "", name)


def _campus(items) -> object:
    items = frozenset(item.strip() for item in items if item and item.strip())
    if items == {"any"} or items == ALL_CAMPUSES:
        return "any"
    if not items <= ALL_CAMPUSES:
        raise AnswerFormatError(f"캠퍼스 {sorted(items)}")
    return items


def _grades(items) -> object:
    if items == "any":
        return "any"
    items = frozenset(items)
    return "any" if items >= ALL_GRADES else items  # README: 1~4학년 전체는 any와 같다


def _terms(text: str) -> frozenset:
    return frozenset(term.strip() for term in text.split(",") if term.strip())


def _team(text: str):
    """"~3, 개인 가능" → (min, max, 인원 제한 없음, 개인 참가 True/False/None)."""
    size, comma, individual = text.rpartition(",")
    if not comma:
        raise AnswerFormatError("'인원, 개인 가능/불가/모름' 꼴이 아님")
    individual = individual.strip()
    choices = {"개인 가능": True, "개인 불가": False, "개인 모름": None}
    if individual not in choices:
        raise AnswerFormatError(f"개인 참가 {individual!r}")
    size = size.strip()
    if size == "제한 없음":
        return (None, None, True, choices[individual])
    low, tilde, high = size.partition("~")
    try:
        if not tilde:
            number = int(low) if low.strip() else None
            return (number, number, False, choices[individual])
        return (int(low) if low.strip() else None, int(high) if high.strip() else None, False, choices[individual])
    except ValueError as error:
        raise AnswerFormatError(f"인원 {size!r}") from error


# ---- AI 출력 ----

@dataclass
class Predicted:
    flag: str | None
    value: object


def read_output(path: Path) -> tuple[dict[str, Predicted] | None, str | None]:
    """AI 출력 → (채점 단위별 (flag, 정규화한 값), 실패 이유). 실패면 (None, 이유) — 출력 실패로 채점한다.

    파일·JSON 문제만 여기서 잡는다. 값 형식 문제는 convert_output이 FORMAT_ERROR로 표시한다
    (채점기 버그가 "모델 실패" 숫자에 섞이지 않게 다른 예외는 그대로 올린다).
    """
    if not path.exists():
        return None, "출력 파일 없음"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, f"출력을 읽지 못함 ({type(error).__name__})"
    if not isinstance(raw, dict):
        return None, "출력 최상위가 객체가 아님"
    is_contest = raw.get("is_contest")
    if not isinstance(is_contest, dict) or is_contest.get("value") is not True:
        return None, "대회가 아니라고 판단 (is_contest)"
    if not isinstance(raw.get("fields"), dict):
        return None, "fields 없음"
    predicted = {}
    for name in FIELDS:
        raw_field = raw["fields"].get(name) if isinstance(raw["fields"].get(name), dict) else {}
        flag = raw_field.get("flag")
        for item, value in convert_output(name, raw_field.get("value"), raw_field.get("note")).items():
            predicted[item] = Predicted(flag, value)
    return predicted, None


def convert_output(name: str, value, note=None) -> dict[str, object]:
    """v0.2 값 → 정답표와 같은 정규화 값. 형식이 틀린 부분은 None(값 없음)으로 본다."""
    empty = {item: None for item in items_of(name)}
    if value is None:
        return empty
    try:
        if name in ("title", "organizer", "benefits", "scope", "kind"):
            return {name: _text(value) if isinstance(value, str) else FORMAT_ERROR}
        if name == "apply":
            return {"apply": (_moment(value.get("start")), _moment(value.get("end")))}
        if name == "stages":
            stages = [(_text(stage["name"]), _moment(stage.get("start")), _moment(stage.get("end"))) for stage in value]
            return {"stages": tuple(sorted(stages, key=_stage_key)) or None}
        if name == "campus":
            return {"campus": "any" if value.get("any") is True else _campus(value.get("allowed") or [])}
        if name == "grades":
            return {"grades": "any" if value.get("any") is True else _grades(value.get("allowed") or [])}
        if name == "enrollment":
            return {"enrollment": (frozenset(value.get("allowed") or []), frozenset(value.get("excluded") or []))}
        if name == "team":
            unlimited = value.get("min") is None and value.get("max") is None and "인원 제한 없음" in (note or "")
            return {"team": (value.get("min"), value.get("max"), unlimited, value.get("individual_ok"))}
        if name == "deliverables":
            tags = frozenset(value.get("tags") or [])
            return {"deliverables.tags": tags or None, "deliverables.text": _text(value.get("text"))}
        if name == "mileage":
            points = value.get("points")
            text = " ‖ ".join(part for part in (value.get("area"), value.get("text")) if part)
            return {"mileage.points": float(points) if isinstance(points, (int, float)) else None, "mileage.text": _text(text)}
        if name == "how_to_apply":
            return {"how_to_apply": _text(" ‖ ".join(part for part in (value.get("text"), value.get("url")) if part))}
        if name == "contact":
            email = value.get("email")
            return {"contact.email": email.strip().lower() if isinstance(email, str) and email.strip() else None}
    except (AttributeError, TypeError, KeyError, AnswerFormatError):
        return {item: FORMAT_ERROR for item in items_of(name)}
    return empty


def _moment(part) -> str | None:
    if not isinstance(part, dict) or not part.get("date"):
        return None
    return f"{part['date']} {part['time']}" if part.get("time") else part["date"]


# ---- 채점 ----

@dataclass
class ItemResult:
    contest_id: str
    item: str
    category: str
    truth: object
    predicted: object
    truth_flag: str
    predicted_flag: str | None
    is_risky: bool = False


def score_item(truth: Truth, predicted: Predicted | None, item: str, judgment=None) -> tuple[str, bool]:
    """(분류, 위험 오류 여부). 분류는 CATEGORY_LABELS의 키.

    - 정답 missing: AI가 값을 내면 지어냄.
    - 정답 확인 필요(needs_check·conflict): AI도 확인 필요면 정답, 값을 확정하면 과신, 비우면 놓침.
    - 정답 found: AI가 확인 필요로 두면 과잉 보류(오류로 세지 않고 비율로 보고), 비우면 놓침, 값 비교.
    위험 오류는 중요 항목에서 "불가인데 가능으로 만들 수 있는" 오류다 (지어낸 값도 위험으로 센다).
    """
    if predicted is None:
        return "failed", False
    has_value = predicted.value is not None and predicted.flag != "missing"
    if truth.flag == "missing":
        category = "fabricated" if has_value else "correct"
    elif truth.flag in HOLD_FLAGS:
        if predicted.flag in HOLD_FLAGS:
            category = "correct"
        else:
            category = "overconfident" if has_value else "missed"
    elif not has_value:
        category = "over_hold" if predicted.flag in HOLD_FLAGS else "missed"
    elif predicted.flag in HOLD_FLAGS:
        category = "over_hold"
    elif truth.value == predicted.value:
        category = "correct"
    elif predicted.value == FORMAT_ERROR:
        category = "wrong"  # 형식이 틀린 값은 사람에게 판정을 맡기지 않는다
    elif item in HUMAN_ITEMS:
        # judgment: (판정, 판정할 때의 AI 값). AI 값이 바뀌었으면 옛 판정을 쓰지 않는다
        verdict = judgment[0] if judgment and judgment[1] == display(predicted.value, limit=None) else None
        category = {"정답": "correct", "오답": "wrong"}.get(verdict or "", "needs_human")
    else:
        category = "wrong"
    is_risky = item in KEY_ITEMS and category in ("wrong", "fabricated", "overconfident") and (
        category == "fabricated" or truth.value is None or is_more_permissive(item, predicted.value, truth.value))
    return category, is_risky


def is_more_permissive(item: str, predicted, truth) -> bool:
    """AI 값이 정답보다 더 많은 학생을 '가능'으로 만들 수 있는지."""
    if item == "apply":
        if predicted == FORMAT_ERROR:
            return True
        if truth[1] is None:
            return False  # 정답에 마감이 없으면 AI의 마감은 더 엄격한 쪽
        if predicted[1] is None:
            return True  # 마감을 비우면 언제든 신청할 수 있어 보인다
        return _end_moment(predicted[1]) > _end_moment(truth[1])
    if predicted == FORMAT_ERROR:
        return True
    if item in ("campus", "grades"):
        if predicted == "any":
            return truth != "any"
        return truth != "any" and bool(predicted - truth)
    if item == "enrollment":
        predicted_allowed, predicted_excluded = predicted
        truth_allowed, truth_excluded = truth
        allows_more = bool(truth_allowed) and (not predicted_allowed or bool(predicted_allowed - truth_allowed))
        return allows_more or bool(truth_excluded - predicted_excluded)
    if item == "team":
        predicted_min, predicted_max, predicted_unlimited, predicted_individual = predicted
        truth_min, truth_max, truth_unlimited, truth_individual = truth
        if truth_individual is False and predicted_individual is True:
            return True
        if truth_max is not None and (predicted_max is None or predicted_max > truth_max):
            return True  # 상한을 없애거나(제한 없음 포함) 늘림
        return truth_min is not None and (predicted_min is None or predicted_min < truth_min)  # 하한을 없애거나 줄임
    return False


def _end_moment(text: str) -> str:
    """날짜만 있는 마감은 그날 끝(24:00)으로 본다 (시각이 있는 마감보다 늦다)."""
    return text if len(text) > 10 else text + " 24:00"


@dataclass
class RunScore:
    name: str
    meta: dict
    results: list[ItemResult]
    failed_contests: list[str]
    extra_outputs: list[str]
    warnings: list[str] = field(default_factory=list)


def score_run(run_dir: Path, answer_key: AnswerKey, judgments: dict | None = None) -> RunScore:
    warnings: list[str] = []
    meta = {}
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            warnings.append("meta.json을 읽지 못함 (모델 이름 없이 채점)")
    judgments = judgments if judgments is not None else read_judgments(run_dir.name, warnings=warnings)
    scored_items = _scored_items(meta, warnings)
    results, failed = [], []
    for contest_id, truths in sorted(answer_key.contests.items()):
        if not truths:
            continue  # 정답표 칸이 모두 깨진 대회 (problems로 보고됨)
        predicted, failure = read_output(run_dir / "outputs" / f"{contest_id}.json")
        if predicted is None:
            failed.append(contest_id)
            warnings.append(f"{contest_id}: 출력 실패 ({failure})")
        for item in scored_items:
            if item not in truths:
                continue
            guess = predicted.get(item) if predicted is not None else None
            if predicted is not None and guess is None:
                guess = Predicted(None, None)
            judgment = judgments.get((contest_id, item))
            category, is_risky = score_item(truths[item], guess, item, judgment)
            if judgment and category == "needs_human":
                warnings.append(f"{contest_id} {item}: 판정 뒤 AI 값이 바뀌어 옛 판정을 쓰지 않음")
            if guess is not None and guess.value == FORMAT_ERROR:
                warnings.append(f"{contest_id} {item}: AI 출력 형식 오류")
            results.append(ItemResult(contest_id, item, category, truths[item].value, guess.value if guess else None,
                                      truths[item].flag, guess.flag if guess else None, is_risky))
    outputs = run_dir / "outputs"
    extra = sorted(path.stem for path in outputs.glob("*.json") if path.stem not in answer_key.contests) if outputs.exists() else []
    return RunScore(run_dir.name, meta, results, failed, extra, warnings)


def _scored_items(meta: dict, warnings: list) -> list[str]:
    """meta.json의 scored_items(규칙 기준선처럼 일부 항목만 다루는 실행)가 있으면 그 항목만 채점한다."""
    requested = meta.get("scored_items")
    if requested is None:
        return ITEMS
    if not isinstance(requested, list):
        warnings.append("meta.json scored_items가 목록이 아니라 모든 항목을 채점")
        return ITEMS
    unknown = [item for item in requested if item not in ITEMS]
    if unknown:
        warnings.append(f"meta.json scored_items의 모르는 항목은 버림: {unknown}")
    scored = [item for item in ITEMS if item in requested]
    if not scored:
        warnings.append("meta.json scored_items에 채점할 항목이 없음")
    return scored


def read_judgments(run_name: str, folder: Path = JUDGMENTS_DIR, warnings: list | None = None) -> dict:
    """judgments/<실행>.csv → {(contest_id, item): (판정, 판정할 때의 AI 값)}. 정답·오답 밖의 값은 경고하고 버린다."""
    path = folder / f"{run_name}.csv"
    warnings = warnings if warnings is not None else []
    if not path.exists():
        return {}
    judgments = {}
    with open(path, encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            judgment = (row.get("judgment") or "").strip()
            if not judgment:
                continue
            if judgment not in ("정답", "오답"):
                warnings.append(f"판정 파일 {reader.line_num}줄: judgment {judgment!r}는 정답·오답이 아니라 버림")
                continue
            key = ((row.get("contest_id") or "").strip(), (row.get("item") or "").strip())
            if key in judgments:
                warnings.append(f"판정 파일 {reader.line_num}줄: {key[0]} {key[1]}이 두 번 나와 뒤 줄을 씀")
            judgments[((row.get("contest_id") or "").strip(), (row.get("item") or "").strip())] = (judgment, _uncell(row.get("ai") or ""))
    return judgments


def write_judgment_todo(score: RunScore, run_dir: Path) -> int:
    """사람이 판정할 줄(뜻이 같으면 정답인 항목)을 실행 폴더에 남긴다. 판정한 것은 judgments/<실행>.csv로 옮긴다."""
    todo = [result for result in score.results if result.category == "needs_human"]
    path = run_dir / "judgments_todo.csv"
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as file:
            if any((row.get("judgment") or "").strip() for row in csv.DictReader(file)):
                # 채운 판정을 지우지 않게 새 할 일은 다른 파일에 쓴다
                score.warnings.append(f"{path.name}에 채운 판정이 있어 새 할 일은 judgments_todo.new.csv에 씀 "
                                      f"(채운 것은 judgments/{score.name}.csv로 옮긴 뒤 지우기)")
                path = run_dir / "judgments_todo.new.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["run", "contest_id", "item", "answer", "ai", "judgment"])
        for result in todo:
            writer.writerow([score.name, result.contest_id, result.item, _cell(display(result.truth, limit=None)),
                             _cell(display(result.predicted, limit=None)), ""])
    return len(todo)


FORMULA_START = ("=", "+", "-", "@", chr(9), chr(13))  # 탭·CR도 엑셀이 수식 시작으로 본다


def _cell(text: str) -> str:
    """판정표는 엑셀로 연다. AI가 낸 값이 =로 시작하면 수식으로 실행되므로 앞에 '를 붙인다 (읽을 때 뗀다)."""
    return "'" + text if text.startswith(FORMULA_START) else text


def _uncell(text: str) -> str:
    return text[1:] if text.startswith("'") and text[1:].startswith(FORMULA_START) else text


# ---- 집계·보고 ----

def summarize(results: list[ItemResult]) -> dict:
    """정확도 = 정답 / (전체 - 판정 전). 판정 전 항목은 분모에서 뺀다 (아직 모르므로)."""
    counts = Counter(result.category for result in results)
    judged = len(results) - counts["needs_human"]
    found_truths = [result for result in results if result.truth_flag == "found" and result.category != "failed"]
    return {
        "total": len(results),
        "judged": judged,
        "counts": dict(counts),
        "accuracy": counts["correct"] / judged if judged else None,
        "risky": sum(result.is_risky for result in results),
        "hold_ratio": (sum(result.category == "over_hold" for result in found_truths) / len(found_truths)) if found_truths else None,
    }


def score_json(score: RunScore) -> dict:
    by_item = {item: summarize([result for result in score.results if result.item == item]) for item in ITEMS}
    return {
        "run": score.name,
        "meta": score.meta,
        "contests": len({result.contest_id for result in score.results}),
        "failed_contests": score.failed_contests,
        "overall_auto": summarize([result for result in score.results if result.item in AUTO_ITEMS]),
        "overall_key": summarize([result for result in score.results if result.item in KEY_ITEMS]),
        "overall_human": summarize([result for result in score.results if result.item in HUMAN_ITEMS]),
        "by_item": by_item,
    }


def render_report(scores: list[RunScore], answer_key: AnswerKey) -> str:
    lines = ["# 추출 정확도 (#9)", "",
             f"정답표: 대회 {sum(1 for truths in answer_key.contests.values() if truths)}건. "
             "채점 규칙은 `eval/answer_key/README.md` \"채점 방식\". 정확도 = 정답 칸 / (전체 칸 - 판정 전). "
             "과잉 보류·출력 실패 칸은 정답이 아니라서 정확도를 낮춘다. 잴 대상이 없으면 \"-\".", ""]
    if answer_key.problems:
        lines += ["## 정답표 형식 문제 (해당 칸은 채점에서 뺌)", ""] + [f"- {problem}" for problem in answer_key.problems] + [""]
    if not scores:
        return "\n".join(lines + ["실행 결과가 없다. 측정 중.", ""])
    summaries = [score_json(score) for score in scores]
    header = "| 항목 | " + " | ".join(_run_label(summary) for summary in summaries) + " |"
    lines += ["## 실행별 비교", "", header, "|---|" + "---|" * len(summaries)]
    for label, key in (("자동 비교 전체", "overall_auto"), ("**중요 항목**", "overall_key"), ("사람 판정 항목", "overall_human")):
        lines.append(f"| {label} | " + " | ".join(_accuracy_cell(summary[key]) for summary in summaries) + " |")
    lines.append("| 위험 오류 (중요 항목) | " + " | ".join(str(summary["overall_key"]["risky"]) for summary in summaries) + " |")
    lines.append("| 출력 실패 (대회 수) | " + " | ".join(str(len(summary["failed_contests"])) for summary in summaries) + " |")
    for category in ("fabricated", "overconfident", "missed"):
        lines.append(f"| {CATEGORY_LABELS[category]} (칸 수) | " + " | ".join(
            str(summary["overall_auto"]["counts"].get(category, 0) + summary["overall_human"]["counts"].get(category, 0)) for summary in summaries) + " |")
    lines.append("| 확인 필요 비율 (자동 비교, 정답 found 중 과잉 보류) | " + " | ".join(
        _percent(summary["overall_auto"]["hold_ratio"]) for summary in summaries) + " |")
    for summary in summaries:
        scored = summary["meta"].get("scored_items")
        if isinstance(scored, list):
            lines.append(f"\n{_run_label(summary)}은 {', '.join(item for item in ITEMS if item in scored)}만 채점했다 "
                         "(나머지 항목은 \"-\"). 전체 정확도는 다른 실행과 분모가 달라 항목별·중요 항목 줄로 비교한다.")
    lines += ["", "## 항목별 정확도", "", header, "|---|" + "---|" * len(summaries)]
    for item in ITEMS:
        marker = " (중요)" if item in KEY_ITEMS else (" (사람 판정)" if item in HUMAN_ITEMS else "")
        lines.append(f"| `{item}`{marker} | " + " | ".join(_accuracy_cell(summary["by_item"][item]) for summary in summaries) + " |")
    for score in scores:
        lines += ["", f"## {score.name} 오류 목록", ""]
        if score.failed_contests:
            lines.append(f"- 출력 실패 (없음·깨짐·대회 아님으로 판단): {', '.join(score.failed_contests)}")
        if score.extra_outputs:
            lines.append(f"- 정답표에 없는 대회 출력 (채점 안 함): {', '.join(score.extra_outputs)}")
        for warning in dict.fromkeys(score.warnings):
            lines.append(f"- 주의: {warning}")
        errors = [result for result in score.results if result.category in ERROR_CATEGORIES - {"failed"}]
        if not errors:
            lines.append("- 오류 없음")
        for result in errors:
            risk = " **위험**" if result.is_risky else ""
            lines.append(f"- {result.contest_id} `{result.item}` {CATEGORY_LABELS[result.category]}{risk}: "
                         f"정답 {display(result.truth, limit=120)} ({result.truth_flag}) / AI {display(result.predicted, limit=120)} ({result.predicted_flag})")
        pending = sum(result.category == "needs_human" for result in score.results)
        if pending:
            lines.append(f"- 판정 전 {pending}칸: 실행 폴더의 `judgments_todo.csv`를 채워 `eval/extraction/judgments/{score.name}.csv`로 저장")
    return "\n".join(lines) + "\n"


def _run_label(summary: dict) -> str:
    meta = summary["meta"]
    model = meta.get("model") or summary["run"]
    return f"{model} ({meta.get('prompt_version', '?')})"


def _accuracy_cell(summary: dict) -> str:
    if summary["accuracy"] is None:
        pending = summary["counts"].get("needs_human", 0)
        return f"- (판정 전 {pending})" if pending else "-"
    correct = summary["counts"].get("correct", 0)
    pending = summary["counts"].get("needs_human", 0)
    suffix = f", 판정 전 {pending}" if pending else ""
    return f"{_percent(summary['accuracy'])} ({correct}/{summary['judged']}{suffix})"


def _percent(ratio) -> str:
    return "-" if ratio is None else f"{ratio * 100:.0f}%"


def display(value, limit: int | None = 80) -> str:
    """보고서·판정표에 쓰는 값 (정답표 표기에 가깝게). 판정표는 limit=None으로 전체를 쓴다."""
    text = _display(value)
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def _display(value) -> str:
    if value is None:
        return "(없음)"
    if isinstance(value, frozenset):
        return ",".join(str(item) for item in sorted(value, key=str)) or "(빈 목록)"
    if isinstance(value, tuple) and value and all(isinstance(stage, tuple) for stage in value):
        return " ‖ ".join(f"{name}: {start or ''} ~ {end or ''}".replace(":  ~", ": ~") for name, start, end in value)
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(part, frozenset) for part in value):
        return f"허용: {display(value[0])} / 제외: {display(value[1])}"
    if isinstance(value, tuple) and len(value) == 2:
        return f"{value[0] or ''} ~ {value[1] or ''}".strip()
    if isinstance(value, tuple) and len(value) == 4:
        low, high, unlimited, individual = value
        size = "제한 없음" if unlimited else (f"{low or ''}~{high or ''}" if low != high or low is None else str(low))
        return f"{size}, 개인 {'가능' if individual else ('불가' if individual is False else '모름')}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지레이더 추출 정확도 채점 (#9)")
    parser.add_argument("runs", nargs="*", type=Path, help="실행 폴더 (meta.json + outputs/)")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    parser.add_argument("--report", type=Path, help="보고서 마크다운 경로 (없으면 화면에만)")
    args = parser.parse_args(argv)
    try:
        answer_key = read_answer_key(args.answers)
    except (OSError, ValueError, csv.Error) as error:  # ValueError: 인코딩이 UTF-8이 아님 등
        print(f"[오류] 정답표를 읽지 못함 ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    scores = []
    for run_dir in args.runs:
        if not (run_dir / "outputs").is_dir():
            print(f"[오류] {run_dir}에 outputs/ 폴더가 없음", file=sys.stderr)
            return 2
        score = score_run(run_dir, answer_key)
        (run_dir / "score.json").write_text(json.dumps(score_json(score), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending = write_judgment_todo(score, run_dir)
        for warning in dict.fromkeys(score.warnings):
            print(f"[주의] {run_dir.name}: {warning}", file=sys.stderr)
        if pending:
            print(f"[판정 전] {run_dir.name}: {pending}칸 → {run_dir / 'judgments_todo.csv'}", file=sys.stderr)
        scores.append(score)
    report = render_report(scores, answer_key)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
