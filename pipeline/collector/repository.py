import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .assets import atomic_bytes, private_path

DB_VERSION = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(payload: dict) -> str:
    content = {key: payload[key] for key in ("title", "body_text", "images", "attachments")}
    # 경로·변환 결과·일시적 오류는 원문 변경이 아니다. 실제 파일 바이트만 해시에 반영한다.
    for kind, label in (("images", "alt"), ("attachments", "name")):
        content[kind] = [{key: item[key] for key in ("url", label, "sha256") if key in item}
                         for item in payload[kind]]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "collector.sqlite3", timeout=30)
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > DB_VERSION:
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
            if version < 3:
                self._migrate_analyses()
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

    def _migrate_analyses(self):
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("""CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY,
                notice_version_id INTEGER NOT NULL REFERENCES notice_versions(id),
                generator_kind TEXT NOT NULL CHECK(generator_kind IN ('rule', 'ai')),
                generator_version TEXT NOT NULL, model TEXT NOT NULL, model_id TEXT NOT NULL,
                prompt_version TEXT NOT NULL, prompt_fingerprint TEXT NOT NULL,
                input_hash TEXT NOT NULL, result_hash TEXT NOT NULL,
                result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(notice_version_id, generator_kind, generator_version, model, model_id,
                       prompt_version, prompt_fingerprint, input_hash, result_hash))""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_analyses_version ON analyses(notice_version_id, id DESC)")
            self.db.execute("PRAGMA user_version=3")

    def save_analyses(self, records: list[dict]) -> list[int]:
        """분석 묶음을 같은 트랜잭션에 보존한다. 다른 공지·본문 판에 연결하면 전부 거절한다."""
        ids = []
        with self.db:
            for record in records:
                notice = record["notice"]
                version_id = notice["version_id"]
                if type(version_id) is not int or version_id < 1:
                    raise ValueError("분석할 공지 버전 ID가 올바르지 않습니다")
                row = self.db.execute("SELECT source_id, external_id, payload_json FROM notice_versions WHERE id=?",
                                      (version_id,)).fetchone()
                input_hash = content_hash(notice)
                if (row is None or row[:2] != (notice["source_id"], notice["external_id"])
                        or content_hash(json.loads(row[2])) != input_hash):
                    raise ValueError("분석 입력과 저장된 공지 버전이 다릅니다. export와 analyze를 다시 실행하세요.")
                kind, version = record["generator_kind"], record["generator_version"]
                if kind not in ("rule", "ai") or not isinstance(version, str) or not version:
                    raise ValueError("분석 생성기 종류 또는 버전이 올바르지 않습니다")
                encoded = json.dumps(record["result"], ensure_ascii=False, sort_keys=True, allow_nan=False)
                result_hash = hashlib.sha256(encoded.encode()).hexdigest()
                key = (version_id, kind, version, record.get("model") or "", record.get("model_id") or "",
                       record.get("prompt_version") or "", record.get("prompt_fingerprint") or "", input_hash, result_hash)
                self.db.execute("""INSERT INTO analyses(notice_version_id, generator_kind, generator_version,
                    model, model_id, prompt_version, prompt_fingerprint, input_hash, result_hash, result_json, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                                (*key, encoded, record.get("created_at") or now()))
                ids.append(self.db.execute("""SELECT id FROM analyses WHERE notice_version_id=? AND generator_kind=?
                    AND generator_version=? AND model=? AND model_id=? AND prompt_version=? AND prompt_fingerprint=?
                    AND input_hash=? AND result_hash=?""", key).fetchone()[0])
        return ids

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

    def latest_payload(self, source_id: str, external_id: str) -> dict | None:
        row = self.db.execute("""SELECT payload_json FROM notice_versions
            WHERE source_id=? AND external_id=? ORDER BY id DESC LIMIT 1""", (source_id, external_id)).fetchone()
        return json.loads(row[0]) if row else None

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
            if parser_changed:
                if reparse_previous is None:
                    raise ValueError("파서가 변경되어 저장된 원문을 다시 파싱해야 합니다")
                old_html = (self.data_dir / current_version[2]).read_bytes()
                reparsed = asdict(reparse_previous(old_html, previous_payload))
                # 과거 HTML을 다시 읽어도 그 당시 파일 바이트는 현재 URL로 재요청하지 않는다.
                # 링크가 같은 자산에 한해 저장돼 있던 메타데이터·해시를 이어 붙인다.
                for kind in ("images", "attachments"):
                    old_assets = {item["url"]: item for item in previous_payload.get(kind, [])}
                    reparsed[kind] = [{**old_assets.get(item["url"], {}), **item} for item in reparsed[kind]]
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
            target = private_path(self.data_dir, raw_path)
            if not target.exists():
                atomic_bytes(target, html)
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
            elif not changed:
                # 자산 확인 메타데이터만 갱신한다. 과거 버전의 URL·게시일은 덮어쓰지 않는다.
                current_payload = json.loads(current_version[1])
                for kind in ("images", "attachments"):
                    current_payload[kind] = payload[kind]
                self.db.execute("UPDATE notice_versions SET payload_json=? WHERE id=?",
                                (json.dumps(current_payload, ensure_ascii=False, sort_keys=True), current_version[0]))
            if changed:
                self.db.execute("""INSERT INTO notice_versions
                    (source_id,external_id,content_hash,payload_json,raw_path,collected_at)
                    VALUES (?,?,?,?,?,?)""", (*key, digest, encoded, raw_path, timestamp))
        return "new" if previous is None else "changed" if changed else "unchanged"

    def export(self, path: Path):
        rows = self.db.execute("""SELECT v.payload_json, v.id, v.collected_at, n.last_seen_at FROM notice_versions v
            JOIN notices n ON n.source_id=v.source_id AND n.external_id=v.external_id
            WHERE v.id=(SELECT MAX(v2.id) FROM notice_versions v2
                WHERE v2.source_id=v.source_id AND v2.external_id=v.external_id)
            ORDER BY v.source_id, v.external_id""")
        result = [{**json.loads(row[0]), "version_id": row[1], "collected_at": row[2], "last_seen_at": row[3]} for row in rows]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "notices": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return len(result)

    def close(self):
        self.db.close()
