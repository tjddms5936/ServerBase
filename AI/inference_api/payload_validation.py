from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


CONTRACT_FILE = Path(__file__).resolve().parents[1] / "configs" / "operational_feature_schema.json"
# 명세는 시작 시 한 번 읽어 요청마다 파일 I/O가 발생하지 않도록 한다.
OPERATIONAL_CONTRACT = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))


def validate_feature_values(contract: dict[str, Any], event_type: str, values: dict[str, Any]) -> list[float]:
    if event_type not in contract["events"]:
        raise ValueError(f"지원하지 않는 이벤트입니다: {event_type}")
    names = contract["events"][event_type]["feature_order"]
    if set(values) != set(names):
        raise ValueError("feature 누락 또는 추가 필드가 있습니다. 누락을 0으로 채우지 않습니다.")
    for name in names:
        value = values[name]
        definition = contract["features"][name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"수치가 아닌 feature입니다: {name}")
        if definition["dtype"] == "integer" and not isinstance(value, int):
            raise ValueError(f"정수 feature입니다: {name}")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or value < definition["minimum"]:
            raise ValueError(f"음수 또는 유한하지 않은 feature입니다: {name}")
    return [float(values[name]) for name in names]


def validate_snapshot_relations(contract: dict[str, Any], payload: dict[str, Any]) -> None:
    event_type = payload["event_type"]
    event = contract["events"][event_type]
    if payload["feature_contract_id"] != event["feature_contract_id"]:
        raise ValueError("feature 의미 버전이 현재 서버와 다릅니다.")
    validate_feature_values(contract, event_type, payload["features"])
    for name, path in event["duplicate_feature_paths"].items():
        section, field = path.split(".")
        if payload["features"][name] != payload[section][field]:
            raise ValueError(f"중복 값이 일치하지 않습니다: features.{name}, {path}")
    timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp는 시간대가 명시된 UTC 시각이어야 합니다.")
    session = payload["session"]
    if session["session_id"] <= 0:
        raise ValueError("session_id는 양수여야 합니다.")
    if event_type == "message":
        message = payload["message"]
        if message["packet_size"] != message["payload_size"] + contract["application_header_size_bytes"]:
            raise ValueError("애플리케이션 헤더와 payload 크기 관계가 일치하지 않습니다.")
        if message["request_index"] < 1 or (message["request_index"] == 1 and message["message_interval_ms"] != 0):
            raise ValueError("첫 메시지 번호와 간격 규칙이 일치하지 않습니다.")
    else:
        if not session["is_started"] or not session["is_closed"]:
            raise ValueError("session_summary는 시작 및 종료가 관측되어야 합니다.")
        if session["request_count"] <= 1 and session["avg_message_interval_ms"] != 0:
            raise ValueError("메시지 0~1개일 때 평균 간격은 0이어야 합니다.")
