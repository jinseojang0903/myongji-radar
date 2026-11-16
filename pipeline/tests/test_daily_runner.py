"""PowerShell 실행기의 인자·종료 코드 계약. Python 호출만 무해한 로컬 stub으로 대체한다."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_daily_collect.ps1"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell 실행기 시험")
class DailyRunnerTests(unittest.TestCase):
    def invoke(self, exit_code=0, *, validate=False, branch="main"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline = root / "pipeline"
            scripts = root / "scripts"
            pipeline.mkdir()
            scripts.mkdir()
            capture = root / "called.json"
            # 실제 실행기는 그대로 쓰고 Python 실행 파일 경로만 무해한 스크립트로 주입한다.
            original = RUNNER.read_text(encoding="utf-8-sig")
            assignment = "$pythonPath = Join-Path $pipelineRoot '.venv\\Scripts\\python.exe'"
            self.assertIn(assignment, original)
            copied = scripts / "run_daily_collect.ps1"
            copied.write_text(original.replace(assignment, "$pythonPath = Join-Path $pipelineRoot 'stub.ps1'"),
                              encoding="utf-8-sig")
            capture_literal = str(capture).replace("'", "''")
            (pipeline / "stub.ps1").write_text(
                "@{ arguments=@($args); cwd=(Get-Location).Path; search=$env:NoDefaultCurrentDirectoryInExePath; "
                "utf8=$env:PYTHONUTF8; encoding=$env:PYTHONIOENCODING } | ConvertTo-Json -Compress | "
                f"Set-Content -LiteralPath '{capture_literal}' -Encoding UTF8\n"
                f"$global:LASTEXITCODE = {exit_code}\n", encoding="utf-8-sig")
            git_function = "throw 'ValidateOnly must not inspect branch'" if validate else (
                f"$global:LASTEXITCODE=0; '{branch}'")
            runner_literal = str(copied).replace("'", "''")
            code = ("$ErrorActionPreference='Stop'; "
                    f"function git {{ {git_function} }}; "
                    f"& '{runner_literal}'" + (" -ValidateOnly" if validate else "") + "; exit $LASTEXITCODE")
            encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                    capture_output=True, timeout=30)
            recorded = json.loads(capture.read_text(encoding="utf-8-sig")) if capture.exists() else None
            expected_config = str(pipeline / "config/publishing.toml")
            return result, recorded, str(pipeline), expected_config

    def test_production_forwards_config_and_all_publish_exit_codes(self):
        for code in (0, 1, 2, 3):
            with self.subTest(code=code):
                result, recorded, pipeline, config = self.invoke(code)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(recorded["arguments"], ["-m", "collector.daily", "--publish-config", config])
                self.assertEqual(recorded["cwd"], pipeline)
                self.assertEqual((recorded["search"], recorded["utf8"], recorded["encoding"]), ("1", "1", "utf-8"))

    def test_validation_uses_same_config_and_dry_run_without_branch_or_python_collection(self):
        result, recorded, _, config = self.invoke(validate=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["arguments"], ["-m", "collector.daily", "--publish-config", config, "--dry-run"])

    def test_production_on_development_branch_never_calls_python(self):
        result, recorded, _, _ = self.invoke(branch="63-daily-publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(recorded)
