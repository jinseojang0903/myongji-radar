import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "collector.sqlite3", timeout=30)
        self.db.execute("PRAGMA foreign_keys=ON")
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
            PRAGMA user_version=1;
        """)

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

    def save(self, notice, html: bytes) -> str:
        payload = asdict(notice)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # RSS 메타데이터는 버전 변경 판정에서 제외. 파서 변경은 재처리 추적을 위해 포함.
        semantic = {key: value for key, value in payload.items() if key not in ("url", "published_at")}
        digest = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        key = (notice.source_id, notice.external_id)
        previous = self.db.execute("SELECT current_hash FROM notices WHERE source_id=? AND external_id=?", key).fetchone()
        timestamp = now()
        changed = previous is None or previous[0] != digest
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
