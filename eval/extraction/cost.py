"""모델 비교(#9) 실행의 토큰 수 → 공개 API 단가로 월 비용 환산.

    python eval/extraction/cost.py <실행 폴더>/meta.json ... [--calls-per-month 17 40] [--report reports/<날짜>_cost.md]

- 입력: 실행 폴더의 meta.json (`totals`·`models_seen`). `runs/`는 올리지 않으므로 이 보고서에 숫자만 옮긴다.
- 단가: 아래 PRICES (출처·조회일은 PRICES_SOURCE). 구독 로그인으로 돌렸다면 실제 청구액이 아니라
  "학생 서비스로 바꿔 API 키로 돌릴 때" 드는 돈의 추정이다.
- 1건 비용은 이 실행에서 쓴 토큰 전부(실패한 호출·확인 호출 포함)를 성공한 추출 수로 나눈 값이다
  (실패해도 돈은 든다). 월 비용 = 1건 비용 × 한 달 추출 수.
- 한 달 추출 수 기본값: 2026-01-01 ~ 11-03 대회 관련 공지 167건(docs/survey) → 월 평균 약 17건, 몰리는 달(4·10월) 40건.
- 학생 문장 해석·판정은 사이트(브라우저)에서 돌아 API 비용이 없다.

종료 코드: 0 보고서를 씀, 2 입력을 읽지 못함·모르는 모델 (보고서를 쓰지 않음)
"""
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing (2026-11-11 조회)"
TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
DEFAULT_CALLS_PER_MONTH = (17, 40)
BATCH_DISCOUNT = 0.5  # Batch API는 입력·출력·캐시 모두 50% (매일 추출은 실시간이 아니어도 된다)


@dataclass(frozen=True)
class Price:
    """$ / 100만 토큰. cache_write는 5분 캐시 쓰기 (claude -p 기본)."""
    input: float
    cache_write: float
    cache_read: float
    output: float

    def cost(self, tokens: dict) -> float:
        return (tokens["input_tokens"] * self.input + tokens["cache_creation_input_tokens"] * self.cache_write
                + tokens["cache_read_input_tokens"] * self.cache_read + tokens["output_tokens"] * self.output) / 1_000_000


# Haiku 5.5는 프롬프트가 10만 토큰을 넘으면 비싼 단가 (PRICES_LONG). 합계만 남으므로 1건 평균 입력으로 고른다
PRICES = {
    "claude-haiku-5-5": Price(input=0.10, cache_write=0.125, cache_read=0.01, output=0.50),
    "claude-sonnet-5-5": Price(input=2.00, cache_write=2.50, cache_read=0.10, output=10.00),
    "claude-opus-5-5": Price(input=4.00, cache_write=5.00, cache_read=0.20, output=20.00),
}
PRICES_LONG = {"claude-haiku-5-5": (100_000, Price(input=0.50, cache_write=0.625, cache_read=0.05, output=2.50))}


class CostError(ValueError):
    """meta.json을 환산할 수 없음 (메시지는 파일 이름·모델 ID만, 공지 내용 없음)."""


@dataclass
class RunCost:
    label: str
    model_id: str
    extractions: int
    failed: int
    probes: int
    tokens: dict
    per_extraction: float
    per_extraction_batch: float
    claude_estimate: float | None
    notes: list


def run_cost(meta: dict, label: str) -> RunCost:
    """meta.json 하나 → 1건 비용.

    Raises:
        CostError: 성공한 추출이 없음, 토큰 합계가 없음, 단가표에 없는 모델이거나 모델이 둘 이상
    """
    totals = meta.get("totals") if isinstance(meta, dict) else None
    if not isinstance(totals, dict):
        raise CostError(f"{label}: totals가 없음")
    extractions = totals.get("calls")
    if not _is_count(extractions) or extractions <= 0:
        raise CostError(f"{label}: 성공한 추출이 없어 1건 비용을 낼 수 없음")
    tokens = {}
    for key in TOKEN_KEYS:
        value = totals.get(key, 0)
        if not _is_count(value):
            raise CostError(f"{label}: totals.{key}가 0 이상의 정수가 아님")
        tokens[key] = value
    seen = [name for name in meta.get("models_seen") or [] if isinstance(name, str)]
    model_id = _main_model(seen, meta.get("model"))
    if model_id is None:
        # 모르는 ID면 틀린 단가로 계산하지 않는다. 공식 가격 페이지를 보고 단가표를 먼저 고친다
        raise CostError(f"{label}: 실행 모델(--model)에 맞는 단가표 모델을 하나로 정할 수 없음 (models_seen={seen[:5]})")
    notes = []
    if len(seen) > 1:
        notes.append(f"models_seen에 {len(seen)}개 모델: 토큰 합계를 모두 {model_id} 단가로 계산 "
                     "(보조 모델 토큰이 합계에 들어 있으면 조금 높게 잡힘. 아래 Claude Code 추정과 대조)")
    price = PRICES[model_id]
    calls_total = extractions + _count(totals, "calls_failed") + _count(totals, "probe_calls")
    prompt_per_call = sum(tokens[key] for key in TOKEN_KEYS if key != "output_tokens") / calls_total
    if model_id in PRICES_LONG and prompt_per_call > PRICES_LONG[model_id][0]:
        price = PRICES_LONG[model_id][1]
        notes.append(f"1건 평균 입력이 {PRICES_LONG[model_id][0]:,}토큰을 넘어 긴 프롬프트 단가")
    total = price.cost(tokens)
    estimate = totals.get("cost_usd_estimate")
    return RunCost(label=label, model_id=model_id, extractions=extractions, failed=_count(totals, "calls_failed"),
                   probes=_count(totals, "probe_calls"), tokens=tokens, per_extraction=total / extractions,
                   per_extraction_batch=total * BATCH_DISCOUNT / extractions,
                   claude_estimate=estimate if isinstance(estimate, (int, float)) and not isinstance(estimate, bool) else None,
                   notes=notes)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _count(totals: dict, key: str) -> int:
    value = totals.get(key, 0)
    return value if _is_count(value) else 0


