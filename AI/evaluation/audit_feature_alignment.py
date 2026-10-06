from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from pydantic import TypeAdapter

from inference_api.feature_transformer import MESSAGE_FEATURE_ORDER, SESSION_SUMMARY_FEATURE_ORDER
from inference_api.payload_validation import validate_feature_values, validate_snapshot_relations
from inference_api.schemas import (
    InferenceRequest, MessageFeatureValues, MessageInferenceRequest,
    SessionSummaryFeatureValues, SessionSummaryInferenceRequest,
)
from scripts.inspect_unsw_nb15 import ENCODING_CANDIDATES, EXPECTED_FILES


CONTRACT_FILE = AI_ROOT / "configs" / "operational_feature_schema.json"
TRAIN_FILE = AI_ROOT / "data" / "raw" / "unsw_nb15" / "UNSW_NB15_training-set.csv"
MODEL_SCHEMA_FILE = AI_ROOT / "artifacts" / "validation_experiment" / "feature_schema.json"
REPORT_FILE = AI_ROOT / "reports" / "feature_alignment.json"
MARKDOWN_FILE = REPORT_FILE.with_suffix(".md")
DESCRIPTION_ALIASES = {
    "smeansz": "smean", "dmeansz": "dmean", "res_bdy_len": "response_body_len",
    "sintpkt": "sinpkt", "dintpkt": "dinpkt",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(AI_ROOT).as_posix()
    except ValueError:
        # 포트폴리오 보고서에서 같은 저장소의 C++ 경로는 PC별 절대 경로 대신 기록합니다.
        try:
            return "../" + path.resolve().relative_to(AI_ROOT.parent).as_posix()
        except ValueError:
            return str(path.resolve())


def read_training_header(path: Path) -> list[str]:
    # 이번 작업은 데이터 의미 비교이므로 학습 행이나 공식 test를 읽지 않습니다.
    with path.open(encoding="utf-8-sig", newline="") as file:
        columns = next(csv.reader(file), [])
    if not {"label", "attack_cat"}.issubset(columns) or len(columns) != len(set(columns)):
        raise ValueError("train CSV의 정답 열 또는 중복 열 이름을 확인하세요.")
    return [name for name in columns if name not in {"id", "label", "attack_cat"}]


def read_descriptions(path: Path) -> tuple[dict[str, str], str]:
    for encoding in ENCODING_CANDIDATES:
        try:
            with path.open(encoding=encoding, newline="") as file:
                rows = list(csv.DictReader(file))
            descriptions = {}
            for row in rows:
                name = "".join(row["Name"].split()).lower()
                name = DESCRIPTION_ALIASES.get(name, name)
                descriptions[name] = row["Description"].strip()
            return descriptions, encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"feature 설명 CSV를 지원 인코딩으로 읽을 수 없습니다: {path}")


def verify_contract(contract: dict[str, Any], raw_names: list[str]) -> dict[str, dict[str, Any]]:
    if contract["wire_schema_version"] != "v1" or contract["missing_value_policy"] != "reject_without_zero_fill":
        raise ValueError("현재 검사 도구는 v1과 결측 입력 거부 규칙을 사용합니다.")
    api_definitions = {
        "message": (MESSAGE_FEATURE_ORDER, MessageFeatureValues),
        "session_summary": (SESSION_SUMMARY_FEATURE_ORDER, SessionSummaryFeatureValues),
    }
    request_models = {"message": MessageInferenceRequest, "session_summary": SessionSummaryInferenceRequest}
    if set(contract["events"]) != set(api_definitions):
        raise ValueError("이벤트 종류가 현재 FastAPI와 다릅니다.")
    for event_type, (order, feature_model) in api_definitions.items():
        event = contract["events"][event_type]
        if get_args(request_models[event_type].model_fields["feature_contract_id"].annotation) != (event["feature_contract_id"],):
            raise ValueError(f"{event_type} 의미 버전이 FastAPI와 다릅니다.")
        names = event["feature_order"]
        if names != order or set(names) != set(feature_model.model_fields):
            raise ValueError(f"{event_type} feature 이름 또는 순서가 FastAPI와 다릅니다.")
        if set(event["duplicate_feature_paths"]) != set(names):
            raise ValueError(f"{event_type} 중복 필드 경로가 불완전합니다.")
        for name in names:
            definition = contract["features"][name]
            expected_type = int if definition["dtype"] == "integer" else float
            if definition["dtype"] not in {"integer", "number"} or feature_model.model_fields[name].annotation is not expected_type:
                raise ValueError(f"{name} 수치 타입이 FastAPI와 다릅니다.")
            if definition["unit"] not in {"byte", "ms", "count"} or definition["minimum"] != 0:
                raise ValueError(f"{name} 단위 또는 최소값 정의를 확인하세요.")
    alignment = {}
    for group in contract["unsw_nb15_alignment_groups"]:
        for name in group["features"]:
            if name in alignment:
                raise ValueError(f"UNSW 입력 정의가 중복됩니다: {name}")
            alignment[name] = {key: value for key, value in group.items() if key != "features"}
    if set(alignment) != set(raw_names):
        raise ValueError("UNSW train의 모든 입력을 정확히 한 번씩 분류해야 합니다.")
    return alignment


