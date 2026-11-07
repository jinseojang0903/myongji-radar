"""외부 첨부를 쓰지 않는 HWPX 압축·XML 선언·작업자 진단 회귀 시험."""
import io
import struct
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from collector import document_text


def archive_xml(xml, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("Contents/section0.xml", xml)
    return buffer.getvalue()


def forged_large_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("Contents/section0.xml", "w") as stream:
            for _ in range(64):
                stream.write(b"x" * 1024 * 1024)
    data = bytearray(buffer.getvalue())
    central = data.index(b"PK\x01\x02")
    # 실제 64MiB를 100바이트라고 속인 ZIP. CRC는 실제 내용의 것으로 둔다.
    struct.pack_into("<I", data, 22, 100)
    struct.pack_into("<I", data, central + 24, 100)
    return bytes(data)


class DocumentLimitTests(unittest.TestCase):
    def test_forged_size_is_rejected_without_expanding_whole_stream(self):
        content = forged_large_zip()
        self.assertLess(len(content), 100_000)
        tracemalloc.start()
        try:
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                document_text.hwpx_text(content)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * 1024 * 1024, "거짓 file_size 때문에 전체 64MiB를 먼저 풀면 안 됨")

    def test_only_stored_and_deflated_sections_are_allowed(self):
        xml = b"<section><t>deadline</t></section>"
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            self.assertEqual(document_text.hwpx_text(archive_xml(xml, compression)), "deadline")
        for compression in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(compression=compression), self.assertRaisesRegex(ValueError, "압축 방식"):
                document_text.hwpx_text(archive_xml(xml, compression))

    def test_actual_stream_total_is_checked_independently_of_metadata(self):
        info = zipfile.ZipInfo("Contents/section0.xml")
        info.file_size = 5
        info.compress_type = zipfile.ZIP_STORED

        class Archive:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def infolist(self):
                return [info]

            def open(self, entry):
                return io.BytesIO(b"x" * 101)

        with patch.object(document_text.zipfile, "ZipFile", return_value=Archive()), \
                patch.object(document_text, "MAX_EXPANDED", 100):
            with self.assertRaisesRegex(ValueError, "실제 본문 크기 상한"):
                document_text.hwpx_text(b"synthetic")

    def test_utf8_utf16_work_and_other_encodings_or_dtd_are_rejected(self):
        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            xml = f'<?xml version="1.0" encoding="{encoding}"?><section><t>마감</t></section>'
            self.assertEqual(document_text.hwpx_text(archive_xml(xml.encode(encoding))), "마감")
        cp037 = '<?xml version="1.0" encoding="cp037"?><!DOCTYPE s [<!ENTITY x "secret">]><s>&x;</s>'
        for xml in (cp037.encode("cp037"), cp037.encode("ascii"),
                    '<section><t>text</t></section>'.encode("utf-32"),
                    '<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x "secret">]><s>&x;</s>'.encode("utf-16")):
            with self.subTest(prefix=xml[:8]), self.assertRaises((ValueError, UnicodeError)):
                document_text.hwpx_text(archive_xml(xml))

    def test_worker_failure_diagnostic_has_no_source_or_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private-name.bin"
            path.write_bytes(b"private body content")
            worker = Path(document_text.__file__).resolve()
            result = subprocess.run([sys.executable, "-I", str(worker), str(path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr.strip(), b"ValueError")

    def test_nested_text_elements_cannot_amplify_output_beyond_limit(self):
        xml = ("<section>" + "<t>" * 100 + "x" * 3000 + "</t>" * 100 + "</section>").encode("utf-8")
        self.assertLess(len(xml), 4000)
        with self.assertRaisesRegex(ValueError, "텍스트 크기 상한"):
            document_text.hwpx_text(archive_xml(xml))


if __name__ == "__main__":
    unittest.main()
