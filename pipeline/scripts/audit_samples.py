"""저장된 상세 HTML과 파싱 결과의 구조적 일치를 오프라인으로 점검한다.

신청 조건·날짜의 의미 정확도나 전체 공지 누락률을 측정하는 도구가 아니다.
"""
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector.config import load_sources


def compact(text):
    return re.sub(r"\s+", "", text)


def audit():
    sources = load_sources(ROOT / "config/sources.toml")
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "HTML structural consistency; not semantic accuracy", "sources": []}
    with closing(sqlite3.connect(ROOT / "data/collector.sqlite3")) as db:
        for source in sources:
            if source.priority > 5:
                continue
            rows = db.execute("""SELECT payload_json,raw_path FROM notice_versions v
                WHERE source_id=? AND id=(SELECT MAX(id) FROM notice_versions v2
                    WHERE v2.source_id=v.source_id AND v2.external_id=v.external_id)
                ORDER BY id DESC LIMIT 5""", (source.id,)).fetchall()
            samples = []
            for payload, raw_path in rows:
                notice = json.loads(payload)
                soup = BeautifulSoup((ROOT / "data" / raw_path).read_bytes(), "html.parser", from_encoding="utf-8")
                # 비교 기준은 저장 원문의 제목/본문 영역과 페이지 전체 다운로드 링크.
                heading = soup.find(class_="artclViewTitle")
                body = soup.find(class_="artclView")
                if body:
                    for node in body.find_all(["script", "style", "noscript"]):
                        node.decompose()
                image_urls = [urljoin(notice["url"], img["src"]) for img in body.find_all("img", src=True)] if body else []
                file_urls = [urljoin(notice["url"], a["href"]) for a in soup.find_all("a", href=True)
                             if "/download.do" in a["href"]]
                checks = {
                    "title_matches_html": heading is not None and compact(notice["title"]) == compact(heading.get_text()),
                    "body_text_matches_html": body is not None and compact(notice["body_text"]) == compact(body.get_text()),
                    "image_urls_match_html": [img["url"] for img in notice["images"]] == image_urls,
                    "attachment_urls_match_page_downloads": [a["url"] for a in notice["attachments"]] == file_urls,
                }
                samples.append({"external_id": notice["external_id"], "title": notice["title"], "url": notice["url"],
                                "raw_path": raw_path, "body_chars": len(notice["body_text"]),
                                "images": len(image_urls), "attachments": len(file_urls), "checks": checks})
            result["sources"].append({"id": source.id, "name": source.name, "priority": source.priority,
                "discovery": source.discovery, "sample_count": len(samples),
                "passed_checks": sum(sum(s["checks"].values()) for s in samples),
                "total_checks": len(samples) * 4,
                "text_available": sum(s["body_chars"] > 0 for s in samples),
                "images": sum(s["images"] for s in samples),
                "attachments": sum(s["attachments"] for s in samples), "samples": samples})
    output = ROOT / "data/audit/structural_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for source in result["sources"]:
        print(f"{source['id']}: {source['sample_count']} samples, {source['passed_checks']}/{source['total_checks']} checks, text {source['text_available']}/{source['sample_count']}, images {source['images']}, attachments {source['attachments']}")
    print(output)
    return 0 if all(s["sample_count"] == 5 and s["passed_checks"] == s["total_checks"] for s in result["sources"]) else 1


if __name__ == "__main__":
    raise SystemExit(audit())
