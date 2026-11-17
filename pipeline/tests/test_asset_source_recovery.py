"""실행 기록으로 출처·공지의 첨부 처리 차례를 넘긴다. 모든 HTTP·변환은 대역이다."""
import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.repository import Repository
from collector.assets import source_order


class AssetSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.config = self.root / "sources.toml"
        self.sources = ["a", "b", "c"]
        self.files = {key: [("one", key.encode() * 6)] for key in self.sources}
        self.config.write_text('''[defaults]
adapter="k2web"
title_selector=".title"
body_selector=".body"
attachment_selector=".files a[href]"
''' + "".join(f'''[[sources]]
id="{key}"
name="{key}"
rss_url="https://{key}.mju.ac.kr/rss"
list_url="https://{key}.mju.ac.kr/list"
allowed_hosts=["{key}.mju.ac.kr"]
''' for key in self.sources), encoding="utf-8")

    def collect(self, source=None):
        calls = []
        def fetch(url, hosts):
            calls.append(url)
            key = hosts[0].split(".")[0]
            if url.endswith("/rss"):
                return (f'<rss><channel><item><title>Contest</title><link>https://{key}.mju.ac.kr/bbs/{key}/1/1/artclView.do</link>'
                        '</item></channel></rss>').encode()
            if url.endswith("artclView.do"):
                links = ''.join(f'<a href="https://{key}.mju.ac.kr/file/{name}">{name}.pdf</a>' for name, _ in self.files[key])
                return f'<h2 class="title">Contest</h2><div class="body">Body</div><div class="files">{links}</div>'.encode()
            return dict(self.files[key])[url.rsplit("/", 1)[-1]]
        args = ["run", "--config", str(self.config), "--data-dir", str(self.data)]
        if source:
            args += ["--source", source]
        with patch("collector.__main__.Fetcher", return_value=Mock(get=Mock(side_effect=fetch))), \
                patch("collector.assets.MAX_RUN_BYTES", 10), patch("collector.assets.AssetCollector.convert"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        return calls

    def test_later_sources_progress_and_same_url_changes_are_still_detected(self):
        for first in self.sources:
            calls = self.collect()
            self.assertEqual(calls[0], f"https://{first}.mju.ac.kr/rss")
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        for key in self.sources:
            item = repo.latest_payload(key, f"{key}/1/1")["attachments"][0]
            self.assertEqual((self.data / item["local_path"]).read_bytes(), key.encode() * 6)
        self.files["a"] = [("one", b"NEWaaa")]
        self.collect()
        item = repo.latest_payload("a", "a/1/1")["attachments"][0]
        self.assertEqual(item["sha256"], hashlib.sha256(b"NEWaaa").hexdigest(), "같은 URL도 다시 요청한다")

    def test_oversized_source_yields_to_other_sources(self):
        self.files["a"] += [("two", b"222222"), ("three", b"333333")]
        self.collect()
        self.assertEqual(self.collect()[0], "https://b.mju.ac.kr/rss")
        self.assertEqual(self.collect()[0], "https://c.mju.ac.kr/rss")
        self.collect()
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        items = repo.latest_payload("a", "a/1/1")["attachments"]
        self.assertTrue(items[1].get("local_path"), "다른 출처를 거친 뒤에도 진짜 미수집 파일이 먼저")

    def test_single_source_trial_does_not_replace_the_full_run_cursor(self):
        self.collect()
        self.collect(source="c")
        self.assertEqual(self.collect()[0], "https://b.mju.ac.kr/rss")

    def test_partial_full_run_deadline_moves_the_next_start(self):
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        run = repo.start_run()
        repo.finish_run(run, [
            {"source": "a", "run_scope": "all", "warnings": [], "errors": []},
            {"source": "b", "run_scope": "all", "warnings": [], "errors": [{"error": "run_deadline_exceeded"}]},
        ])
        previous = repo.latest_full_run(self.sources)
        sources = [SimpleNamespace(id=key) for key in self.sources]
        self.assertEqual([s.id for s in source_order(sources, previous)], ["b", "c", "a"])
