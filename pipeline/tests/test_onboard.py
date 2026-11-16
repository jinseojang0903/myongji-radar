"""게시판 주소로 출처 등록 (collector.onboard) 시험. 네트워크는 쓰지 않는다 (가짜 fetcher)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import onboard
from collector.config import load_sources

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.toml"
DEFAULTS = onboard.read_defaults(CONFIG)
HOST = "math.mju.ac.kr"
LIST = f"https://{HOST}/math/1234/subview.do"
RSS = f"https://{HOST}/bbs/math/777/rssList.do?row=50"
ARTICLE = f"https://{HOST}/bbs/math/777/{{}}/artclView.do"
PAGE = (f'<html><head><title>공지사항 | 명지대학교</title></head><body><table><tbody>'
        f'<tr><td class="_artclTdNum">1</td><td><a class="artclLinkView" href="/bbs/math/777/501/artclView.do"><strong>공모전 안내</strong></a></td>'
        f'<td class="_artclTdRdate">2026.11.10</td></tr></tbody></table></body></html>').encode("utf-8")
RSS_XML = ("<?xml version='1.0' encoding='UTF-8'?><rss><channel>"
           + "".join(f"<item><title>글 {n}</title><link>{ARTICLE.format(n)}</link><pubDate>2026-11-1{n % 10}T09:00:00</pubDate></item>"
                     for n in (501, 502, 503, 504))
           + "</channel></rss>").encode("utf-8")
DETAIL = '<div class="artclViewTitle">공모전 안내</div><div class="artclView">신청 기간 11월</div>'.encode("utf-8")


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, allowed_hosts):
        self.calls.append((url, list(allowed_hosts)))
        page = self.pages.get(url)
        if page is None:
            raise OSError("없는 페이지")
        return page


def details(numbers=(501, 502, 503, 504), body=DETAIL):
    return {ARTICLE.format(n): body for n in numbers}


class OnboardTests(unittest.TestCase):
    def setUp(self):
        self.existing = load_sources(CONFIG)

    def probe(self, url, pages, name=None):
        return onboard.probe(url, FakeFetcher(pages), self.existing, DEFAULTS, name)

    def test_list_page_url_finds_the_board_and_uses_rss(self):
        proposal = self.probe(LIST, {LIST: PAGE, RSS: RSS_XML, **details()})
        self.assertTrue(proposal.ok, proposal.reason)
        self.assertEqual((proposal.sampled, proposal.readable), (3, 3))
        entry = proposal.entry
        self.assertEqual((entry["id"], entry["rss_url"], entry["allowed_hosts"]), ("mju_math", RSS, [HOST]))
        self.assertEqual(entry["name"], f"공지사항 ({HOST})", "페이지 제목에서 학교 이름을 빼고, 흔한 이름이면 호스트를 덧붙인다")
        self.assertTrue(proposal.name_from_page)
        self.assertNotIn("discovery", entry)

    def test_widget_and_other_host_links_do_not_pick_the_board(self):
        widget = ('<html><title>수학과 공지</title><div class="side"><a href="/bbs/math/999/1/artclView.do">최신글</a>'
                  '<a class="artclLinkView" href="https://other.mju.ac.kr/bbs/math/555/1/artclView.do">다른 호스트</a></div>'
                  + PAGE.decode("utf-8").split("<body>", 1)[1]).encode("utf-8")
        proposal = self.probe(LIST, {LIST: widget, RSS: RSS_XML, **details()})
        self.assertTrue(proposal.ok, proposal.reason)
        self.assertEqual(proposal.entry["rss_url"], RSS, "목록 표의 같은 호스트 글 링크(777)만 본다")
        self.assertEqual(proposal.entry["name"], "수학과 공지")

    def test_names_are_cleaned_for_the_public_card(self):
        self.assertEqual(onboard.clean_name("수학과\n공지‮\x07  게시판"), "수학과 공지 게시판")
        self.assertEqual(onboard.clean_name("가" * 60), "가" * 40)
        self.assertEqual(onboard.clean_name("\n\t "), "")
        entry = {"id": "mju_x", "name": '따옴표 "와 \\ 그리고 \x7f', "priority": 100, "rss_url": RSS, "allowed_hosts": [HOST]}
        self.assertIn('name = "따옴표 \\"와 \\\\ 그리고 \\u007f"', onboard.toml_entry(entry))

    def test_given_name_is_used_and_html_list_is_the_fallback(self):
        proposal = self.probe(LIST, {LIST: PAGE, **details()}, name="수학과 공지")
        self.assertTrue(proposal.ok, proposal.reason)
        self.assertEqual((proposal.entry["name"], proposal.entry["discovery"], proposal.entry["list_url"]),
                         ("수학과 공지", "html", LIST), "RSS가 없으면 목록 페이지로 발견")

    def test_unsupported_unreadable_and_known_boards(self):
        fetcher = FakeFetcher({})
        for url in ("http://math.mju.ac.kr/x", "https://example.com/bbs/a/1/rssList.do", "https://user:pw@math.mju.ac.kr/",
                    "https://math.mju.ac.kr:abc/x", "https://math.mju.ac.kr:8443/x"):
            proposal = onboard.probe(url, fetcher, self.existing, DEFAULTS)
            self.assertFalse(proposal.ok)
            self.assertIn("지원 안 됨", proposal.reason)
        self.assertEqual(fetcher.calls, [], "학교 밖·http·인증정보 주소는 요청하지 않는다")
        other = self.probe(LIST, {LIST: b"<html><title>x</title><a href='/page.do'>a</a></html>"})
        self.assertIn("K2Web 게시판 글 링크를 찾지 못함", other.reason)
        unreadable = self.probe(LIST, {LIST: PAGE, RSS: RSS_XML, **details(body=b"<div>login</div>")})
        self.assertFalse(unreadable.ok)
        self.assertIn("제목·본문을 읽은 글이 없음", unreadable.reason)
        known = self.existing[0]
        url = known.rss_url or known.list_url
        if "/bbs/" in url:
            proposal = onboard.probe(url, FakeFetcher({}), self.existing, DEFAULTS, name="이름")
            self.assertIsNone(proposal.ok, "이미 등록된 게시판은 실패가 아니라 건너뜀")

    def test_ids_do_not_collide(self):
        taken = {source.id for source in self.existing} | {"mju_math"}
        proposal = onboard.probe(LIST, FakeFetcher({LIST: PAGE, RSS: RSS_XML, **details()}), self.existing, DEFAULTS, None, taken)
        self.assertEqual(proposal.entry["id"], "mju_math_777")
        taken.add("mju_math_777")
        third = onboard.probe(RSS, FakeFetcher({RSS: RSS_XML, **details()}), self.existing, DEFAULTS, "이름", taken)
        self.assertFalse(third.ok)
        self.assertIn("출처 id를 만들지 못함", third.reason, "원인을 그대로 보여 준다")
        self.assertEqual(onboard._source_id("x" * 60, "12345", set())[:4], "mju_")
        self.assertEqual(len(onboard._source_id("x" * 60, "12345", {"mju_" + "x" * 36})), 40)
        self.assertTrue(onboard._source_id("x" * 60, "12345", {"mju_" + "x" * 36}).endswith("_12345"))

    def test_same_board_twice_in_one_run_is_registered_once(self):
        fetcher = FakeFetcher({LIST: PAGE, RSS: RSS_XML, **details()})
        taken, known = {source.id for source in self.existing}, onboard.known_boards(self.existing)
        first = onboard.probe(LIST, fetcher, self.existing, DEFAULTS, "수학과", taken, known)
        second = onboard.probe(RSS, fetcher, self.existing, DEFAULTS, "수학과", taken, known)
        self.assertTrue(first.ok)
        self.assertIsNone(second.ok, "같은 실행에서 앞서 통과한 게시판은 건너뛴다")

    def test_write_appends_and_rolls_back_on_invalid_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "sources.toml"
            config.write_bytes(CONFIG.read_bytes())
            entry = self.probe(LIST, {LIST: PAGE, RSS: RSS_XML, **details()}, name='따옴표 "이름"').entry
            onboard.write_entries(config, [entry])
            added = [source for source in load_sources(config) if source.id == "mju_math"]
            self.assertEqual((len(added), added[0].name), (1, '따옴표 "이름"'))
            before = config.read_bytes()
            with self.assertRaises(ValueError):
                onboard.write_entries(config, [{**entry, "id": "mju_math"}])  # 같은 id: 형식 검사 실패
            self.assertEqual(config.read_bytes(), before, "실패하면 원래대로 되돌린다")

    def test_cli_reports_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "sources.toml"
            config.write_bytes(CONFIG.read_bytes())
            fetcher = FakeFetcher({LIST: PAGE, RSS: RSS_XML, **details()})
            out = io.StringIO()
            with mock.patch.object(onboard, "Fetcher", return_value=fetcher), contextlib.redirect_stdout(out):
                code = onboard.main([LIST, "https://example.com/x", "--config", str(config), "--write",
                                     "--name", "수학과 공지", "--name", "학교 밖"])
            self.assertEqual(code, 1, "일부 주소를 등록하지 못하면 1")
            self.assertIn("[등록 가능]", out.getvalue())
            self.assertIn("[더함] 1곳", out.getvalue())
            self.assertTrue(any(source.id == "mju_math" for source in load_sources(config)))
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                onboard.main([LIST, RSS, "--name", "하나만", "--config", str(config)])  # 이름 수가 주소 수와 다름


if __name__ == "__main__":
    unittest.main()
