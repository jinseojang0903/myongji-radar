import hashlib
import json
import tomllib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .rules import analyze_notice
from .repository import Repository


def analyze_file(input_path: Path, rules_path: Path, output_dir: Path):
    if not input_path.exists():
        raise ValueError("수집 결과가 없습니다. 먼저 run 또는 export를 실행하세요.")
    config_bytes = rules_path.read_bytes()
    config = tomllib.loads(config_bytes.decode("utf-8"))
    input_bytes = input_path.read_bytes()
    notices = json.loads(input_bytes.decode("utf-8"))["notices"]
    if not notices:
        raise ValueError("분석할 공지가 없습니다.")
    result = [analyze_notice(notice, config) for notice in notices]
    counts = dict(Counter(n["classification"]["activity"]["value"] for n in result))
    summary = {"total": len(result), "activity": counts,
               "notice_kind": dict(Counter(n["classification"]["notice_kind"]["value"] for n in result)),
               "image_only": sum(any(w["code"] == "image_only" for w in n["notices"]) for n in result)}
    output = {"schema_version": 1, "rules_version": config["version"],
              "rules_sha256": hashlib.sha256(config_bytes).hexdigest(),
              "engine_sha256": hashlib.sha256(Path(__file__).with_name("rules.py").read_bytes()).hexdigest(),
              "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
              "analyzed_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "notices": result}
    # DB를 가진 운영 입력만 버전에 연결한다. JSON만으로 돌리는 오프라인 시험은 그대로 허용한다.
    if (input_path.parent / "collector.sqlite3").exists():
        repo = Repository(input_path.parent)
        try:
            repo.save_analyses([{"notice": notice, "generator_kind": "rule",
                                 "generator_version": ":".join((config["version"], output["rules_sha256"], output["engine_sha256"])),
                                 "result": analysis, "created_at": output["analyzed_at"]}
                                for notice, analysis in zip(notices, result)])
        finally:
            repo.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "analysis.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    # 같은 입력·설정·엔진으로 재실행하면 이력은 중복 저장하지 않는다.
    history_key = hashlib.sha256("|".join(output[k] for k in ("input_sha256", "rules_sha256", "engine_sha256")).encode()).hexdigest()
    history_dir = output_dir / "analysis_history"
    history_dir.mkdir(exist_ok=True)
    history = history_dir / f"{history_key}.json"
    if not history.exists():
        history.write_bytes(target.read_bytes())
    lines = ["# 명지레이더 규칙 분석 검수 목록", "", f"규칙: {config['version']} / 총 {len(result)}건", "",
             "자동 추출 결과이며 전부 사람 검수 전입니다. 참가 가능 여부는 판정하지 않습니다.",
             "본문에서 조건 언급을 찾았더라도 허용·제외·대표자 조건은 근거를 확인해야 합니다.", ""]
    labels = {"application_period": "신청·접수 일정", "submission_period": "제출 일정", "event_period": "행사·교육 일정",
              "grades": "학년 조건", "student_status": "학적 조건", "eligible_campuses": "참가 캠퍼스 조건",
              "major_restrictions": "전공 조건", "team_size": "팀 인원 조건", "participation_mode": "개인·팀 참가", "venue_campuses": "행사 장소 캠퍼스"}
    for n in result:
        c = n["classification"]
        lines += [f"## {n['title']}", "", n["source_url"], "",
                  f"- ID: {n['source_id']} / {n['external_id']} / 원문 버전 {n['source_version_id']}",
                  f"- 분류: {c['activity']['value']} / 유형: {', '.join(c['types']['value']) or '미상'} / 공지 성격: {c['notice_kind']['value']}"]
        for key, value in n["fields"].items():
            text = json.dumps(value["value"], ensure_ascii=False) if value["value"] is not None else "확인 필요"
            lines.append(f"- {labels[key]} ({key}): {text} [{value['status']}]")
            for e in value["evidence"]:
                lines.append("  - 근거: " + " ".join(e["quote"].split()))
        for e in n["eligibility_evidence"]:
            lines.append("- 자격 원문: " + " ".join(e["quote"].split()))
        lines += ["- 안내: " + w["message"] for w in n["notices"]]
        lines.append("")
    review = output_dir / "analysis_review.md"
    temporary = review.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(review)
    return summary
