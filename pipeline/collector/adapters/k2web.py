import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, CData, NavigableString, Tag

from ..models import Notice, NoticeRef, Source
from ..text_safety import safe_terminal

# 줄을 바꾸는 태그. 그 밖의 태그(span·b·font·a 등 글자 꾸밈)는 앞뒤 글자를 이어 붙인다.
# 한글 문서에서 붙여 넣은 공지는 "2026. 11. 9.(<span>월</span>)"처럼 글자마다 span으로 쪼개져 있어,
# 태그마다 줄을 바꾸면 한 줄이 여러 줄로 찢어지고 AI 인용 근거가 "2026. 11. 9.("처럼 잘린다.
BLOCK_TAGS = {"address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dir", "div", "dl",
              "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
              "html", "legend", "li", "main", "menu", "nav", "ol", "option", "p", "pre", "section", "summary", "table",
              "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul"}
# 글자로 읽지 않는 태그 (본문은 parse_detail이 이미 script·style·noscript를 지우지만 제목·목록 글에도 같은 함수를 쓴다)
SKIPPED_TAGS = {"script", "style", "noscript", "template"}
# 글자로 모으는 노드 종류. NavigableString의 하위 종류인 주석·처리 지시문(한글·워드에서 붙여 넣은 공지의
# "<?xml:namespace ...?>")·문서 선언은 글자가 아니다. bs4 get_text의 기본값과 같다
TEXT_TYPES = (NavigableString, CData)
LINE_BREAK = "\x00"  # 내부 줄 표시 (원문 글자의 줄바꿈은 HTML 규칙대로 띄어쓰기로 본다)


def normalized_text(node) -> str:
    """HTML 노드 → 글자. 블록 태그와 <br>에서만 줄을 바꾸고, 줄마다 공백을 하나로 접는다.
    트리를 반복으로 돈다 (깊게 중첩된 공지에서 재귀 한도에 걸리지 않게)."""
    parts = []
    stack = [iter(node.children)] if isinstance(node, Tag) else []
    closing = []  # 스택 깊이마다 닫을 때 줄을 바꿀지
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            if closing and closing.pop():
                parts.append(LINE_BREAK)
            continue
        if isinstance(child, NavigableString):
            if type(child) in TEXT_TYPES:  # 하위 종류(주석·처리 지시문 등)는 정확한 종류로 거른다
                parts.append(str(child).replace(LINE_BREAK, " "))
            continue
        if not isinstance(child, Tag) or child.name in SKIPPED_TAGS:
            continue
        if child.name == "br":
            parts.append(LINE_BREAK)
            continue
        is_block = child.name in BLOCK_TAGS
        if is_block:
            parts.append(LINE_BREAK)
        stack.append(iter(child.children))
        closing.append(is_block)
    return "\n".join(
        line for part in "".join(parts).split(LINE_BREAK)
        if (line := safe_terminal(" ".join(part.split())))
    )


