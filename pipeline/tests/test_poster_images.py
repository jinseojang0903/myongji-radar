"""포스터 이미지 입력(#14) 시험. 실제 claude는 부르지 않는다: 이미지를 고르는 규칙, 표준 입력(stream-json) 모양,
확인 호출, 공개 설정을 본다. 매일 추출에서 켜고 끌 때는 test_daily_extract. (unittest discover -s tests로 돌린다)"""
import base64
import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from extractor import claude as claude_cli
from extractor.images import MAX_IMAGES, MAX_IMAGE_BYTES, PosterImage, image_refs, media_type_of, poster_images
from test_daily_extract import PNG, stdin_message, store
from test_extractor import FakeRun, stream

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body"


class ImageSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_verified_image_files_from_the_asset_folder(self):
        changed = store(self.folder, PNG + b"changed")
        changed["sha256"] = hashlib.sha256(b"other").hexdigest()  # 파일이 수집 기록과 다르다
        outside = dict(store(self.folder, PNG), local_path="../secret.bin")
        notice = {"images": [
            store(self.folder, PNG),                                       # 1 넣음
            store(self.folder, b"%PDF-1.7 not an image"),                  # 2 모르는 형식
            changed,                                                       # 3 바뀐 파일
            outside,                                                       # 4 data 밖 경로 모양
            dict(store(self.folder, JPEG), download_status="failed"),      # 5 내려받지 못함
            dict(store(self.folder, JPEG), size_bytes=MAX_IMAGE_BYTES + 1),  # 6 너무 큼
            "모양이 다른 기록",                                             # 7
            store(self.folder, JPEG),                                      # 8 넣음
        ]}
        images = poster_images(notice, self.folder)
        self.assertEqual([(image.name, image.media_type) for image in images],
                         [("이미지 1.png", "image/png"), ("이미지 8.jpg", "image/jpeg")],
                         "이름은 공지 안 순서로 매긴다 (원래 파일 이름·alt는 공지가 정하는 글이라 쓰지 않는다)")
        self.assertEqual(images[0].data, PNG)

    def test_at_most_max_images_and_format_sniffing(self):
        notice = {"images": [store(self.folder, PNG + bytes([index])) for index in range(MAX_IMAGES + 2)]}
        self.assertEqual(len(image_refs(notice)), MAX_IMAGES)
        self.assertEqual(len(poster_images(notice, self.folder)), MAX_IMAGES)
        self.assertEqual(media_type_of(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ("image/webp", "webp"))
        self.assertEqual(media_type_of(b"GIF89a..."), ("image/gif", "gif"))
        self.assertIsNone(media_type_of(b"BM bitmap"))
        self.assertEqual(image_refs({}), [])

    def test_probe_image_is_a_real_png(self):
        self.assertEqual(media_type_of(PNG), ("image/png", "png"))
        idat = PNG.index(b"IDAT")
        length = int.from_bytes(PNG[idat - 4:idat], "big")
        self.assertEqual(zlib.decompress(PNG[idat + 4:idat + 4 + length]), b"\x00\xff\xff\xff", "1×1 흰 점 한 줄")


class StreamInputTests(unittest.TestCase):
    def call(self, fake, **kwargs):
        with mock.patch.object(claude_cli.subprocess, "run", fake):
            return claude_cli.call("공지 글\n<image name=\"이미지 1.png\" />", "claude.exe", "haiku", timeout=12, **kwargs)

    def test_images_go_on_stdin_with_every_tool_still_off(self):
        fake = FakeRun()
        self.call(fake, images=[PosterImage("이미지 1.png", "image/png", PNG)])
        sent = fake.calls[0]
        command = sent["command"]
        self.assertEqual(command[command.index("--input-format") + 1], "stream-json")
        self.assertEqual(command[command.index("--tools") + 1], "", "이미지를 넣어도 도구는 모두 끈다")
        self.assertEqual(command[command.index("--disallowedTools") + 1], "mcp__*")
        self.assertEqual(sent["cwd_files"], [], "이미지를 파일로 두지 않는다 (빈 임시 폴더)")
        message = stdin_message(sent)
        self.assertEqual((message["type"], message["message"]["role"]), ("user", "user"))
        content = message["message"]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "공지 글\n<image name=\"이미지 1.png\" />"}, "줄바꿈 그대로")
        self.assertEqual(content[1], {"type": "text", "text": '<image name="이미지 1.png" />'}, "이미지 앞에 이름")
        self.assertEqual(content[2]["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(content[2]["source"]["data"]), PNG)

    def test_without_images_stdin_stays_plain_text(self):
        fake = FakeRun()
        self.call(fake)
        self.assertNotIn("--input-format", fake.calls[0]["command"])
        self.assertTrue(fake.calls[0]["input"].startswith("공지 글".encode("utf-8")))

    def test_probe_with_image_checks_the_input_format_and_isolation(self):
        fake = FakeRun()
        with mock.patch.object(claude_cli.subprocess, "run", fake):
            claude_cli.probe("claude.exe", "haiku", with_image=True)
        content = stdin_message(fake.calls[0])["message"]["content"]
        self.assertEqual(base64.b64decode(content[2]["source"]["data"]), PNG, "시험 이미지만 보낸다 (공지 없음)")
        for broken, expected in ((FakeRun(stream(is_error=True)), "이미지 입력 확인 실패"),
                                 (FakeRun(stream(init={"type": "system", "subtype": "init", "tools": ["Read"],
                                                       "mcp_servers": []})), "격리 확인 실패")):
            with self.subTest(expected), mock.patch.object(claude_cli.subprocess, "run", broken), \
                    self.assertRaisesRegex(claude_cli.ClaudeError, expected):
                claude_cli.probe("claude.exe", "haiku", with_image=True)


if __name__ == "__main__":
    unittest.main()
