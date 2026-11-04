"""추출 프롬프트 v0.2의 출력 JSON Schema(schema.json)를 만든다.

항목마다 같은 감싸개(value·flag·evidence·note·alternatives)를 쓰므로 손으로 쓰면 틀리기 쉬워 생성한다.
- `claude -p --json-schema`가 $ref를 지원하는지 확인하지 못해 참조 없이 펼친다.
- 스키마를 명령줄 인자로 넘기므로 Windows 명령줄 한도(약 32KB) 안에 들도록 설명문 없이 압축해서 쓴다.
  항목의 뜻은 system.md에 있다.
- 형식 검사로 막지 못하는 조합(flag가 found인데 value가 null 등)은 파이프라인 코드가 검사한다 (#14).
실행: python build_schema.py  (같은 폴더에 schema.json을 덮어쓴다)
"""
import json
from pathlib import Path

MAX_SIZE = 20_000  # 명령줄 한도에 여유를 둔 상한


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def text(max_length):
    return {"type": "string", "maxLength": max_length}


def enum(*values):
    return {"type": "string", "enum": list(values)}


NULL_TEXT = {"type": ["string", "null"], "maxLength": 300}
EVIDENCE = {"type": "array", "items": obj({
    "quote": text(200),
    "notice": {"type": "integer", "minimum": 1},
    "location": enum("제목", "본문", "이미지", "첨부"),
    "file": {"type": ["string", "null"]},
})}
# 날짜는 값이 없어도 같은 모양을 유지한다 (date·time이 null). union을 줄이기 위함.
DATE = obj({
    "date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "time": {"type": ["string", "null"], "pattern": "^\\d{2}:\\d{2}$"},
    "year_inferred": {"type": "boolean"},
})
DELIVERABLE_TAGS = ["기획서·제안서", "영상", "에세이·수기", "코드·개발", "발표", "디자인", "현장 참가", "기타"]

VALUES = {
    "title": text(200),
    "organizer": text(100),
    "scope": enum("교내", "외부"),
    "kind": enum("모집", "연장", "변경", "취소", "결과", "기타"),
    "apply": obj({"start": DATE, "end": DATE}),
    "stages": {"type": "array", "items": obj({
        "name": enum("신청", "출품", "예선", "본선", "발표", "시상", "기타"),
        "label": text(100),
        "start": DATE,
        "end": DATE,
    })},
    "campus": obj({"any": {"type": "boolean"}, "allowed": {"type": "array", "items": enum("인문", "자연")}}),
    "grades": obj({"any": {"type": "boolean"}, "allowed": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 6}}}),
    "enrollment": obj({"allowed": {"type": "array", "items": text(30)}, "excluded": {"type": "array", "items": text(30)}}),
    "team": obj({
        "min": {"type": ["integer", "null"], "minimum": 1},
        "max": {"type": ["integer", "null"], "minimum": 1},
        "individual_ok": {"type": ["boolean", "null"]},
    }),
    "deliverables": obj({"tags": {"type": "array", "items": enum(*DELIVERABLE_TAGS)}, "text": text(300)}),
    "benefits": text(300),
    "mileage": obj({"text": text(200), "area": {"type": ["string", "null"]}, "points": {"type": ["number", "null"]}}),
    "how_to_apply": obj({"text": text(300), "url": {"type": ["string", "null"], "pattern": "^https?://"}}),
    "contact": obj({"dept": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}, "phone": {"type": ["string", "null"]}}),
}


def field(value_schema):
    return obj({
        "value": nullable(value_schema),
        "flag": enum("found", "missing", "needs_check", "conflict"),
        "evidence": EVIDENCE,
        "note": NULL_TEXT,
        # 고르지 않은 다른 값. 날짜는 "YYYY-MM-DD" 또는 "YYYY-MM-DD HH:MM"으로 쓴다 (마감 변경 이력에 쓰임)
        "alternatives": {"type": "array", "items": obj({"text": text(200), "notice": {"type": "integer", "minimum": 1}})},
    })


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "명지레이더 대회 카드 추출 결과 v0.2",
    "type": "object",
    "properties": {
        "is_contest": obj({"value": {"type": "boolean"}, "reason": text(200)}),
        "fields": obj({name: field(schema) for name, schema in VALUES.items()}),
        "warnings": {"type": "array", "items": text(200)},
    },
    # is_contest가 false면 fields를 생략할 수 있다. true일 때 fields가 있는지는 코드가 검사한다.
    "required": ["is_contest", "warnings"],
    "additionalProperties": False,
}

if __name__ == "__main__":
    encoded = json.dumps(SCHEMA, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SIZE:
        raise SystemExit(f"schema.json이 {len(encoded.encode('utf-8'))}바이트로 상한 {MAX_SIZE}을 넘는다")
    out = Path(__file__).with_name("schema.json")
    out.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    print("wrote", out, len(encoded.encode("utf-8")), "bytes")