class K2WebAdapter:
    def __init__(self):
        self.discovery_errors = []

    def discover(self, xml: bytes, source: Source) -> list[NoticeRef]:
        self.discovery_errors = []
        if source.discovery == "html":
            return self.discover_html(xml, source)
        if source.repair_rss_ampersands:
            # 확인된 두 RSS의 title에 있는 미이스케이프 &만 보정한다.
            # 정상 엔티티와 CDATA는 보존하고, 다른 XML 오류는 그대로 실패시킨다.
            chunks = re.split(r'(<!\[CDATA\[.*?\]\]>)', xml.decode("utf-8"), flags=re.S)
            xml = "".join(chunk if chunk.startswith("<![CDATA[") else re.sub(
                r'&(?!amp;|lt;|gt;|apos;|quot;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', chunk)
                for chunk in chunks).encode("utf-8")
        root = ET.fromstring(xml)
        if root.tag != "rss":
            raise ValueError("RSS 루트가 아닙니다")
        refs = {}
        for item in root.findall("./channel/item"):
            url = source.rss_url
            try:
                link = (item.findtext("link") or "").strip()
                if not link:
                    raise ValueError("RSS 항목 링크 없음")
                url = urljoin(source.rss_url, link)
                match = re.search(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do$", urlsplit(url).path)
                if not match:
                    raise ValueError("지원하지 않는 게시글 URL")
                raw_date = (item.findtext("pubDate") or "").strip()
                published_at = None
                if raw_date:
                    date = datetime.fromisoformat(raw_date)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone(timedelta(hours=9)))
                    published_at = date.isoformat()
                external_id = "/".join(match.groups())
                refs[external_id] = NoticeRef(external_id, url, safe_terminal((item.findtext("title") or "").strip()), published_at)
            except (ValueError, TypeError):
                self.discovery_errors.append({"url": url, "error": "invalid_rss_item"})
        if not refs and not self.discovery_errors:
            raise ValueError("RSS 항목 0건: 빈 게시판 또는 구조 변경 확인 필요")
        return list(refs.values())

    def discover_html(self, html: bytes, source: Source) -> list[NoticeRef]:
        """공개 목록 첫 페이지에서만 발견. 과거 페이지 순회는 아직 지원하지 않는다."""
        soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
        refs = {}
        self.discovery_errors = []
        for row in soup.select("tbody tr"):
            link = row.select_one("a.artclLinkView[href]")
            if link is None:
                continue
            url = source.list_url
            try:
                url = urljoin(source.list_url, link["href"])
                match = re.search(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do$", urlsplit(url).path)
                if not match:
                    raise ValueError("지원하지 않는 HTML 게시글 URL")
                title = link.select_one("strong") or link
                date = row.select_one("._artclTdRdate")
                published = normalized_text(date).replace(".", "-") if date else None
                if published:
                    datetime.strptime(published, "%Y-%m-%d")
            except (ValueError, TypeError):
                self.discovery_errors.append({"url": url, "error": "invalid_html_item"})
                continue
            external_id = "/".join(match.groups())
            # K2Web 목록에서 확인한 고정 행/번호 칸 표식만 사용한다.
            # 제목의 '공지' 문구나 게시 날짜로 고정 여부를 추측하지 않는다.
            is_pinned = ("headline" in row.get("class", [])
                         or row.select_one("._artclTdNum ._artclNotice") is not None)
            previous = refs.get(external_id)
            refs[external_id] = NoticeRef(external_id, url, normalized_text(title), published,
                                         is_pinned or (previous.is_pinned if previous else False))
        if not refs and not self.discovery_errors:
            raise ValueError("HTML 목록 0건: 접근 권한 또는 목록 구조 확인 필요")
        return list(refs.values())

    def parse_detail(self, html: bytes, ref: NoticeRef, source: Source) -> Notice:
        soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
        title = soup.select_one(source.title_selector)
        body = soup.select_one(source.body_selector)
        if title is None or body is None or not normalized_text(title):
            raise ValueError("제목/본문 선택자 불일치: 로그인 또는 게시판 구조 확인 필요")
        for node in body.select("script, style, noscript"):
            node.decompose()
        images = []
        for img in body.select("img[src]"):
            url = urljoin(ref.url, img["src"])
            if urlsplit(url).scheme in ("http", "https"):
                images.append({"url": url, "alt": safe_terminal(img.get("alt", ""))})
        attachments = []
        for link in soup.select(source.attachment_selector):
            url = urljoin(ref.url, link["href"])
            if urlsplit(url).scheme in ("http", "https"):
                attachments.append({"url": url, "name": normalized_text(link)})
        text = normalized_text(body)
        if not text and not images and not attachments:
            raise ValueError("본문·이미지·첨부가 모두 비어 있습니다")
        return Notice(source.id, ref.external_id, ref.url, normalized_text(title),
                      ref.published_at, text, images, attachments)
