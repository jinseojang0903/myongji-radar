import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

from collector.adapters.k2web import K2WebAdapter
from collector.models import Notice, NoticeRef, Source
from collector.repository import Repository, content_hash


def legacy_hash(payload):
    semantic = {key: value for key, value in payload.items() if key not in ("url", "published_at")}
    return hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.notice = Notice("test", "test/1/1", "https://example.com/post", "제목", None, "본문")

    def test_parser_version_only_refreshes_payload_without_new_version(self):
        repo = Repository(self.data)
        try:
            self.assertEqual(repo.save(self.notice, b"original"), "new")
            old_row = repo.db.execute("SELECT id, raw_path, collected_at FROM notice_versions").fetchone()
            updated = replace(self.notice, parser_version="k2web-v2")
            self.assertEqual(repo.save(updated, b"views changed", lambda raw, payload: updated), "unchanged")
            rows = repo.db.execute("SELECT id, raw_path, collected_at, payload_json FROM notice_versions").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][:3], old_row)
            self.assertEqual(json.loads(rows[0][3])["parser_version"], "k2web-v2")
            repo.export(self.data / "notices.json")
            exported = json.loads((self.data / "notices.json").read_text(encoding="utf-8"))["notices"][0]
            self.assertEqual(exported["version_id"], old_row[0])
            self.assertEqual(exported["parser_version"], "k2web-v2")
        finally:
            repo.close()

    def test_only_four_content_fields_create_versions(self):
        for field, value in (("title", "새 제목"), ("body_text", "새 본문"),
                             ("images", [{"url": "https://example.com/new.png", "alt": ""}]),
                             ("attachments", [{"url": "https://example.com/file", "name": "file"}])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                repo = Repository(Path(temp))
                try:
                    self.assertEqual(repo.save(self.notice, b"old"), "new")
                    metadata_only = replace(self.notice, url="https://example.com/new-url", published_at="2026-11-04")
                    self.assertEqual(repo.save(metadata_only, b"different markup"), "unchanged")
                    self.assertEqual(repo.save(replace(metadata_only, **{field: value}), b"new"), "changed")
                    self.assertEqual(repo.db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0], 2)
                finally:
                    repo.close()

    def test_invalid_reparse_identity_or_parser_keeps_database_unchanged(self):
        for field, value in (("source_id", "other"), ("external_id", "test/1/other"),
                             ("parser_version", "wrong-parser")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                repo = Repository(Path(temp))
                try:
                    repo.save(self.notice, b"original")
                    before_versions = repo.db.execute("SELECT * FROM notice_versions").fetchall()
                    before_notices = repo.db.execute("SELECT * FROM notices").fetchall()
                    updated = replace(self.notice, parser_version="k2web-v2", body_text="new")
                    wrong_result = replace(updated, **{field: value})
                    with self.assertRaisesRegex(ValueError, "현재 공지 또는 파서와 다릅니다"):
                        repo.save(updated, b"new", lambda raw, payload: wrong_result)
                    self.assertEqual(repo.db.execute("SELECT * FROM notice_versions").fetchall(), before_versions)
                    self.assertEqual(repo.db.execute("SELECT * FROM notices").fetchall(), before_notices)
                    self.assertEqual(len(list((Path(temp) / "raw").glob("*.html"))), 1)
                finally:
                    repo.close()

    def test_two_parser_only_upgrades_preserve_one_original_version(self):
        repo = Repository(self.data)
        try:
            repo.save(self.notice, b"original")
            before = repo.db.execute("SELECT id, raw_path, collected_at FROM notice_versions").fetchone()
            for version in ("k2web-v2", "k2web-v3"):
                updated = replace(self.notice, parser_version=version)

                def reparse(raw, payload):
                    self.assertEqual(raw, b"original")
                    return replace(Notice(**payload), parser_version=version)

                self.assertEqual(repo.save(updated, b"view counter changed", reparse), "unchanged")
                rows = repo.db.execute("SELECT id, raw_path, collected_at, payload_json FROM notice_versions").fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][:3], before)
                self.assertEqual(json.loads(rows[0][3])["parser_version"], version)
        finally:
            repo.close()

    def test_reparse_distinguishes_parser_change_from_real_content_change(self):
        source = Source("test", "테스트", "k2web", "https://example.com/rss", ["example.com"],
                        ".title", ".body", ".file a[href]")
        ref = NoticeRef(self.notice.external_id, self.notice.url, "", None)
        adapter = K2WebAdapter()

        def html(body, views):
            return f'<h1 class="title">Title</h1><div class="body">{body}</div><span>{views}</span>'.encode()

        def parse(raw, version):
            notice = adapter.parse_detail(raw, ref, source)
            notice.parser_version = version
            if version == "v2":
                notice.body_text = notice.body_text.upper()
            elif version == "v3":
                notice.body_text = "새 파서: " + notice.body_text.upper()
            return notice

        repo = Repository(self.data)
        try:
            old_html = html("first", 1)
            self.assertEqual(repo.save(parse(old_html, "v1"), old_html), "new")
            first_id = repo.db.execute("SELECT id FROM notice_versions").fetchone()[0]
            same_content = html("first", 2)
            self.assertEqual(repo.save(parse(same_content, "v2"), same_content,
                                       lambda raw, payload: parse(raw, "v2")), "unchanged")
            changed_content = html("second", 3)
            self.assertEqual(repo.save(parse(changed_content, "v3"), changed_content,
                                       lambda raw, payload: parse(raw, "v3")), "changed")
            rows = repo.db.execute("SELECT id, content_hash, payload_json FROM notice_versions ORDER BY id").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], first_id)
            self.assertEqual([json.loads(row[2])["body_text"] for row in rows], ["새 파서: FIRST", "새 파서: SECOND"])
            for _, digest, payload in rows:
                self.assertEqual(digest, content_hash(json.loads(payload)))
            self.assertEqual(repo.db.execute("SELECT current_hash FROM notices").fetchone()[0], rows[-1][1])
        finally:
            repo.close()

    def test_reparse_failure_does_not_create_or_change_versions(self):
        repo = Repository(self.data)
        try:
            repo.save(self.notice, b"original")
            before = repo.db.execute("SELECT * FROM notice_versions").fetchall()
            before_notice = repo.db.execute("SELECT * FROM notices").fetchall()
            updated = replace(self.notice, body_text="파서가 바꾼 본문", parser_version="k2web-v2")
            with self.assertRaisesRegex(ValueError, "다시 파싱"):
                repo.save(updated, b"new")
            (self.data / before[0][5]).unlink()
            with self.assertRaises(FileNotFoundError):
                repo.save(updated, b"new", lambda raw, payload: updated)
            self.assertEqual(repo.db.execute("SELECT * FROM notice_versions").fetchall(), before)
            self.assertEqual(repo.db.execute("SELECT * FROM notices").fetchall(), before_notice)
        finally:
            repo.close()

    def test_equal_old_and_new_hashes_do_not_hide_change_during_parser_upgrade(self):
        source = Source("test", "테스트", "k2web", "https://example.com/rss", ["example.com"],
                        ".title", ".body", ".file a[href]")
        ref = NoticeRef(self.notice.external_id, self.notice.url, "", None)
        adapter = K2WebAdapter()
        old_html = b'<h1 class="title">Title</h1><div class="body">Body<span>Extra</span></div>'
        new_html = b'<h1 class="title">Title</h1><div class="body">Body</div>'

        def parse(raw, version):
            notice = adapter.parse_detail(raw, ref, source)
            notice.parser_version = version
            if version == "v1":
                notice.body_text = notice.body_text.split("\n")[0]
            return notice

        repo = Repository(self.data)
        try:
            old_notice = parse(old_html, "v1")
            new_notice = parse(new_html, "v2")
            self.assertEqual(content_hash(asdict(old_notice)), content_hash(asdict(new_notice)))
            self.assertEqual(repo.save(old_notice, old_html), "new")
            self.assertEqual(repo.save(new_notice, new_html, lambda raw, payload: parse(raw, "v2")), "changed")
            rows = repo.db.execute("SELECT payload_json FROM notice_versions ORDER BY id").fetchall()
            self.assertEqual([json.loads(row[0])["body_text"] for row in rows], ["Body\nExtra", "Body"])
        finally:
            repo.close()

    def seed_legacy_db(self):
        repo = Repository(self.data)
        try:
            repo.save(self.notice, b"first")
            repo.save(replace(self.notice, body_text="두 번째"), b"second")
            with repo.db:
                # 예전 방식에서 파서 버전만 달라 중복 생성된 버전도 유지한다.
                payload = json.dumps(asdict(replace(self.notice, body_text="두 번째", parser_version="k2web-v2")),
                                     ensure_ascii=False, sort_keys=True)
                repo.db.execute("""INSERT INTO notice_versions(source_id, external_id, content_hash,
                    payload_json, raw_path, collected_at)
                    SELECT source_id, external_id, content_hash, ?, raw_path, collected_at
                    FROM notice_versions ORDER BY id DESC LIMIT 1""", (payload,))
            repo.save(replace(self.notice, source_id="other", body_text="다른 출처"), b"other")
            with repo.db:
                for version_id, payload_json in repo.db.execute("SELECT id, payload_json FROM notice_versions").fetchall():
                    repo.db.execute("UPDATE notice_versions SET content_hash=? WHERE id=?",
                                    (legacy_hash(json.loads(payload_json)), version_id))
                repo.db.execute("""UPDATE notices SET current_hash=(SELECT content_hash FROM notice_versions v
                    WHERE v.source_id=notices.source_id AND v.external_id=notices.external_id ORDER BY id DESC LIMIT 1)""")
                repo.db.execute("DROP INDEX idx_notice_versions_identity_id")
                repo.db.execute("PRAGMA user_version=1")
        finally:
            repo.close()

    def test_existing_db_recalculates_hashes_without_losing_versions(self):
        self.seed_legacy_db()
        with closing(sqlite3.connect(self.data / "collector.sqlite3")) as db:
            before = db.execute("SELECT id, payload_json, raw_path, collected_at FROM notice_versions ORDER BY id").fetchall()
        repo = Repository(self.data)
        try:
            self.assertEqual(repo.db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(repo.db.execute("SELECT id, payload_json, raw_path, collected_at FROM notice_versions ORDER BY id").fetchall(), before)
            rows = repo.db.execute("SELECT content_hash, payload_json FROM notice_versions ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in rows], [content_hash(json.loads(row[1])) for row in rows])
            hashes = dict(repo.db.execute("SELECT source_id, current_hash FROM notices").fetchall())
            self.assertEqual(hashes["test"], rows[-2][0])
            self.assertEqual(hashes["other"], rows[-1][0])
            self.assertEqual(repo.save(replace(self.notice, body_text="두 번째", parser_version="k2web-v2"), b"second"), "unchanged")
            self.assertEqual(repo.db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0], 4)
        finally:
            repo.close()

    def test_bad_legacy_payload_rolls_back_all_hash_updates(self):
        self.seed_legacy_db()
        with closing(sqlite3.connect(self.data / "collector.sqlite3")) as db:
            db.execute("UPDATE notice_versions SET payload_json='{}' WHERE id=(SELECT MAX(id) FROM notice_versions)")
            db.commit()
            before = db.execute("SELECT id, content_hash FROM notice_versions ORDER BY id").fetchall()
        with self.assertRaises(KeyError):
            Repository(self.data)
        with closing(sqlite3.connect(self.data / "collector.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT id, content_hash FROM notice_versions ORDER BY id").fetchall(), before)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='idx_notice_versions_identity_id'").fetchone())

    def test_newer_database_version_is_not_downgraded(self):
        repo = Repository(self.data)
        try:
            repo.db.execute("PRAGMA user_version=99")
        finally:
            repo.close()
        with self.assertRaisesRegex(ValueError, "DB 버전: 99"):
            Repository(self.data)
        with closing(sqlite3.connect(self.data / "collector.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)


if __name__ == "__main__":
    unittest.main()
