"""다운로드 문서를 읽는 제한 시간 적용 대상 작업자. 네트워크·외부 실행·OCR 없음."""
import io
import logging
import re
import struct
import sys
import unicodedata
import warnings
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_EXPANDED = 10 * 1024 * 1024
MAX_CHARS = 200_000
READ_CHUNK = 64 * 1024


def xml_text(xml):
    """HWPX 본문은 UTF-8/16만 허용하고 파서에 넘기기 전에 선언을 검사한다."""
    if xml.startswith((b"\xff\xfe\0\0", b"\0\0\xfe\xff", b"<\0\0\0", b"\0\0\0<")):
        raise ValueError("지원하지 않는 XML 인코딩")
    if xml.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = xml.decode("utf-16")
        actual = "utf16"
    elif xml.startswith(b"<\0"):
        text, actual = xml.decode("utf-16-le"), "utf16"
    elif xml.startswith(b"\0<"):
        text, actual = xml.decode("utf-16-be"), "utf16"
    else:
        text, actual = xml.decode("utf-8-sig"), "utf8"
    declaration = re.match(r"\s*<\?xml\s+([^?]*)\?>", text, re.I)
    if declaration:
        match = re.search(r"\bencoding\s*=\s*(['\"])(.*?)\1", declaration[1], re.I)
        if match:
            encoding = match[2].lower().replace("-", "")
            if (encoding not in ("utf8", "utf16", "utf16le", "utf16be")
                    or (actual == "utf8") != (encoding == "utf8")):
                raise ValueError("지원하지 않는 XML 인코딩")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML 엔티티 금지")
    return text


def clean_text(text):
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def hwpx_text(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sections = [i for i in archive.infolist() if re.fullmatch(r"Contents/section\d+\.xml", i.filename)]
        if not sections or len(sections) > 100 or sum(i.file_size for i in sections) > MAX_EXPANDED:
            raise ValueError("HWPX 본문 크기/개수 상한")
        if any(i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for i in sections):
            raise ValueError("지원하지 않는 HWPX 압축 방식")
        text, expanded, text_length = [], 0, 0
        for info in sorted(sections, key=lambda i: int(re.search(r"\d+", i.filename)[0])):
            # file_size는 공격자가 바꿀 수 있다. 제한된 크기로 해제하면서 실제 바이트도 센다.
            xml = bytearray()
            with archive.open(info) as stream:
                while chunk := stream.read(min(READ_CHUNK, MAX_EXPANDED - expanded + 1)):
                    expanded += len(chunk)
                    if expanded > MAX_EXPANDED:
                        raise ValueError("HWPX 실제 본문 크기 상한")
                    xml.extend(chunk)
            if len(xml) != info.file_size:
                raise ValueError("HWPX 본문 크기 불일치")
            root = ET.fromstring(xml_text(bytes(xml)))
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] != "t":
                    continue
                parts, length = [], 0
                separator = 1 if text else 0
                for part in node.itertext():
                    length += len(part)
                    if text_length + separator + length > MAX_CHARS:
                        raise ValueError("텍스트 크기 상한")
                    parts.append(part)
                if text_length + separator + length > MAX_CHARS:
                    raise ValueError("텍스트 크기 상한")
                text.append("".join(parts))
                text_length += separator + length
        return "\n".join(text)


def hwp_paragraph(data):
    # HWP 5의 확장·인라인 제어 문자는 8개 UTF-16 코드 단위를 차지한다.
    units = struct.unpack(f"<{len(data) // 2}H", data) if len(data) % 2 == 0 else ()
    out, index = bytearray(), 0
    extended = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
    while index < len(units):
        code = units[index]
        if code in extended:
            if index + 8 > len(units):
                raise ValueError("잘린 HWP 제어 문자")
            out.extend(b" \x00")
            index += 8
        else:
            if code in (10, 13):
                out.extend(b"\n\x00")
            elif code >= 32:
                out.extend(struct.pack("<H", code))
            index += 1
    return out.decode("utf-16-le")


def hwp_records(data):
    offset, text = 0, []
    while offset < len(data):
        if len(data) - offset < 4:
            raise ValueError("잘린 HWP 레코드")
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag, size = header & 1023, header >> 20
        if size == 4095:
            if len(data) - offset < 4:
                raise ValueError("잘린 HWP 크기")
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        if size > len(data) - offset:
            raise ValueError("잘린 HWP 본문")
        if tag == 67:  # HWPTAG_BEGIN(16) + 51 = HWPTAG_PARA_TEXT
            text.append(hwp_paragraph(data[offset:offset + size]))
        offset += size
    return "\n".join(text)


def hwp_text(content):
    import olefile

    with olefile.OleFileIO(io.BytesIO(content)) as document:
        header = document.openstream("FileHeader").read(256)
        if len(header) < 40 or not header.startswith(b"HWP Document File") or header[35] != 5:
            raise ValueError("HWP 5 문서 아님")
        flags = struct.unpack_from("<I", header, 36)[0]
        if flags & (2 | 4):
            raise ValueError("암호/배포용 HWP는 지원하지 않음")
        sections = [p for p in document.listdir() if len(p) == 2 and p[0] == "BodyText" and re.fullmatch(r"Section\d+", p[1])]
        if not sections or len(sections) > 100:
            raise ValueError("HWP 본문 없음/상한")
        text, total = [], 0
        for path in sorted(sections, key=lambda p: int(p[1][7:])):
            if document.get_size(path) > MAX_EXPANDED - total:
                raise ValueError("HWP 크기 상한")
            data = document.openstream(path).read()
            if flags & 1:
                inflater = zlib.decompressobj(-15)
                data = inflater.decompress(data, MAX_EXPANDED - total + 1)
                if not inflater.eof or inflater.unconsumed_tail:
                    raise ValueError("HWP 압축 크기 상한/손상")
            total += len(data)
            if total > MAX_EXPANDED:
                raise ValueError("HWP 크기 상한")
            text.append(hwp_records(data))
        return "\n".join(text)


def extract_text(content):
    if content.startswith(b"%PDF-"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("암호 PDF 또는 100쪽 상한")
        result, length = [], 0
        for page in reader.pages:
            text = page.extract_text() or ""
            length += len(text)
            if length > MAX_CHARS:
                raise ValueError("텍스트 크기 상한")
            result.append(text)
        return "\n".join(result)
    if content.startswith(b"PK\x03\x04"):
        return hwpx_text(content)
    if content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return hwp_text(content)
    raise ValueError("지원하지 않는 형식")


if __name__ == "__main__":
    # 라이브러리 경고·진단에는 외부 파일의 문자열이 섞일 수 있다.
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    try:
        path = Path(sys.argv[1])
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("파일 크기 상한")
        text = clean_text(extract_text(path.read_bytes()))
        if len(text) > MAX_CHARS:
            raise ValueError("텍스트 크기 상한")
        sys.stdout.buffer.write(text.encode("utf-8"))
    except Exception as exc:
        # 부모는 이 고정된 예외 종류만 기록한다. 메시지·본문·파일명은 출력하지 않는다.
        sys.stderr.write(type(exc).__name__ + "\n")
        raise SystemExit(1)
