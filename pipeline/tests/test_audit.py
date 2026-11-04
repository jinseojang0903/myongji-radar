import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from collector.adapters.k2web import K2WebAdapter
from collector.config import load_sources
from collector.models import NoticeRef
from collector.repository import Repository


ROOT = Path(__file__).resolve().parent.parent
AUDIT_SCRIPT = ROOT / "scripts/audit_samples.py"


class AuditTests(unittest.TestCase):
    def test_shared_config_and_saved_samples_without_network(self):
        # 기본 선택자가 출처별로 반복되지 않아도 점검 스크립트가 동작한다.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            config = '''[defaults]
adapter = "k2web"
title_selector = ".artclViewTitle"
body_selector = ".artclView"
attachment_selector = ".attfile a[href]"

[[sources]]
id = "audit_fixture"
name = "Audit fixture"
priority = 1
rss_url = "https://example.com/rss"
allowed_hosts = ["example.com"]
'''
            (root / "config/sources.toml").write_text(config, encoding="utf-8")
            repo = Repository(root / "data")
            try:
                adapter = K2WebAdapter()
                source, = load_sources(root / "config/sources.toml")
                for index in range(5):
                    html = f'<h2 class="artclViewTitle">Example {index}</h2><div class="artclView">Body</div>'.encode()
                    ref = NoticeRef(str(index), f"https://{source.allowed_hosts[0]}/post/{index}", "", None)
                    repo.save(adapter.parse_detail(html, ref, source), html)
            finally:
                repo.close()
            spec = importlib.util.spec_from_file_location("audit_samples", AUDIT_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.ROOT = root
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.audit(), 0)
            result = json.loads((root / "data/audit/structural_report.json").read_text(encoding="utf-8"))
            self.assertEqual([s["id"] for s in result["sources"]], ["audit_fixture"])
            self.assertTrue(all(s["sample_count"] == 5 and s["passed_checks"] == 20 for s in result["sources"]))

    def test_script_imports_from_a_different_working_directory(self):
        # 스크립트 경로로 실행할 때도 옮겨진 collector 패키지를 찾는다.
        with tempfile.TemporaryDirectory() as tmp:
            code = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='audit_import_check')"
            result = subprocess.run([sys.executable, "-c", code, str(AUDIT_SCRIPT)],
                                    cwd=tmp, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
