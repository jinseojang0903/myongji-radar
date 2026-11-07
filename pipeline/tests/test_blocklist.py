"""수동 차단 목록 읽기(#65, `exporter/blocklist.py`) 시험."""
import tempfile
import unittest
from pathlib import Path

from exporter.blocklist import MAX_REASON_CHARS, BlocklistError, load_blocked

ROOT = Path(__file__).resolve().parents[1]
REASON = "결과 공지가 대회 카드로 잘못 뽑힘"
ENTRY = f'card_id = "mju-ctl-ctl-792-235972"\nreason = "{REASON}"\nsince = 2026-11-06\n'


def entry(**changes) -> str:
    """ENTRY의 줄을 바꾼 칸 하나 (값은 TOML 표기 그대로)."""
    lines = dict(line.split(" = ", 1) for line in ENTRY.splitlines())
    lines.update(changes)
    return "[[blocked]]\n" + "".join(f"{key} = {value}\n" for key, value in lines.items())


class BlocklistTests(unittest.TestCase):
    def load_bytes(self, content: bytes) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blocked.toml"
            path.write_bytes(content)
            return load_blocked(path)

    def load(self, text: str) -> dict:
        return self.load_bytes(text.encode("utf-8"))

    def test_repository_file_is_valid(self):
        # 비어 있는지는 보지 않는다: 실제로 카드를 내리는 PR이 이 시험을 깨면 안 된다
        self.assertIsInstance(load_blocked(ROOT / "config" / "blocked_cards.toml"), dict)

    def test_entries(self):
        blocked = self.load(entry() + entry(card_id='"mju-ctl-ctl-792-235973"'))
        self.assertEqual(blocked["mju-ctl-ctl-792-235972"], {"reason": REASON, "since": "2026-11-06"})
        self.assertEqual(len(blocked), 2)
        self.assertEqual(self.load("blocked = []\n"), {})

    def test_boundaries_that_pass(self):
        self.assertIn("a" * 64, self.load(entry(card_id=f'"{"a" * 64}"')), "64자 id")
        reason = self.load(entry(reason=f'"{"가" * MAX_REASON_CHARS}"'))["mju-ctl-ctl-792-235972"]["reason"]
        self.assertEqual(len(reason), MAX_REASON_CHARS)
        self.assertEqual(self.load(entry(reason='"  앞뒤 공백  "'))["mju-ctl-ctl-792-235972"]["reason"], "앞뒤 공백")
        self.assertEqual(len(self.load_bytes("﻿".encode("utf-8") + entry().encode("utf-8"))), 1, "BOM(메모장)도 받는다")

    def test_any_format_error_stops(self):
        cases = {
            "문법 오류": "blocked = [",
            "blocked 없음": "",
            "모르는 최상위 키": "blocked = []\nblock = []\n",
            "목록이 아님": 'blocked = "x"\n',
            "칸이 표가 아님": "blocked = [1]\n",
            "빠진 키": '[[blocked]]\ncard_id = "a"\nsince = 2026-11-06\n',
            "오타 키": "[[blocked]]\n" + ENTRY.replace("card_id", "card-id"),
            "더 붙은 키": "[[blocked]]\n" + ENTRY + 'by = "장진서"\n',
            "id가 문자열 아님": entry(card_id="1"),
            "id 형식": entry(card_id='"MJU_ctl"'),
            "65자 id": entry(card_id=f'"{"a" * 65}"'),
            "id 끝 줄바꿈": entry(card_id='"abc\\n"'),
            "이유가 문자열 아님": entry(reason="1"),
            "빈 이유": entry(reason='"  "'),
            "긴 이유": entry(reason=f'"{"가" * (MAX_REASON_CHARS + 1)}"'),
            "여러 줄 이유": entry(reason='"""첫 줄\n둘째 줄"""'),
            "이유에 \\r": entry(reason='"첫 줄\\r둘째"'),
            "이유에 탭": entry(reason='"가\\t나"'),
            "이유에 글 방향 뒤집기": entry(reason='"가\\u202e나"'),
            "이유에 줄 구분자": entry(reason='"가\\u2028나"'),
            "이유에 NUL": entry(reason='"가\\u0000나"'),
            "문자열 날짜": entry(since='"2026-11-06"'),
            "시각 붙은 날짜": entry(since="2026-11-06T09:00:00"),
            "시간대 붙은 날짜": entry(since="2026-11-06T09:00:00Z"),
            "시각만": entry(since="09:00:00"),
            "같은 id 두 번": entry() + entry(),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(BlocklistError):
                self.load(text)
        with self.assertRaises(BlocklistError):
            self.load_bytes(entry().encode("cp949"))  # UTF-8이 아닌 파일

    def test_error_messages_do_not_echo_the_file(self):
        for text in (entry(card_id='"BAD ID"'), entry(reason=f'"{REASON}'), entry(since=f'"{REASON}"')):
            with self.subTest(text[:40]), self.assertRaises(BlocklistError) as caught:
                self.load(text)
            self.assertNotIn("결과 공지", str(caught.exception), "이유 문구·파일 내용은 메시지에 넣지 않는다")
        with self.assertRaises(BlocklistError) as caught:
            self.load(entry(card_id='"BAD ID"'))
        self.assertIn("1번 칸", str(caught.exception))

    def test_missing_file_is_an_error(self):
        with self.assertRaises(OSError):
            load_blocked(Path(tempfile.gettempdir()) / "없는-차단-목록.toml")


if __name__ == "__main__":
    unittest.main()
