import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(payload: dict) -> str:
    content = {key: payload[key] for key in ("title", "body_text", "images", "attachments")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "collector.sqlite3", timeout=30)
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError(f"지원하지 않는 DB 버전: {version}")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS notices (
                    source_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    url TEXT NOT NULL, current_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, external_id));
                CREATE TABLE IF NOT EXISTS notice_versions (
                    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
                    raw_path TEXT NOT NULL, collected_at TEXT NOT NULL,
                    FOREIGN KEY (source_id, external_id) REFERENCES notices(source_id, external_id));
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
                    finished_at TEXT, result_json TEXT);
            """)
            if version < 2:
                self._migrate_content_hashes()
        except Exception:
            self.db.close()
            raise

    def _migrate_content_hashes(self):
        # 기존 버전 ID와 원문은 보존하고 해시만 같은 트랜잭션에서 재계산한다.
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_notice_versions_identity_id
                ON notice_versions(source_id, external_id, id DESC)""")
            rows = self.db.execute("SELECT id, payload_json FROM notice_versions").fetchall()
            for version_id, payload_json in rows:
                digest = content_hash(json.loads(payload_json))
                self.db.execute("UPDATE notice_versions SET content_hash=? WHERE id=?", (digest, version_id))
            self.db.execute("""UPDATE notices SET current_hash=(SELECT v.content_hash FROM notice_versions v
                WHERE v.source_id=notices.source_id AND v.external_id=notices.external_id
                ORDER BY v.id DESC LIMIT 1)
                WHERE EXISTS (SELECT 1 FROM notice_versions v WHERE v.source_id=notices.source_id
                    AND v.external_id=notices.external_id)""")
            self.db.execute("PRAGMA user_version=2")

    def start_run(self):
        with self.db:
            return self.db.execute("INSERT INTO runs(started_at) VALUES (?)", (now(),)).lastrowid

    def finish_run(self, run_id, result):
        with self.db:
            self.db.execute("UPDATE runs SET finished_at=?, result_json=? WHERE id=?",
                            (now(), json.dumps(result, ensure_ascii=False), run_id))

    def has_notice(self, source_id: str, external_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM notices WHERE source_id=? AND external_id=?",
                               (source_id, external_id)).fetchone() is not None

    def save(self, notice, html: bytes, reparse_previous=None) -> str:
        payload = asdict(notice)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = content_hash(payload)
        key = (notice.source_id, notice.external_id)
        previous = self.db.execute("SELECT current_hash FROM notices WHERE source_id=? AND external_id=?", key).fetchone()
        current_version = None
        reparsed_encoded = None
        baseline_hash = previous[0] if previous else None
        parser_changed = False
        if previous:
            current_version = self.db.execute("""SELECT id, payload_json, raw_path FROM notice_versions
                WHERE source_id=? AND external_id=? ORDER BY id DESC LIMIT 1""", key).fetchone()
            if current_version is None:
                raise ValueError("저장된 공지의 원문 버전이 없습니다")
            previous_payload = json.loads(current_version[1])
            parser_changed = previous_payload.get("parser_version") != payload["parser_version"]
            if parser_changed and baseline_hash != digest:
                if reparse_previous is None:
                    raise ValueError("파서가 변경되어 저장된 원문을 다시 파싱해야 합니다")
                old_html = (self.data_dir / current_version[2]).read_bytes()
                reparsed = asdict(reparse_previous(old_html, previous_payload))
                if ((reparsed["source_id"], reparsed["external_id"]) != key
                        or reparsed["parser_version"] != payload["parser_version"]):
                    raise ValueError("저장된 원문의 재파싱 결과가 현재 공지 또는 파서와 다릅니다")
                reparsed_encoded = json.dumps(reparsed, ensure_ascii=False, sort_keys=True)
                baseline_hash = content_hash(reparsed)
        timestamp = now()
        changed = previous is None or baseline_hash != digest
        raw_path = ""
        if changed:
            raw_hash = hashlib.sha256(html).hexdigest()
            raw_path = f"raw/{raw_hash}.html"
            target = self.data_dir / raw_path
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                target.write_bytes(html)
        with self.db:
            self.db.execute("""INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                url=excluded.url, current_hash=excluded.current_hash, last_seen_at=excluded.last_seen_at""",
                (*key, notice.url, digest, timestamp, timestamp))
            if parser_changed:
                # 같은 원문은 현재 파싱 결과를 갱신한다. 원문도 바뀌었다면
                # 이전 원문을 현재 파서로 재해석한 결과를 그 버전에 보존한다.
                updated_payload = reparsed_encoded if changed else encoded
                updated_hash = baseline_hash if changed else digest
                self.db.execute("UPDATE notice_versions SET content_hash=?, payload_json=? WHERE id=?",
                                (updated_hash, updated_payload, current_version[0]))
            if changed:
                self.db.execute("""INSERT INTO notice_versions
                    (source_id,external_id,content_hash,payload_json,raw_path,collected_at)
                    VALUES (?,?,?,?,?,?)""", (*key, digest, encoded, raw_path, timestamp))
        return "new" if previous is None else "changed" if changed else "unchanged"

    def export(self, path: Path):
        rows = self.db.execute("""SELECT v.payload_json, v.id, v.collected_at FROM notice_versions v
            WHERE v.id=(SELECT MAX(v2.id) FROM notice_versions v2
                WHERE v2.source_id=v.source_id AND v2.external_id=v.external_id)
            ORDER BY v.source_id, v.external_id""")
        result = [{**json.loads(row[0]), "version_id": row[1], "collected_at": row[2]} for row in rows]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "notices": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return len(result)

    def close(self):
        self.db.close()
