import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.models import Notice, NoticeRef
from collector.repository import Repository


CONFIG = '''[defaults]
adapter = "k2web"
title_selector = ".artclViewTitle"
body_selector = ".artclView"
attachment_selector = ".attfile a[href]"

[[sources]]
id = "test"
name = "Test board"
rss_url = "https://example.com/rss"
list_url = "https://example.com/list"
allowed_hosts = ["example.com"]
'''


def reference(number, *, pinned=False):
    return NoticeRef(f"test/1/{number}", f"https://example.com/bbs/test/1/{number}/artclView.do",
                     f"Notice {number}", None, pinned)


def detail(ref):
    return f'<h2 class="artclViewTitle">{ref.title}</h2><div class="artclView">Body</div>'.encode()


class CollectionRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.config = self.root / "sources.toml"

    def seed(self, refs, *, source_id="test"):
        repo = Repository(self.data)
        try:
            for ref in refs:
                repo.save(Notice(source_id, ref.external_id, ref.url, ref.title, None, "Body"), detail(ref))
        finally:
            repo.close()

    def collect(self, refs, *, limit=None, html=False):
        self.config.write_text(CONFIG + ('discovery = "html"\n' if html else ''), encoding="utf-8")
        if html:
            rows = []
            for ref in refs:
                marker = '<span class="_artclNotice">Pinned</span>' if ref.is_pinned else '1'
                rows.append(f'<tr><td class="_artclTdNum">{marker}</td><td>'
                            f'<a class="artclLinkView" href="{ref.url}"><strong>{ref.title}</strong></a></td></tr>')
            listing = ('<table><tbody>' + ''.join(rows) + '</tbody></table>').encode()
        else:
            items = ''.join(f'<item><title>{ref.title}</title><link>{ref.url}</link></item>' for ref in refs)
            listing = f'<rss><channel>{items}</channel></rss>'.encode()
        discovery_url = "https://example.com/list" if html else "https://example.com/rss"
        responses = {ref.url: detail(ref) for ref in refs}
        responses[discovery_url] = listing
        requests = []

        def fetch(url, hosts):
            self.assertEqual(hosts, ["example.com"])
            requests.append(url)
            return responses[url]

        fetcher = Mock()
        fetcher.get.side_effect = fetch
        args = ["collector", "run", "--config", str(self.config), "--data-dir", str(self.data)]
        if limit is not None:
            args += ["--limit", str(limit)]
        with patch("sys.argv", args), patch("collector.__main__.Fetcher", return_value=fetcher), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        fetcher.close.assert_called_once()
        self.assertEqual(requests[0], discovery_url)
        return requests[1:]

    def notices(self):
        return json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"]

    def test_default_collects_more_than_three_new_posts(self):
        refs = [reference(i) for i in range(6)]
        self.assertEqual(self.collect(refs), [ref.url for ref in refs])
        self.assertEqual(len(self.notices()), 6)

    def test_default_cap_and_next_run_prioritize_remaining_posts(self):
        refs = [reference(i) for i in range(55)]
        self.assertEqual(self.collect(refs), [ref.url for ref in refs[:50]])
        self.assertEqual(len(self.notices()), 50)
        second = self.collect(refs)
        self.assertEqual(second[:5], [ref.url for ref in refs[50:]])
        self.assertEqual(len(second), 50)
        self.assertEqual(len(self.notices()), 55)

    def test_explicit_limit_selects_unseen_posts_before_saved_posts(self):
        refs = [reference(i) for i in range(5)]
        self.seed(refs[:2])
        self.assertEqual(self.collect(refs, limit=2), [ref.url for ref in refs[2:4]])
        self.assertEqual(len(self.notices()), 4)

    def test_saved_identity_is_scoped_to_source(self):
        refs = [reference(1), reference(2)]
        self.seed(refs[:1])
        self.seed(refs[1:], source_id="other")
        self.assertEqual(self.collect(refs, limit=1), [refs[1].url])

    def test_saved_posts_are_still_checked_for_changes(self):
        refs = [reference(1), reference(2)]
        self.seed(refs)
        self.assertEqual(self.collect(refs), [ref.url for ref in refs])
        self.assertEqual(len(self.notices()), 2)

    def test_html_new_regular_and_pinned_posts_precede_saved_posts(self):
        refs = [reference(1, pinned=True), reference(2), reference(3, pinned=True), reference(4), reference(5)]
        self.seed(refs[:2])
        self.assertEqual(self.collect(refs, html=True), [refs[i].url for i in (3, 4, 2, 1, 0)])
        self.assertEqual(len(self.notices()), 5)
        self.assertTrue(all("is_pinned" not in notice for notice in self.notices()))

    def test_saved_pinned_posts_do_not_take_limited_new_post_slots(self):
        refs = [reference(1, pinned=True), reference(2, pinned=True), reference(3), reference(4)]
        self.seed(refs[:2])
        self.assertEqual(self.collect(refs, html=True, limit=2), [refs[2].url, refs[3].url])

    def test_html_duplicate_post_is_requested_once(self):
        refs = [reference(1, pinned=True), reference(1), reference(2)]
        self.assertEqual(self.collect(refs, html=True), [refs[2].url, refs[0].url])
        self.assertEqual(len(self.notices()), 2)


if __name__ == "__main__":
    unittest.main()
