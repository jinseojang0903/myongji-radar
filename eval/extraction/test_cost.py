"""월 비용 환산(#9) 시험. 숫자는 계산을 확인하려고 손으로 만든 것이다 (측정값 아님).

    python -m unittest discover -s eval/extraction
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cost  # noqa: E402


def meta(model_id="claude-sonnet-5-5", calls=10, failed=0, probes=1, **tokens):
    totals = {"calls": calls, "calls_failed": failed, "probe_calls": probes, "cost_usd_estimate": 0.5,
              "input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    totals.update(tokens)
    alias = next((name for name in ("haiku", "sonnet", "opus") if name in model_id), "sonnet")  # 실행기 --model 값
    return {"model": alias, "models_seen": [model_id], "totals": totals}


class RunCostTests(unittest.TestCase):
    def test_each_token_kind_uses_its_price(self):
        # Sonnet 5.5: 입력 2, 캐시 쓰기 2.5, 캐시 읽기 0.1, 출력 10 ($/100만)
        item = cost.run_cost(meta(calls=1, probes=0, input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
                                  cache_read_input_tokens=1_000_000, output_tokens=1_000_000), "run")
        self.assertAlmostEqual(item.per_extraction, 2 + 2.5 + 0.1 + 10)
        self.assertAlmostEqual(item.per_extraction_batch, (2 + 2.5 + 0.1 + 10) / 2)

    def test_failed_and_probe_calls_are_paid_but_not_counted_as_extractions(self):
        item = cost.run_cost(meta(calls=4, failed=1, probes=1, output_tokens=600_000), "run")
        self.assertAlmostEqual(item.per_extraction, 6.0 / 4, msg="6달러를 성공한 4건으로 나눈다")

    def test_models_have_different_prices(self):
        # 입력은 Haiku 긴 프롬프트 경계(10만) 아래로 두고 출력 100만으로 단가 차이를 본다
        tokens = {"input_tokens": 10_000, "output_tokens": 1_000_000}
        per = {model: cost.run_cost(meta(model, calls=1, probes=0, **tokens), model).per_extraction
               for model in cost.PRICES}
        self.assertAlmostEqual(per["claude-haiku-5-5"], 0.10 * 0.01 + 0.50)
        self.assertAlmostEqual(per["claude-sonnet-5-5"], 2 * 0.01 + 10)
        self.assertAlmostEqual(per["claude-opus-5-5"], 4 * 0.01 + 20)

    def test_haiku_long_prompt_price(self):
        short = cost.run_cost(meta("claude-haiku-5-5", calls=1, probes=0, input_tokens=100_000), "short")
        long = cost.run_cost(meta("claude-haiku-5-5", calls=1, probes=0, input_tokens=100_001), "long")
        self.assertAlmostEqual(short.per_extraction, 0.10 * 0.1)
        self.assertAlmostEqual(long.per_extraction, 0.50 * 0.100001)
        self.assertTrue(long.notes and not short.notes)

    def test_refuses_what_it_cannot_convert(self):
        cases = {
            "추출 0건": meta(calls=0),
            "totals 없음": {"models_seen": ["claude-sonnet-5-5"]},
            "모르는 모델": meta("claude-sonnet-9"),
            "별칭으로도 못 고름": {**meta(), "model": None, "models_seen": ["claude-sonnet-5-5", "claude-opus-5-5"]},
            "별칭과 다른 모델뿐": {**meta("claude-opus-5-5"), "model": "haiku"},
            "음수 토큰": meta(output_tokens=-1),
            "문자열 토큰": meta(output_tokens="많음"),
            "참거짓 토큰": meta(output_tokens=True),
            "참거짓 추출 수": meta(calls=True),
        }
        for name, item in cases.items():
            with self.assertRaises(cost.CostError, msg=name):
                cost.run_cost(item, "run")

    def test_main_model_is_chosen_by_the_run_alias(self):
        # claude -p가 보조 모델(Haiku 등)을 함께 쓰면 models_seen에 둘이 나온다. 실행한 --model 별칭으로 고른다
        item = cost.run_cost({**meta("claude-opus-5-5"), "model": "opus",
                              "models_seen": ["claude-haiku-5-5", "claude-opus-5-5"]}, "run")
        self.assertEqual(item.model_id, "claude-opus-5-5")
        self.assertIn("2개 모델", item.notes[0])
        # 날짜가 붙은 ID도 같은 모델
        dated = cost.run_cost(meta("claude-sonnet-5-5-20261001", calls=1, probes=0, output_tokens=1_000_000), "run")
        self.assertEqual(dated.model_id, "claude-sonnet-5-5")
        self.assertAlmostEqual(dated.per_extraction, 10)
        self.assertIsNone(cost._priced_id("claude-sonnet-5-50"), "다른 버전 번호와 섞이지 않는다")

    def test_extra_unpriced_model_is_noted(self):
        item = cost.run_cost({**meta(), "models_seen": ["claude-sonnet-5-5", "보조-모델"]}, "run")
        self.assertEqual(item.model_id, "claude-sonnet-5-5")
        self.assertIn("2개 모델", item.notes[0])


class ReportTests(unittest.TestCase):
    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cost.main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_report_columns_per_month_and_folder_name_only(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "비밀사용자" / "2026-11-12_sonnet_v0.2"
            run.mkdir(parents=True)
            (run / "meta.json").write_text(json.dumps(meta(calls=2, probes=0, output_tokens=200_000)), encoding="utf-8")
            code, out, _ = self.run_cli(run / "meta.json", "--calls-per-month", "10", "100")
        self.assertEqual(code, 0)
        self.assertIn("| 2026-11-12_sonnet_v0.2 | `claude-sonnet-5-5` | 2(0·0) |", out)
        self.assertIn("| $1.00 | $10.00 | $100.00 | $5.00 | $50.00 |", out, "1건 $1 → 월 10건 $10, 100건 $100, Batch 절반")
        self.assertIn("| 월 10건 | 월 100건 | Batch 월 10건 | Batch 월 100건 |", out)
        self.assertNotIn("비밀사용자", out, "전체 경로(PC 사용자 이름)는 넣지 않는다")
        self.assertIn("Claude Code 추정 합계 $0.5000", out)

    def test_cli_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "meta.json"
            broken.write_text("{깨짐", encoding="utf-8")
            self.assertEqual(self.run_cli(broken)[0], 2)
            self.assertEqual(self.run_cli(Path(folder) / "없음.json")[0], 2)
            broken.write_text(json.dumps(meta("claude-sonnet-9")), encoding="utf-8")
            code, _, errors = self.run_cli(broken)
            self.assertEqual(code, 2)
            self.assertIn("단가표 모델을 하나로 정할 수 없음", errors)
            broken.write_text(json.dumps(meta()), encoding="utf-8")
            self.assertEqual(self.run_cli(broken, "--calls-per-month", "0")[0], 2)


if __name__ == "__main__":
    unittest.main()