def validate_payload(contract: dict[str, Any], payload: dict[str, Any]) -> list[float]:
    # API와 오프라인 검사가 같은 검증 함수를 사용해 규칙 차이를 방지한다.
    event_type = payload["event_type"]
    values = validate_feature_values(contract, event_type, payload["features"])
    parsed = TypeAdapter(InferenceRequest).validate_python(payload).model_dump()
    validate_snapshot_relations(contract, parsed)
    return values


def assess_model_schema(contract: dict[str, Any], event_type: str, model_schema: dict[str, Any] | None) -> dict[str, Any]:
    event = contract["events"][event_type]
    expected_names = event["feature_order"]
    if model_schema is None:
        return {"schema_compatible": False, "reasons": ["모델 feature schema 파일이 없습니다."]}
    actual_names = model_schema.get("raw_input_features", [])
    required_metadata = {
        "feature_contract_id": event["feature_contract_id"],
        "wire_schema_version": contract["wire_schema_version"],
        "capture_layer": contract["capture_layer"],
        "observation_scope": event["observation_scope"],
        "event_type": event_type,
        "feature_units": {name: contract["features"][name]["unit"] for name in expected_names},
    }
    reasons = []
    if actual_names != expected_names:
        reasons.append("모델의 원본 입력 이름 또는 순서가 운영 입력과 다릅니다.")
    for name, expected in required_metadata.items():
        if model_schema.get(name) != expected:
            reasons.append(f"모델 schema의 {name} 정의가 없거나 운영 명세와 다릅니다.")
    return {
        "schema_compatible": not reasons, "reasons": reasons,
        "model_raw_feature_count": len(actual_names), "operational_feature_count": len(expected_names),
        "missing_model_inputs": [name for name in actual_names if name not in expected_names],
        "extra_operational_inputs": [name for name in expected_names if name not in actual_names],
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# 운영 feature와 UNSW-NB15 입력 정합성 검사", "",
        "이 보고서는 입력 의미와 schema 호환성을 검사한다. 모델을 학습하거나 실제 추론 서버에 연결하지 않는다.", "",
        f"- UNSW 원본 입력: {len(report['unsw_features'])}개; 공식 train의 헤더만 확인",
        f"- 운영 메시지 입력: {len(report['events']['message']['feature_order'])}개",
        f"- 운영 세션 종료 요약 입력: {len(report['events']['session_summary']['feature_order'])}개",
        "- 현재 FastAPI의 필드 이름·순서·수치 타입과 운영 명세의 일치 검사 통과",
        "- C++ 수집 의미는 코드 검토 기반이며, 자동 검사로 TCP 도착 시각이나 I/O 완료 순서까지 증명한 것은 아님", "",
        "## 모델 schema 호환성", "",
    ]
    for event_type, comparison in report["model_schema_comparison"].items():
        lines.extend([
            f"### {event_type}", "",
            f"schema 호환: {'예' if comparison['schema_compatible'] else '아니오'}", "",
            *[f"- {reason}" for reason in comparison["reasons"]], "",
        ])
    lines.extend([
        "schema가 맞아도 탐지 성능, threshold 적합성, 분포 차이 검증은 별도로 필요하다. "
        "이 검사에서 비호환으로 나와도 기본 실행은 보고서를 정상 생성하고 종료한다. "
        f"`--require-compatible` 옵션은 검사 대상 `{report['compatibility_gate_event_type']}`의 "
        "schema가 비호환 또는 미존재이면 종료 코드 2를 반환한다. "
        "`--event-type`으로 메시지 또는 종료 요약 모델을 각각 검사한다.", "",
        "## 운영 입력 명세", "",
    ])
    for event_type, event in report["events"].items():
        lines.extend([
            f"### {event_type}: {event['feature_contract_id']}", "",
            event["purpose"], "", f"관측 시점: {event['emission_point']}", "",
            "| 순서 | 입력 | 타입 | 단위 | 의미 |", "| --- | --- | --- | --- | --- |",
        ])
        for index, name in enumerate(event["feature_order"], 1):
            definition = report["feature_definitions"][name]
            lines.append(f"| {index} | {name} | {definition['dtype']} | {definition['unit']} | {definition['meaning']} |")
        lines.append("")
    lines.extend([
        "## UNSW 전체 입력 대조", "",
        "related_server_features는 비교 후보이지 동등한 변환식이 아니다. "
        "현재 명세에는 자동 치환 가능한 동등 입력을 등록하지 않았다.", "",
        "| UNSW 입력 | 상태 | 관련 서버 입력 | 이유 |", "| --- | --- | --- | --- |",
    ])
    for row in report["unsw_features"]:
        related = ", ".join(row["related_server_features"]) or "없음"
        lines.append(f"| {row['name']} | {row['status']} | {related} | {row['reason']} |")
    lines.extend([
        "", "## 상태별 개수", "",
        *[f"- {name}: {count}개" for name, count in report["alignment_status_counts"].items()],
        "", "## 수집 및 배포 전 제약", "",
        *[f"- {note}" for note in report["known_limitations"]],
        "", "## 다음 단계", "",
        "오류 계수, 종료 snapshot 고정 및 FastAPI 공통 검증을 적용한 v2 contract로 "
        "정상/통제된 이상 시나리오를 수집하고 별도 애플리케이션 모델을 학습한다. "
        "UNSW baseline의 42개 입력을 0으로 채우거나 이름만 바꿔 현재 FastAPI에 연결하지 않는다.", "",
        "세부 설계: `docs/feature_alignment.md`; 보강 코드와 테스트: `docs/runtime_hardening.md`", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 feature 명세와 UNSW/FastAPI/model schema를 대조합니다.")
    parser.add_argument("--model-schema", type=Path, default=MODEL_SCHEMA_FILE)
    parser.add_argument("--event-type", choices=["message", "session_summary"], default="message")
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args()
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    raw_names = read_training_header(TRAIN_FILE)
    alignment = verify_contract(contract, raw_names)
    description_path = next((TRAIN_FILE.parent / name for name in EXPECTED_FILES["features"] if (TRAIN_FILE.parent / name).exists()), None)
    descriptions, encoding = read_descriptions(description_path) if description_path else ({}, None)
    model_schema = json.loads(args.model_schema.read_text(encoding="utf-8")) if args.model_schema.exists() else None
    comparison = {event: assess_model_schema(contract, event, model_schema) for event in contract["events"]}
    evidence_paths = [
        AI_ROOT.parent / "Server" / "ServerBase" / "Session.cpp",
        AI_ROOT.parent / "Server" / "ServerBase" / "Protocol.h",
        AI_ROOT.parent / "Server" / "FeatureExtraction" / "FeatureCollector.cpp",
        AI_ROOT.parent / "Server" / "FeatureExtraction" / "FeatureSnapshot.h",
        AI_ROOT.parent / "Server" / "FeatureExtraction" / "AiInferenceWorker.cpp",
        AI_ROOT.parent / "Server" / "Server" / "ServerSession.cpp",
        AI_ROOT / "inference_api" / "schemas.py",
        AI_ROOT / "inference_api" / "feature_transformer.py",
        AI_ROOT / "inference_api" / "payload_validation.py",
    ]
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(UTC).isoformat(),
        "contract_path": describe_path(CONTRACT_FILE), "contract_sha256": sha256(CONTRACT_FILE),
        "training_header_source": describe_path(TRAIN_FILE), "official_test_read": False,
        "description_source": describe_path(description_path) if description_path else None,
        "description_encoding": encoding,
        "model_schema_source": describe_path(args.model_schema),
        "compatibility_gate_event_type": args.event_type,
        "model_schema_sha256": sha256(args.model_schema) if model_schema is not None else None,
        "code_evidence": [{"path": describe_path(path), "sha256": sha256(path)} for path in evidence_paths],
        "events": contract["events"], "feature_definitions": contract["features"],
        "excluded_model_metadata": contract["excluded_model_metadata"],
        "known_limitations": contract["known_limitations"],
        "unsw_features": [{"name": name, "dataset_description": descriptions.get(name), **alignment[name]} for name in raw_names],
        "alignment_status_counts": dict(sorted(Counter(row["status"] for row in alignment.values()).items())),
        "model_schema_comparison": comparison,
    }
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    MARKDOWN_FILE.write_text(render_report(report), encoding="utf-8")
    print(f"[Feature Audit] UNSW 입력 {len(raw_names)}개 / 메시지 7개 / 세션 요약 8개")
    for event_type, result in comparison.items():
        print(f"  - {event_type} 모델 schema 호환: {result['schema_compatible']}")
    print(f"  - 보고서: {MARKDOWN_FILE}")
    return 2 if args.require_compatible and not comparison[args.event_type]["schema_compatible"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
