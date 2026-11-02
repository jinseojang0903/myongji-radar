import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import Notice, NoticeRef, Source


def normalized_text(node) -> str:
    return "\n".join(
        line for part in node.get_text("\n").splitlines()
        if (line := " ".join(part.split()))
    )


class K2WebAdapter:
    def discover(self, xml: bytes, source: Source) -> list[NoticeRef]:
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
            link = (item.findtext("link") or "").strip()
            if not link:
                raise ValueError("RSS 항목 링크 없음: RSS 미지원 여부 확인 후 HTML 발견 설정 필요")
            url = urljoin(source.rss_url, link)
            match = re.search(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do$", urlsplit(url).path)
            if not match:
                raise ValueError(f"지원하지 않는 게시글 URL: {url}")
            raw_date = (item.findtext("pubDate") or "").strip()
            published_at = None
            if raw_date:
                # K2Web RSS 날짜에 시간대가 없으면 학교 현지 시각을 적용.
                date = datetime.fromisoformat(raw_date)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone(timedelta(hours=9)))
                published_at = date.isoformat()
            external_id = "/".join(match.groups())
            refs[external_id] = NoticeRef(external_id, url, (item.findtext("title") or "").strip(), published_at)
        if not refs:
            raise ValueError("RSS 항목 0건: 빈 게시판 또는 구조 변경 확인 필요")
        return list(refs.values())

    def discover_html(self, html: bytes, source: Source) -> list[NoticeRef]:
        """공개 목록 첫 페이지에서만 발견. 과거 페이지 순회는 아직 지원하지 않는다."""
        soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
        refs = {}
        for row in soup.select("tbody tr"):
            link = row.select_one("a.artclLinkView[href]")
            if link is None:
                continue
            url = urljoin(source.list_url, link["href"])
            match = re.search(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do$", urlsplit(url).path)
            if not match:
                raise ValueError(f"지원하지 않는 HTML 게시글 URL: {url}")
            title = link.select_one("strong") or link
            date = row.select_one("._artclTdRdate")
            published = normalized_text(date).replace(".", "-") if date else None
            if published:
                datetime.strptime(published, "%Y-%m-%d")
            external_id = "/".join(match.groups())
            refs[external_id] = NoticeRef(external_id, url, normalized_text(title), published)
        if not refs:
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
                images.append({"url": url, "alt": img.get("alt", "")})
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