def _priced_id(name: str) -> str | None:
    """단가표의 ID. 날짜가 붙은 ID("claude-sonnet-5-5-20261001")도 같은 모델로 본다."""
    return next((model for model in PRICES if name == model or name.startswith(model + "-")), None)


def _main_model(seen: list[str], alias) -> str | None:
    """실행한 모델. claude -p가 보조 모델을 함께 쓰면 models_seen에 둘 이상 나오므로 --model 별칭(haiku 등)으로 고른다."""
    priced = sorted({model for model in map(_priced_id, seen) if model})
    if isinstance(alias, str) and alias:
        # --model haiku로 돌렸는데 Opus만 보이면 이상한 실행이라 멈춘다
        priced = [model for model in priced if alias in model]
    return priced[0] if len(priced) == 1 else None


def render_report(costs: list[RunCost], calls_per_month: list[int]) -> str:
    months = " | ".join(f"월 {calls}건" for calls in calls_per_month)
    batch_months = " | ".join(f"Batch 월 {calls}건" for calls in calls_per_month)
    lines = [
        "# 모델별 월 비용 환산 (#9)", "",
        f"- 단가: {PRICES_SOURCE}. 5분 캐시 쓰기, 전 세계 라우팅(기본) 기준.",
        "- 추출 1건 비용 = 실행에서 쓴 토큰 전부(실패한 호출·확인 호출 포함)의 값 ÷ 성공한 추출 수. "
        "확인 호출은 실행마다 한 번이라 대회 수가 적은 실행에서는 1건 비용이 조금 높게 나온다.",
        "- 호출당 평균 입력·출력은 모든 호출(성공·실패·확인)로 나눈 값이다. Haiku 긴 프롬프트 단가(10만 토큰 초과)도 이 평균으로 "
        "고르므로, 몇 건만 10만을 넘으면 놓친다.",
        "- 월 비용 = 추출 1건 비용 × 한 달 추출 수. Batch는 매일 추출을 Batch API로 돌릴 때(50% 할인).",
        "- 구독 로그인으로 잰 토큰 수를 공개 API 단가로 바꾼 추정이다 (실제 청구액 아님).", "",
        f"| 실행 | 모델 | 추출(실패·확인) | 호출당 평균 입력 | 호출당 평균 출력 | 추출 1건 비용 | {months} | {batch_months} |",
        "|---|---|---|---|---|---|" + "---|" * (len(calls_per_month) * 2),
    ]
    for item in costs:
        calls = item.extractions + item.failed + item.probes
        prompt = sum(item.tokens[key] for key in TOKEN_KEYS if key != "output_tokens") / calls
        monthly = " | ".join(_dollars(item.per_extraction * count) for count in calls_per_month)
        batch = " | ".join(_dollars(item.per_extraction_batch * count) for count in calls_per_month)
        lines.append(f"| {item.label} | `{item.model_id}` | {item.extractions}({item.failed}·{item.probes}) | "
                     f"{prompt:,.0f} | {item.tokens['output_tokens'] / calls:,.0f} | {_dollars(item.per_extraction)} | "
                     f"{monthly} | {batch} |")
    checks = [f"- {item.label}: Claude Code 추정 합계 {_dollars(item.claude_estimate)} (이 표의 합계 "
              f"{_dollars(item.per_extraction * item.extractions)})" for item in costs if item.claude_estimate is not None]
    if checks:
        lines += ["", "**대조:** `totals.cost_usd_estimate`(Claude Code가 계산한 값)와 크게 다르면 단가표나 토큰 합계를 확인한다.", *checks]
    notes = [f"- {item.label}: {note}" for item in costs for note in item.notes]
    if notes:
        lines += ["", "**참고:**", *notes]
    return "\n".join(lines) + "\n"


def _dollars(value: float) -> str:
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="모델 비교 실행의 토큰 수를 공개 API 단가로 월 비용 환산 (#9)")
    parser.add_argument("meta", type=Path, nargs="+", help="실행 폴더의 meta.json")
    parser.add_argument("--calls-per-month", type=int, nargs="+", default=list(DEFAULT_CALLS_PER_MONTH),
                        help="한 달 추출 수 (여러 개면 열마다)")
    parser.add_argument("--report", type=Path, help="보고서 파일 (없으면 화면에)")
    args = parser.parse_args(argv)
    if any(count <= 0 for count in args.calls_per_month):
        print("[오류] --calls-per-month는 1 이상", file=sys.stderr)
        return 2
    try:
        # 이름은 실행 폴더 이름 (예: 2026-11-12_haiku_v0.2). 전체 경로는 보고서에 넣지 않는다 (PC 사용자 이름)
        costs = [run_cost(json.loads(path.read_text(encoding="utf-8")), path.resolve().parent.name) for path in args.meta]
    except CostError as error:
        print(f"[오류] {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"[오류] meta.json을 읽지 못함 ({type(error).__name__})", file=sys.stderr)
        return 2
    report = render_report(costs, args.calls_per_month)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8", newline="\n")
        print(f"[환산] {len(costs)}개 실행 → {args.report}", file=sys.stderr)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
