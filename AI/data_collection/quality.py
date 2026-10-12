from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from inference_api.schemas import InferenceRequest
from .config import ScenarioManifest, StrictModel
from .traffic import CampaignPlan, build_expectations


class CollectedRecord(StrictModel):
    record_schema_version: Literal["v1"]
    collection_sequence_id: int = Field(ge=1, strict=True)
    received_at_utc: str
    run_id: str
    scenario_id: str
    server_build_id: str
    session_id: int = Field(gt=0, strict=True)
    request_index: int | None = Field(ge=1, strict=True)
    label_reference: Literal["run_manifest.json:selected_scenario"]
    payload: InferenceRequest

    @model_validator(mode="after")
    def validate_metadata(self) -> CollectedRecord:
        payload = self.payload.model_dump()
        if self.session_id != payload["session"]["session_id"]:
            raise ValueError("envelope와 payload의 세션 ID가 다릅니다.")
        if self.request_index != payload.get("message", {}).get("request_index"):
            raise ValueError("envelope와 payload의 메시지 번호가 다릅니다.")
        timestamp = datetime.fromisoformat(self.received_at_utc.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("수집 시각은 UTC여야 합니다.")
        return self


def reject_constant(value: str):
    raise ValueError(f"유한하지 않은 JSON 상수입니다: {value}")


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"중복 JSON 필드입니다: {key}")
        result[key] = value
    return result


def parse_json(content: str):
    return json.loads(content, parse_constant=reject_constant, object_pairs_hook=unique_object)


def read_json(path: Path):
    return parse_json(path.read_text(encoding="utf-8-sig"))


def inside_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("캠페인 바깥을 가리키는 파일 경로입니다.")
    return path


def expected_signature(session: dict) -> tuple:
    return (
        tuple((frame["packet_id"], frame["packet_size"]) for frame in session["expected_message_frames"]),
        len(session["expected_message_frames"]), session["expected_error_count"],
        session["expected_last_error_message"], sum(frame["wire_size"] for frame in session["frames"]),
    )


