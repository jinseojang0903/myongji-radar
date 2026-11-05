"""실제 파일 바이트와 변환 작업자로 수집→해시→버전까지 검증한다. 모두 합성 데이터다."""
import contextlib
import io
import json
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from collector.__main__ import main
from collector.assets import AssetCollector, convert_document, private_path
from collector.document_text import hwp_records, hwpx_text
from collector.fetcher import Fetcher
from collector.models import Notice
from collector.repository import Repository, content_hash


def hwpx(text="접수 마감 2026.11.19"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("Contents/section0.xml", date_time=(2026, 1, 1, 0, 0, 0))
        archive.writestr(info, f'<section xmlns:h="urn:test"><h:t>{text}</h:t></section>')
    return buffer.getvalue()


def pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Deadline 2026-11-19) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def hwp(compressed=False):
    """외부 파일 없이 만든 HWP 5 OLE 문서: FileHeader와 BodyText/Section0만 포함."""
    free, end = 0xFFFFFFFF, 0xFFFFFFFE
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHH", header, 24, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 16, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 17, *([free] * 108))
    file_header = bytearray(4096)
    file_header[:17] = b"HWP Document File"
    file_header[35] = 5
    paragraph = "접수 마감 2026.11.19\r".encode("utf-16-le")
    body = struct.pack("<I", 67 | len(paragraph) << 20) + paragraph
    padding = 4096 - len(body) - 4
    body += struct.pack("<I", padding << 20) + bytes(padding)
    if compressed:
        struct.pack_into("<I", file_header, 36, 1)
        compressor = zlib.compressobj(wbits=-15)
        body = (compressor.compress(body) + compressor.flush()).ljust(4096, b"\0")

    def entry(name, kind, start=end, size=0, right=free, child=free):
        data = bytearray(128)
        encoded = (name + "\0").encode("utf-16-le")
        data[:len(encoded)] = encoded
        struct.pack_into("<HBBIII", data, 64, len(encoded), kind, 1, free, right, child)
        struct.pack_into("<IQ", data, 116, start, size)
        return data

    directory = (entry("Root Entry", 5, child=1) + entry("FileHeader", 2, 0, 4096, right=2)
                 + entry("BodyText", 1, child=3) + entry("Section0", 2, 8, 4096))
    fat = list(range(1, 8)) + [end] + list(range(9, 16)) + [end, end, 0xFFFFFFFD] + [free] * 110
    return bytes(header + file_header + body + directory + struct.pack("<128I", *fat))


class AssetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name) / "data"
        self.notice = Notice("test", "test/1/1", "https://www.mju.ac.kr/post", "모집", None, "본문",
                             attachments=[{"url": "https://www.mju.ac.kr/file", "name": "../../evil.hwpx"}])

    def test_actual_pdf_hwp_hwpx_workers(self):
        for content, expected in ((pdf(), "Deadline 2026-11-19"), (hwp(), "접수 마감 2026.11.19"),
                                  (hwp(compressed=True), "접수 마감 2026.11.19"),
                                  (hwpx(), "접수 마감 2026.11.19")):
            with self.subTest(expected=expected, magic=content[:4]):
                collector = AssetCollector(self.data, Mock(get=Mock(return_value=content)))
                notice = replace(self.notice, attachments=[dict(self.notice.attachments[0])])
                self.assertEqual(collector.enrich(notice, ["www.mju.ac.kr"]), [])
                item = notice.attachments[0]
                self.assertEqual(item["text_status"], "extracted")
                self.assertIn(expected, (self.data / item["text_path"]).read_text(encoding="utf-8"))
                self.assertEqual((self.data / item["local_path"]).read_bytes(), content)
                self.assertNotIn("evil", item["local_path"])

    def test_same_url_file_change_and_transient_failure(self):
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        versions = []
        for response, expected in ((hwpx("첫 공지"), "new"), (hwpx("첫 공지"), "unchanged"),
                                   (ValueError("network"), "unchanged"), (hwpx("마감 연장"), "changed")):
            notice = replace(self.notice, attachments=[dict(self.notice.attachments[0])])
            fetcher = Mock()
            fetcher.get.side_effect = response if isinstance(response, Exception) else None
            fetcher.get.return_value = response
            collector = AssetCollector(self.data, fetcher)
            errors = collector.enrich(notice, ["www.mju.ac.kr"], repo.latest_payload("test", "test/1/1"))
            self.assertEqual(bool(errors), isinstance(response, Exception))
            self.assertEqual(repo.save(notice, b"same html"), expected)
            versions.append(repo.db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0])
            if errors:
                self.assertEqual(repo.latest_payload("test", "test/1/1")["attachments"][0]["download_status"], "failed")
        self.assertEqual(versions, [1, 1, 1, 2])
        for payload, in repo.db.execute("SELECT payload_json FROM notice_versions"):
            path = json.loads(payload)["attachments"][0]["local_path"]
            self.assertTrue((self.data / path).is_file())

    def test_parser_upgrade_retains_original_file_hash(self):
        repo = Repository(self.data)
        self.addCleanup(repo.close)
        old = replace(self.notice, attachments=[dict(self.notice.attachments[0])])
        AssetCollector(self.data, Mock(get=Mock(return_value=hwpx()))).enrich(old, ["www.mju.ac.kr"])
        repo.save(old, b"same html")
        upgraded = replace(self.notice, parser_version="k2web-v2", attachments=[dict(self.notice.attachments[0])])
        AssetCollector(self.data, Mock(get=Mock(return_value=hwpx()))).enrich(upgraded, ["www.mju.ac.kr"])
        self.assertEqual(repo.save(upgraded, b"same html", lambda raw, p: replace(self.notice, parser_version="k2web-v2")), "unchanged")

    def test_external_url_does_not_reach_network(self):
        fetcher = Fetcher(interval=0)
        self.addCleanup(fetcher.close)
        self.notice.attachments[0]["url"] = "https://evil.example/file"
        with patch.object(fetcher.session, "get") as get:
            errors = AssetCollector(self.data, fetcher).enrich(self.notice, ["www.mju.ac.kr"])
        get.assert_not_called()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.notice.attachments[0]["download_status"], "failed")

    def test_image_unknown_and_scanned_pdf_require_review(self):
        writer = PdfWriter()
        writer.add_blank_page(width=50, height=50)
        buffer = io.BytesIO()
        writer.write(buffer)
        for kind, content in (("images", b"image"), ("attachments", b"<html>login</html>"),
                              ("attachments", buffer.getvalue())):
            notice = replace(self.notice, attachments=[], images=[])
            getattr(notice, kind).append({"url": "https://www.mju.ac.kr/file", "name": "test"})
            self.assertEqual(AssetCollector(self.data, Mock(get=Mock(return_value=content))).enrich(notice, ["www.mju.ac.kr"]), [])
            self.assertEqual(getattr(notice, kind)[0]["text_status"], "needs_review")

    def test_timeout_oversize_count_and_xml_bomb(self):
        path = self.data / "test.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        with patch("collector.assets.subprocess.run", side_effect=subprocess.TimeoutExpired("test", 15)):
            self.assertEqual(convert_document(path, self.data), ("", "conversion_timeout"))
        fetcher = Mock(get=Mock(return_value=b"x" * (5 * 1024 * 1024 + 1)))
        self.assertEqual(len(AssetCollector(self.data, fetcher).enrich(self.notice, ["www.mju.ac.kr"])), 1)
        self.assertFalse((self.data / "assets").exists())
        self.notice.attachments = [{"url": f"https://www.mju.ac.kr/{i}", "name": "file"} for i in range(21)]
        fetcher = Mock(get=Mock(return_value=b"x"))
        with patch("collector.assets.convert_document", return_value=("", "unsupported")):
            errors = AssetCollector(self.data, fetcher).enrich(self.notice, ["www.mju.ac.kr"])
        self.assertEqual(fetcher.get.call_count, 20)
        self.assertEqual(len(errors), 1)
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Contents/section0.xml", " " * (10 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            hwpx_text(bomb.getvalue())
        with self.assertRaises(ValueError):
            hwp_records(struct.pack("<I", 67 | 100 << 20))

    def test_runtime_metadata_not_content_change(self):
        from dataclasses import asdict
        before = asdict(self.notice)
        after = asdict(self.notice)
        after["attachments"][0].update(local_path="different", text_status="needs_review", download_error="Timeout")
        self.assertEqual(content_hash(before), content_hash(after))
        after["attachments"][0]["sha256"] = "abc"
        self.assertNotEqual(content_hash(before), content_hash(after))

    def test_total_bytes_and_private_path_limits(self):
        self.notice.images = [{"url": f"https://www.mju.ac.kr/{i}", "alt": ""} for i in range(5)]
        self.notice.attachments = []
        fetcher = Mock(get=Mock(return_value=b"x" * (5 * 1024 * 1024)))
        errors = AssetCollector(self.data, fetcher).enrich(self.notice, ["www.mju.ac.kr"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.notice.images[-1]["download_status"], "failed")
        with self.assertRaises(ValueError):
            private_path(self.data, "../escaped.bin")
        xml = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE s [<!ENTITY x "bad">]><s>&x;</s>'
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("Contents/section0.xml", xml.encode("utf-16"))
        with self.assertRaises(ValueError):
            hwpx_text(archive_bytes.getvalue())

    def test_cli_asset_failure_preserves_body_and_other_file(self):
        self.data.mkdir(parents=True)
        config = self.data.parent / "sources.toml"
        config.write_text('''[defaults]
adapter="k2web"
title_selector="h1"
body_selector=".body"
attachment_selector=".files a[href]"
[[sources]]
id="test"
name="test"
rss_url="https://www.mju.ac.kr/rss"
allowed_hosts=["www.mju.ac.kr"]
''', encoding="utf-8")
        rss = b'<rss><channel><item><title>test</title><link>https://www.mju.ac.kr/bbs/mju/1/1/artclView.do</link></item></channel></rss>'
        html = b'<h1>title</h1><div class="body">body</div><div class="files"><a href="/bad">bad</a><a href="/good">good</a></div>'
        fetcher = Mock()
        fetcher.get.side_effect = [rss, html, ValueError("bad file"), hwpx()]
        with patch("collector.__main__.Fetcher", return_value=fetcher), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--config", str(config), "--data-dir", str(self.data)]), 1)
        notices = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"]
        self.assertEqual(notices[0]["body_text"], "body")
        self.assertEqual(notices[0]["attachments"][0]["download_status"], "failed")
        self.assertEqual(notices[0]["attachments"][1]["text_status"], "extracted")


if __name__ == "__main__":
    unittest.main()