def audit_run(root: Path, entry: dict, campaign: dict) -> dict:
    result = {
        "run_id": entry["run_id"], "scenario_id": entry["scenario_id"], "passed": False,
        "eligible_for_training": False, "label": None, "sessions": 0, "messages": 0, "summaries": 0,
        "record_count": 0, "invalid_records": 0, "duplicate_records": 0,
        "missing_messages": 0, "missing_summaries": 0, "unexpected_records": 0,
        "issues": [], "warnings": [],
    }
    issues = result["issues"]

    def require(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    try:
        run_dir = inside_path(root, f"runs/{entry['run_id']}")
        expected_path = inside_path(root, entry["expectations_file"])
        expected = read_json(expected_path)
        require(hashlib.sha256(expected_path.read_bytes()).hexdigest() == entry["expectations_sha256"], "사전 기대값 파일의 해시가 다릅니다.")
        plan = CampaignPlan.model_validate(campaign["plan_snapshot"])
        profile = next(profile for profile in plan.profiles if profile.scenario_id == entry["scenario_id"])
        source_manifest = ScenarioManifest.model_validate(campaign["scenario_manifest_snapshot"])
        scenario = next(scenario for scenario in source_manifest.scenarios if scenario.scenario_id == entry["scenario_id"])
        label = scenario.label
        require(entry["independent_label"] == label, "캠페인 실행의 정답이 독립 manifest와 다릅니다.")
        regenerated = build_expectations(profile, entry["seed"], entry["run_id"], label)
        require(expected == regenerated, "사전 기대값이 계획·시드·생성기 정의와 일치하지 않습니다.")
        result["label"] = label
        result["message_observable"] = regenerated["message_observable"]
        result["expected_messages"] = regenerated["expected_messages"]
        result["expected_summaries"] = regenerated["expected_summaries"]
        manifest = read_json(run_dir / "run_manifest.json")
        summary = read_json(run_dir / "collection_summary.json")
        runtime = read_json(root / "outcomes" / f"{entry['run_id']}_runtime.json")
        outcome = read_json(root / "outcomes" / f"{entry['run_id']}_traffic.json")
        for field in ("run_id", "scenario_id"):
            require(manifest[field] == entry[field], f"run manifest의 {field}가 다릅니다.")
            require(outcome[field] == entry[field], f"트래픽 결과의 {field}가 다릅니다.")
            require(summary[field] == entry[field], f"수집 통계의 {field}가 다릅니다.")
        require(runtime["run_id"] == entry["run_id"], "프로세스 결과의 run_id가 다릅니다.")
        require(manifest["selected_scenario"] == scenario.model_dump(mode="json"), "수집 당시 독립 정답 정의가 다릅니다.")
        require(manifest["label_source"] == source_manifest.label_source, "정답 출처가 독립 시나리오 정의가 아닙니다.")
        require(manifest["scenario_manifest_snapshot"] == campaign["scenario_manifest_snapshot"], "시나리오 manifest 복사본이 다릅니다.")
        require(manifest["source_manifest_sha256"] == campaign["source_manifest_sha256"], "시나리오 manifest 해시가 다릅니다.")
        require(manifest["server_build_id"] == campaign["server_build_id"], "서버 빌드 식별자가 다릅니다.")
        require(manifest["operational_contract_sha256"] == campaign["operational_contract_sha256"], "운영 의미 schema 해시가 다릅니다.")
        require(manifest["operational_contract_snapshot"] == campaign["operational_contract_snapshot"], "운영 의미 schema 복사본이 다릅니다.")
        require(manifest["dataset_purpose"] == campaign["dataset_purpose"], "데이터 수집 목적이 다릅니다.")
        require(runtime["passed"] is True and all(runtime[name]["graceful"] is True for name in ("server_shutdown", "api_shutdown")), "프로세스 실행 또는 정상 종료가 실패했습니다.")
        require(outcome["passed"] is True, "클라이언트 검증이 실패했습니다.")
        require(len(outcome["sessions"]) == len(regenerated["sessions"]), "클라이언트 결과 수가 기대값과 다릅니다.")
        actual_clients = {session["client_index"]: session for session in outcome["sessions"]}
        require(len(actual_clients) == len(outcome["sessions"]), "클라이언트 결과의 인덱스가 중복되었습니다.")
        require(set(actual_clients) == set(range(profile.sessions)), "클라이언트 결과가 누락되거나 추가되었습니다.")
        for session in regenerated["sessions"]:
            actual = actual_clients.get(session["client_index"], {})
            require(actual.get("passed") is True and actual.get("connected") is True, "실패하거나 연결되지 않은 클라이언트가 있습니다.")
            require(actual.get("frames_sent") == len(session["frames"]), "실제 송신 프레임 수가 기대값과 다릅니다.")
            require(actual.get("bytes_sent") == sum(frame["wire_size"] for frame in session["frames"]), "실제 송신 바이트가 기대값과 다릅니다.")
            require(actual.get("responses_received") == session["expected_responses"], "실제 정상 응답 수가 기대값과 다릅니다.")

        require(summary["complete"] is True and summary["shutdown_complete"] is True, "불완전 종료로 기록된 run입니다.")
        require(not summary["shutdown_timed_out"] and not summary["write_error"] and not summary["summary_error"], "종료 시간 초과 또는 파일 저장 오류가 있습니다.")
        require(all(summary[key] == 0 for key in ("dropped_full", "dropped_closed", "dropped_failed", "failed_records", "pending", "in_flight", "unfinished")), "수집 큐 유실 또는 미완료 레코드가 있습니다.")
        text = (run_dir / "features.jsonl").read_text(encoding="utf-8")
        lines = text.splitlines()
        result["record_count"] = len(lines)
        require(not text or text.endswith("\n"), "JSONL 마지막 줄이 정상적으로 종료되지 않았습니다.")
        records = []
        for line_index, line in enumerate(lines, 1):
            try:
                record = CollectedRecord.model_validate(parse_json(line)).model_dump(mode="json")
                records.append(record)
            except (ValueError, TypeError) as error:
                result["invalid_records"] += 1
                if result["invalid_records"] <= 5:
                    issues.append(f"JSONL {line_index}행 검증 실패: {str(error)[:250]}")
        for key in ("attempted", "enqueued", "written", "flushed"):
            require(summary[key] == len(lines), f"수집 통계 {key}와 실제 JSONL 줄 수가 다릅니다.")
        require(summary["enqueued"] == regenerated["expected_records"], "사전 기대 수와 실제 수집 수가 다릅니다.")
        collection_ids = [record["collection_sequence_id"] for record in records]
        queue_ids = [record["payload"]["queue_sequence_id"] for record in records]
        require(collection_ids == list(range(1, len(lines) + 1)), "수집 순번에 누락·중복·재정렬이 있습니다.")
        require(sorted(queue_ids) == list(range(1, len(lines) + 1)), "C++ 큐 순번에 누락 또는 중복이 있습니다.")
        groups = defaultdict(lambda: {"messages": [], "summaries": []})
        semantic_keys = []
        for record in records:
            require(record["run_id"] == entry["run_id"] and record["scenario_id"] == entry["scenario_id"], "레코드에 다른 run 또는 시나리오가 혼입되었습니다.")
            require(record["server_build_id"] == campaign["server_build_id"], "레코드의 서버 빌드가 다릅니다.")
            payload = record["payload"]
            event_type = payload["event_type"]
            group = groups[record["session_id"]]
            group["messages" if event_type == "message" else "summaries"].append(payload)
            semantic_keys.append((record["session_id"], event_type, record["request_index"]))
        result["duplicate_records"] = len(semantic_keys) - len(set(semantic_keys))
        require(result["duplicate_records"] == 0, "동일 세션/메시지 또는 종료 요약이 중복되었습니다.")
        result["sessions"] = len(groups)
        result["messages"] = sum(len(group["messages"]) for group in groups.values())
        result["summaries"] = sum(len(group["summaries"]) for group in groups.values())
        unique_messages = len({key for key in semantic_keys if key[1] == "message"})
        unique_summaries = len({key for key in semantic_keys if key[1] == "session_summary"})
        result["missing_messages"] = max(0, regenerated["expected_messages"] - unique_messages)
        result["missing_summaries"] = max(0, regenerated["expected_summaries"] - unique_summaries)
        result["unexpected_records"] = max(0, unique_messages - regenerated["expected_messages"]) + max(0, unique_summaries - regenerated["expected_summaries"])
        require(result["sessions"] == regenerated["expected_sessions"], "서버 세션 수가 사전 기대값과 다릅니다.")
        actual_signatures = Counter()
        for session_id, group in groups.items():
            messages = sorted(group["messages"], key=lambda payload: payload["message"]["request_index"])
            require(len(group["summaries"]) == 1, f"세션 {session_id}: 종료 요약이 누락되거나 중복되었습니다.")
            indices = [payload["message"]["request_index"] for payload in messages]
            require(indices == list(range(1, len(messages) + 1)), f"세션 {session_id}: 메시지 번호가 연속적이지 않습니다.")
            for field in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total"):
                values = [payload["features"][field] for payload in messages]
                require(values == sorted(values), f"세션 {session_id}: {field}가 감소했습니다.")
            if len(group["summaries"]) != 1:
                continue
            summary_payload = group["summaries"][0]
            values = summary_payload["features"]
            require(values["request_count"] == len(messages), f"세션 {session_id}: request_count와 메시지 수가 다릅니다.")
            intervals = sum(payload["features"]["message_interval_ms"] for payload in messages)
            expected_mean = intervals / (len(messages) - 1) if len(messages) > 1 else 0
            # C++ JSON의 소수점 3자리 반올림 오차만 허용한다.
            require(math.isclose(values["avg_message_interval_ms"], expected_mean, abs_tol=0.0015), f"세션 {session_id}: 평균 간격이 메시지 간격과 다릅니다.")
            if messages:
                last = messages[-1]["features"]
                for field in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total"):
                    require(values[field] >= last[field], f"세션 {session_id}: 종료 요약의 {field}가 마지막 메시지보다 작습니다.")
            actual_signatures[(
                tuple((payload["message"]["packet_id"], payload["message"]["packet_size"]) for payload in messages),
                values["request_count"], values["error_count"], summary_payload["session"]["last_error_message"],
                values["bytes_received_total"],
            )] += 1
        expected_signatures = Counter(expected_signature(session) for session in regenerated["sessions"])
        require(actual_signatures == expected_signatures, "세션별 프레임 크기·순서·수신 바이트·오류 기대값의 다중집합이 다릅니다.")
        if not regenerated["message_observable"]:
            result["warnings"].append("헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.")
        if campaign["dataset_purpose"] == "integration_test":
            result["warnings"].append("연동 테스트 목적으로 수집한 run이므로 학습 후보에서 제외합니다.")
        result["passed"] = not issues
        result["eligible_for_training"] = (
            result["passed"] and campaign["dataset_purpose"] == "controlled_experiment"
            and regenerated["message_observable"] and result["messages"] > 0
        )
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as error:
        issues.append(f"필수 산출물 또는 실행 정의 검증 실패: {type(error).__name__}: {str(error)[:300]}")
        result["missing_messages"] = max(0, result.get("expected_messages", 0) - result["messages"])
        result["missing_summaries"] = max(0, result.get("expected_summaries", 0) - result["summaries"])
    return result


def audit_campaign(root: Path) -> dict:
    campaign = read_json(root / "campaign_manifest.json")
    results = [audit_run(root, entry, campaign) for entry in campaign["runs"]]
    ids = [entry["run_id"] for entry in campaign["runs"]]
    campaign_issues = []
    if len(ids) != len(set(ids)):
        campaign_issues.append("캠페인 run_id가 중복되었습니다.")
    actual_dirs = {path.name for path in (root / "runs").iterdir() if path.is_dir()} if (root / "runs").exists() else set()
    if actual_dirs - set(ids):
        campaign_issues.append("계획에 없는 추가 run 디렉터리가 있습니다.")
    plan = CampaignPlan.model_validate(campaign["plan_snapshot"])
    expected_ids = {f"{campaign['campaign_id']}_r{repeat:02d}_{profile.scenario_id}" for repeat in range(plan.repeats) for profile in plan.profiles}
    if set(ids) != expected_ids:
        campaign_issues.append("수집 계획 대비 run 항목이 누락되거나 추가되었습니다.")
    canonical = {
        f"{campaign['campaign_id']}_r{repeat:02d}_{profile.scenario_id}": (profile.scenario_id, repeat, plan.seed + repeat * 100 + index)
        for repeat in range(plan.repeats) for index, profile in enumerate(plan.profiles)
    }
    for entry in campaign["runs"]:
        if canonical.get(entry["run_id"]) != (entry["scenario_id"], entry["repeat_index"], entry["seed"]):
            campaign_issues.append(f"{entry['run_id']}: 반복 번호·시드·시나리오가 계획과 다릅니다.")
    labels = {unit: {"0": 0, "1": 0} for unit in ("runs", "sessions", "messages", "session_summaries")}
    scenario_counts = {}
    candidate_labels = {unit: {"0": 0, "1": 0} for unit in ("runs", "sessions", "messages")}
    for result in results:
        if result["passed"]:
            label = str(result["label"])
            for unit, count in [("runs", 1), ("sessions", result["sessions"]), ("messages", result["messages"]), ("session_summaries", result["summaries"])]:
                labels[unit][label] += count
            counts = scenario_counts.setdefault(result["scenario_id"], {"label": result["label"], "runs": 0, "sessions": 0, "messages": 0, "summaries": 0})
            for unit, count in [("runs", 1), ("sessions", result["sessions"]), ("messages", result["messages"]), ("summaries", result["summaries"])]:
                counts[unit] += count
    passed = not campaign_issues and all(result["passed"] for result in results)
    if not passed:
        for result in results:
            # 캠페인 범위 검증 실패도 학습 승인을 막는다.
            if campaign_issues:
                result["eligible_for_training"] = False
    for result in results:
        if result["eligible_for_training"]:
            for unit, count in [("runs", 1), ("sessions", result["sessions"]), ("messages", result["messages"])]:
                candidate_labels[unit][str(result["label"])] += count
    return {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(),
        "campaign_id": campaign["campaign_id"], "campaign_path": str(root.resolve()),
        "campaign_manifest_sha256": hashlib.sha256((root / "campaign_manifest.json").read_bytes()).hexdigest(),
        "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_purpose": campaign["dataset_purpose"], "passed": passed,
        "run_count": len(results), "passed_runs": sum(result["passed"] for result in results),
        "eligible_runs": sum(result["eligible_for_training"] for result in results),
        "totals": {name: sum(result[name] for result in results) for name in (
            "sessions", "messages", "summaries", "record_count", "invalid_records", "duplicate_records",
            "missing_messages", "missing_summaries", "unexpected_records",
        )},
        "validated_label_distribution": labels, "message_candidate_label_distribution": candidate_labels,
        "validated_scenario_distribution": scenario_counts, "campaign_issues": campaign_issues, "runs": results,
        "limitations": [
            "정답은 통제된 시나리오 의도이며 실제 침입 정답이나 모델 성능이 아니다.",
            "클라이언트 인덱스와 C++ 세션 ID의 직접 대응은 없으므로 세션 기대값의 다중집합을 대조한다.",
            "누락 검증은 이 생성기가 계획한 로컬 세션에 한정되며 일반 운영 환경의 모든 유실 원인을 특정하지 않는다.",
            "run 단위 분리·대표성·정답의 탐지 가능성을 추가 검토한 뒤 학습해야 한다.",
        ],
    }


def save_quality_report(report: dict, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    totals = report["totals"]
    lines = [
        "# 운영 트래픽 수집 품질 검증", "",
        f"캠페인: `{report['campaign_id']}`", f"원본 위치: `{report['campaign_path']}`", "",
        f"전체 통과: **{report['passed']}**, run {report['passed_runs']}/{report['run_count']}, 메시지 학습 후보 run {report['eligible_runs']}",
        f"세션 {totals['sessions']}, 메시지 {totals['messages']}, 종료 요약 {totals['summaries']}, JSONL {totals['record_count']}건", "",
        f"잘못된 레코드 {totals['invalid_records']}, 중복 {totals['duplicate_records']}, 메시지 누락 {totals['missing_messages']}, 요약 누락 {totals['missing_summaries']}, 추가 레코드 {totals['unexpected_records']}", "",
        "## 품질 검증을 통과한 run의 정답 분포", "",
        "| 집계 단위 | 정상 0 | 통제된 이상 1 |", "| --- | ---: | ---: |",
    ]
    for unit, counts in report["validated_label_distribution"].items():
        lines.append(f"| {unit} | {counts['0']} | {counts['1']} |")
    lines += ["", "정답 분포는 통과한 run만 집계한다. 세션 수와 메시지 수의 불균형을 구분하며 자동 균형 조정은 하지 않는다.", "", "## 메시지 학습 후보의 정답 분포", "", "| 집계 단위 | 정상 0 | 통제된 이상 1 |", "| --- | ---: | ---: |"]
    for unit, counts in report["message_candidate_label_distribution"].items():
        lines.append(f"| {unit} | {counts['0']} | {counts['1']} |")
    lines += ["", "## 시나리오별 분포", "", "| 시나리오 | 정답 | run | 세션 | 메시지 | 종료 요약 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, counts in report["validated_scenario_distribution"].items():
        lines.append(f"| {name} | {counts['label']} | {counts['runs']} | {counts['sessions']} | {counts['messages']} | {counts['summaries']} |")
    lines += ["", "## run별 결과", "", "| run | 시나리오 정답 | 메시지 | 종료 요약 | 통과 |", "| --- | ---: | ---: | ---: | --- |"]
    for result in report["runs"]:
        lines.append(f"| {result['run_id']} | {result['label']} | {result['messages']} | {result['summaries']} | {result['passed']} |")
    lines += ["", "## 진단과 한계", ""]
    for issue in report["campaign_issues"]:
        lines.append(f"- {issue}")
    for result in report["runs"]:
        for message in result["issues"] + result["warnings"]:
            lines.append(f"- `{result['run_id']}`: {message}")
    lines.extend(f"- {message}" for message in report["limitations"])
    lines += ["", "## 다음 단계", "", "run/session 단위 train·validation·test 분리와 메시지 feature의 탐지 가능성 분석 후 운영 LR/RF 학습을 진행한다.", ""]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
