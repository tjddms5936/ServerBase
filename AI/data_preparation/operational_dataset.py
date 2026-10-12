from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from data_collection.config import StrictModel
from data_collection.quality import CollectedRecord, audit_campaign, inside_path, parse_json, read_json
from inference_api.payload_validation import CONTRACT_FILE, OPERATIONAL_CONTRACT
from inference_api.feature_transformer import MESSAGE_FEATURE_ORDER
from inference_api.schemas import MessageFeatureValues


SPLITS = ("train", "validation", "test")
FEATURE_NAMES = OPERATIONAL_CONTRACT["events"]["message"]["feature_order"]
METADATA_COLUMNS = [
    "row_id", "campaign_id", "run_id", "scenario_id", "repeat_index", "session_id",
    "request_index", "queue_sequence_id", "collection_sequence_id", "timestamp",
    "received_at_utc", "source_line", "block_id", "split", "feature_hash",
]


class DatasetConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    dataset_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
    campaign_dirs: Annotated[list[Path], Field(min_length=1)]
    output_dir: Path
    feature_contract_id: Literal["server_application_message_v2"] = "server_application_message_v2"
    split_policy: Literal["chronological_repeat_blocks"] = "chronological_repeat_blocks"
    validation_blocks: Annotated[int, Field(ge=1, strict=True)] = 1
    test_blocks: Annotated[int, Field(ge=1, strict=True)] = 1
    require_all_scenarios_each_split: bool = Field(default=True, strict=True)
    duplicate_feature_policy: Literal["report", "error"] = "report"


def load_config(path: Path) -> DatasetConfig:
    path = path.resolve()
    values = read_json(path)
    values["campaign_dirs"] = [(path.parent / Path(item)).resolve() for item in values["campaign_dirs"]]
    values["output_dir"] = (path.parent / Path(values["output_dir"])).resolve()
    return DatasetConfig.model_validate(values)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("실행 시작·종료 시각은 UTC여야 합니다.")
    return parsed


def fingerprint(features: dict) -> str:
    # 정수형 바이트·카운터는 큰 값도 정밀도를 잃지 않도록 float로 바꾸지 않는다.
    values = [int(features[name]) if OPERATIONAL_CONTRACT["features"][name]["dtype"] == "integer"
              else float(features[name]) for name in FEATURE_NAMES]
    content = json.dumps(values, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def snapshot_inputs(root: Path, campaign: dict) -> dict[str, str]:
    paths = [root / "campaign_manifest.json"]
    for entry in campaign["runs"]:
        paths.append(inside_path(root, entry["expectations_file"]))
        for name in ("features.jsonl", "run_manifest.json", "collection_summary.json"):
            paths.append(inside_path(root, f"runs/{entry['run_id']}/{name}"))
        for suffix in ("traffic", "runtime"):
            paths.append(inside_path(root, f"outcomes/{entry['run_id']}_{suffix}.json"))
    return {str(path): file_hash(path) for path in paths if path.is_file()}


def load_runs(config: DatasetConfig) -> tuple[list[dict], list[dict], dict[str, str], list[dict]]:
    roots = [path.resolve() for path in config.campaign_dirs]
    if len(roots) != len(set(roots)):
        raise ValueError("같은 캠페인 경로를 중복 지정했습니다.")
    runs, excluded, evidence, quality = [], [], {}, []
    campaign_ids, run_ids = set(), set()
    current_contract_hash = file_hash(CONTRACT_FILE)
    for root in roots:
        campaign = read_json(root / "campaign_manifest.json")
        campaign_id = campaign["campaign_id"]
        if campaign_id in campaign_ids:
            raise ValueError("서로 다른 입력 경로의 campaign_id가 중복되었습니다.")
        campaign_ids.add(campaign_id)
        evidence.update(snapshot_inputs(root, campaign))
        if campaign["dataset_purpose"] == "integration_test":
            excluded.append({"campaign_id": campaign_id, "reason": "integration_test 캠페인은 학습에 사용하지 않습니다.",
                             "run_count": len(campaign["runs"])})
            continue
        if campaign["dataset_purpose"] != "controlled_experiment":
            raise ValueError("알 수 없는 데이터 수집 목적입니다.")
        if campaign["operational_contract_sha256"] != current_contract_hash or campaign["operational_contract_snapshot"] != OPERATIONAL_CONTRACT:
            raise ValueError("현재 운영 feature 의미 명세와 다른 캠페인입니다.")
        checked = audit_campaign(root)
        quality.append({"campaign_id": campaign_id, "passed": checked["passed"],
                        "passed_runs": checked["passed_runs"], "run_count": checked["run_count"],
                        "validator_sha256": checked["validator_sha256"],
                        "campaign_manifest_sha256": checked["campaign_manifest_sha256"]})
        if checked["campaign_issues"]:
            raise ValueError(f"캠페인 식별/계획 검증이 실패했습니다: {checked['campaign_issues']}")
        checked_runs = {result["run_id"]: result for result in checked["runs"]}
        for entry in campaign["runs"]:
            result = checked_runs[entry["run_id"]]
            if not result["eligible_for_training"]:
                excluded.append({"campaign_id": campaign_id, "run_id": entry["run_id"],
                                 "scenario_id": entry["scenario_id"], "reason": result["issues"] + result["warnings"]})
                continue
            if entry["run_id"] in run_ids:
                raise ValueError("캠페인 사이에서 run_id가 중복되었습니다.")
            run_ids.add(entry["run_id"])
            run_dir = inside_path(root, f"runs/{entry['run_id']}")
            manifest = read_json(run_dir / "run_manifest.json")
            summary = read_json(run_dir / "collection_summary.json")
            start = utc_time(manifest["created_at_utc"])
            end = utc_time(summary["finished_at_utc"])
            if end < start:
                raise ValueError("실행 종료 시각이 시작 시각보다 이릅니다.")
            rows = []
            for line_index, line in enumerate((run_dir / "features.jsonl").read_text(encoding="utf-8").splitlines(), 1):
                record = CollectedRecord.model_validate(parse_json(line)).model_dump(mode="json")
                payload = record["payload"]
                if payload["event_type"] != "message":
                    continue
                features = {name: payload["features"][name] for name in FEATURE_NAMES}
                rows.append({
                    "features": features, "label": result["label"],
                    "metadata": {
                        "row_id": f"{campaign_id}:{entry['run_id']}:{record['session_id']}:{record['request_index']}",
                        "campaign_id": campaign_id, "run_id": entry["run_id"], "scenario_id": entry["scenario_id"],
                        "repeat_index": entry["repeat_index"], "session_id": record["session_id"],
                        "request_index": record["request_index"], "queue_sequence_id": payload["queue_sequence_id"],
                        "collection_sequence_id": record["collection_sequence_id"], "timestamp": payload["timestamp"],
                        "received_at_utc": record["received_at_utc"], "source_line": line_index,
                        "feature_hash": fingerprint(features),
                    },
                })
            if len(rows) != result["messages"] or not rows:
                raise ValueError("품질 검사와 실제 추출 메시지 수가 다릅니다.")
            runs.append({"campaign_id": campaign_id, "run_id": entry["run_id"], "scenario_id": entry["scenario_id"],
                         "repeat_index": entry["repeat_index"], "start": start, "end": end, "rows": rows})
    if not runs:
        raise ValueError("사용 가능한 운영 메시지 run이 없습니다.")
    return runs, excluded, evidence, quality


def split_runs(runs: list[dict], config: DatasetConfig) -> tuple[dict[str, list[dict]], list[dict]]:
    groups = defaultdict(list)
    for run in runs:
        groups[(run["campaign_id"], run["repeat_index"])].append(run)
    blocks = [{"key": key, "runs": group, "start": min(run["start"] for run in group),
               "end": max(run["end"] for run in group)} for key, group in groups.items()]
    blocks.sort(key=lambda block: (block["start"], block["key"]))
    if len(blocks) < config.validation_blocks + config.test_blocks + 1:
        raise ValueError("train·validation·test를 만들 독립 반복 묶음이 부족합니다. 최소 3개가 필요합니다.")
    for previous, current in zip(blocks, blocks[1:]):
        if previous["end"] > current["start"]:
            raise ValueError("반복 묶음의 시간 범위가 겹칩니다. 순차 수집한 독립 캠페인이 필요합니다.")
    train_end = len(blocks) - config.validation_blocks - config.test_blocks
    validation_end = len(blocks) - config.test_blocks
    assigned = {name: [] for name in SPLITS}
    manifest = []
    for index, block in enumerate(blocks):
        split = "train" if index < train_end else "validation" if index < validation_end else "test"
        block_id = f"{block['key'][0]}:repeat:{block['key'][1]}"
        manifest.append({"block_id": block_id, "split": split, "start_at_utc": block["start"].isoformat(),
                         "end_at_utc": block["end"].isoformat(), "run_ids": sorted(run["run_id"] for run in block["runs"])})
        for run in sorted(block["runs"], key=lambda run: (run["start"], run["run_id"])):
            for row in sorted(run["rows"], key=lambda row: (row["metadata"]["session_id"], row["metadata"]["request_index"])):
                row["metadata"].update(block_id=block_id, split=split)
                assigned[split].append(row)
    all_scenarios = {run["scenario_id"] for run in runs}
    for split, rows in assigned.items():
        if {row["label"] for row in rows} != {0, 1}:
            raise ValueError(f"{split}에 정상·통제된 이상 정답이 모두 필요합니다.")
        if config.require_all_scenarios_each_split and {row["metadata"]["scenario_id"] for row in rows} != all_scenarios:
            raise ValueError(f"{split}에 일부 시나리오가 빠졌습니다. 추가 독립 run을 수집하세요.")
    return assigned, manifest


def validate_partitions(assigned: dict[str, list[dict]]) -> dict:
    overlaps = {}
    for left, right in combinations(SPLITS, 2):
        pair = f"{left}__{right}"
        overlaps[pair] = {}
        for name, key in [
            ("runs", lambda row: row["metadata"]["run_id"]),
            ("sessions", lambda row: (row["metadata"]["run_id"], row["metadata"]["session_id"])),
            ("rows", lambda row: row["metadata"]["row_id"]),
        ]:
            overlaps[pair][name] = len({key(row) for row in assigned[left]} & {key(row) for row in assigned[right]})
    if any(count for checks in overlaps.values() for count in checks.values()):
        raise ValueError("분할 사이에 같은 run/session/메시지 식별자가 존재합니다.")
    for rows in assigned.values():
        ids = [row["metadata"]["row_id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("분할 내부 메시지 식별자가 중복되었습니다.")
    return overlaps


def duplicate_report(assigned: dict[str, list[dict]]) -> dict:
    groups = defaultdict(list)
    for split, rows in assigned.items():
        for row in rows:
            groups[row["metadata"]["feature_hash"]].append((split, row["label"]))
    cross_split = {key: values for key, values in groups.items() if len({split for split, _ in values}) > 1}
    conflicts = {key: values for key, values in groups.items() if len({label for _, label in values}) > 1}
    return {
        "duplicate_feature_groups": sum(len(values) > 1 for values in groups.values()),
        "cross_split_feature_groups": len(cross_split),
        "cross_split_feature_rows": sum(len(values) for values in cross_split.values()),
        "conflicting_label_feature_groups": len(conflicts),
        "pairwise_shared_feature_groups": {f"{left}__{right}": len(
            {row["metadata"]["feature_hash"] for row in assigned[left]} &
            {row["metadata"]["feature_hash"] for row in assigned[right]}
        ) for left, right in combinations(SPLITS, 2)},
    }


def split_summary(rows: list[dict]) -> dict:
    return {
        "rows": len(rows), "runs": len({row["metadata"]["run_id"] for row in rows}),
        "sessions": len({(row["metadata"]["run_id"], row["metadata"]["session_id"]) for row in rows}),
        "label_distribution": {str(label): sum(row["label"] == label for row in rows) for label in (0, 1)},
        "scenario_distribution": dict(sorted(Counter(row["metadata"]["scenario_id"] for row in rows).items())),
    }


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def build_dataset(config: DatasetConfig) -> dict:
    output_dir = config.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError("이미 존재하는 데이터셋은 덮어쓰지 않습니다. 새 --output-dir를 지정하세요.")
    if any(output_dir.is_relative_to(root.resolve()) for root in config.campaign_dirs):
        raise ValueError("원본 캠페인 안에는 데이터셋을 저장하지 않습니다.")
    if FEATURE_NAMES != MESSAGE_FEATURE_ORDER or FEATURE_NAMES != list(MessageFeatureValues.model_fields):
        raise ValueError("운영 명세와 API/transformer의 메시지 입력 순서가 다릅니다.")
    if config.feature_contract_id != OPERATIONAL_CONTRACT["events"]["message"]["feature_contract_id"]:
        raise ValueError("설정과 운영 명세의 feature 의미 버전이 다릅니다.")
    if read_json(CONTRACT_FILE) != OPERATIONAL_CONTRACT:
        raise ValueError("명세 파일이 모듈을 불러온 뒤 변경되었습니다. 다시 실행하세요.")
    contract_hash = file_hash(CONTRACT_FILE)
    runs, excluded, evidence, quality = load_runs(config)
    evidence[str(CONTRACT_FILE)] = contract_hash
    assigned, blocks = split_runs(runs, config)
    overlaps = validate_partitions(assigned)
    duplicates = duplicate_report(assigned)
    if config.duplicate_feature_policy == "error" and duplicates["cross_split_feature_groups"]:
        raise ValueError("분할 사이에 같은 feature 벡터가 있습니다. 중복 정책에 따라 중단합니다.")
    summaries = {split: split_summary(rows) for split, rows in assigned.items()}
    train_statistics = {
        name: {"minimum": min(row["features"][name] for row in assigned["train"]),
               "maximum": max(row["features"][name] for row in assigned["train"]),
               "unique_values": len({row["features"][name] for row in assigned["train"]})}
        for name in FEATURE_NAMES
    }
    # 검증과 추출 사이에 파일이 바뀌면 하나의 검증된 원본으로 만들어졌다고 주장하지 않는다.
    for path, digest in evidence.items():
        if not Path(path).is_file() or file_hash(Path(path)) != digest:
            raise ValueError("데이터셋 구성 도중 원본 파일이 변경되었습니다.")
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for split, rows in assigned.items():
        for name, columns, values in [
            (f"X_{split}.csv", FEATURE_NAMES, [row["features"] for row in rows]),
            (f"y_{split}.csv", ["label"], [{"label": row["label"]} for row in rows]),
            (f"metadata_{split}.csv", METADATA_COLUMNS, [row["metadata"] for row in rows]),
        ]:
            path = output_dir / name
            write_csv(path, columns, values)
            outputs[name] = {"rows": len(rows), "columns": columns, "sha256": file_hash(path)}
    event = OPERATIONAL_CONTRACT["events"]["message"]
    schema = {
        "schema_version": 1, "dataset_id": config.dataset_id, "event_type": "message",
        "feature_contract_id": event["feature_contract_id"], "wire_schema_version": OPERATIONAL_CONTRACT["wire_schema_version"],
        "capture_layer": OPERATIONAL_CONTRACT["capture_layer"], "observation_scope": event["observation_scope"],
        "raw_input_features": FEATURE_NAMES, "numeric_features": FEATURE_NAMES,
        "feature_units": {name: OPERATIONAL_CONTRACT["features"][name]["unit"] for name in FEATURE_NAMES},
        "feature_dtypes": {name: OPERATIONAL_CONTRACT["features"][name]["dtype"] for name in FEATURE_NAMES},
        "missing_value_policy": "reject_without_zero_fill",
        "preprocessing": {"status": "not_fitted", "fit_scope": "train_only_in_training_stage"},
        "excluded_model_metadata": METADATA_COLUMNS + ["label", "session_summary", "last_error_message", "packet_id"],
    }
    assignments = [{
        "campaign_id": run["campaign_id"], "run_id": run["run_id"], "scenario_id": run["scenario_id"],
        "repeat_index": run["repeat_index"], "split": run["rows"][0]["metadata"]["split"],
        "block_id": run["rows"][0]["metadata"]["block_id"], "rows": len(run["rows"]),
        "sessions": len({row["metadata"]["session_id"] for row in run["rows"]}),
    } for run in sorted(runs, key=lambda run: (run["start"], run["run_id"]))]
    documents = {
        "feature_schema.json": schema,
        "label_map.json": {"normal": 0, "controlled_anomaly": 1},
        "split_manifest.json": {"split_policy": config.split_policy, "blocks": blocks, "runs": assignments,
                                "identity_overlaps": overlaps, "duplicate_features": duplicates},
    }
    for name, document in documents.items():
        path = output_dir / name
        write_json(path, document)
        outputs[name] = {"sha256": file_hash(path)}
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_id": config.dataset_id, "status": "complete", "output_dir": str(output_dir),
        "feature_contract_id": config.feature_contract_id, "feature_count": len(FEATURE_NAMES),
        "feature_order": FEATURE_NAMES, "total_message_rows": sum(item["rows"] for item in summaries.values()),
        "selected_runs": len(runs), "splits": summaries, "blocks": blocks,
        "identity_overlaps": overlaps, "duplicate_features": duplicates,
        "excluded_sources": excluded, "source_quality": quality,
        "training_feature_statistics": train_statistics,
        "constant_training_features": [name for name, values in train_statistics.items() if values["unique_values"] == 1],
        "preprocessing_fitted": False, "model_training_performed": False,
        "config_snapshot": config.model_dump(mode="json"), "source_file_sha256": evidence,
        "contract_sha256": file_hash(CONTRACT_FILE), "producer_sha256": file_hash(Path(__file__)),
        "output_files": outputs,
        "limitations": [
            "정답은 통제된 시나리오 의도이며 실제 침입 탐지 정답이 아니다.",
            "초기 3개 반복 묶음은 비율보다 독립성을 우선해 1:1:1로 분리한다. 충분한 데이터 규모를 보장하지 않는다.",
            "run/session 비중복은 같은 시나리오·생성기·서버에 대한 편향이나 분포 이동을 해결하지 않는다.",
            "동일 feature 벡터는 별도로 보고한다. 같은 feature를 가진 서로 다른 실행을 자동 삭제하거나 정답을 바꾸지 않는다.",
            "종료 요약은 원본 품질 확인에만 사용하며 메시지 입력이나 정답 생성에 사용하지 않는다.",
            "test는 최종 평가용이다. 모델 설정·threshold 선택 및 전처리 fit에는 사용하지 않는다.",
            "추후 LSTM 창은 분할 완료 후 각 split의 run/session 내부에서만 구성해야 한다.",
        ],
    }
    # 모든 입력·정답·메타데이터·schema 저장이 성공한 뒤에만 완료 manifest를 기록한다.
    write_json(output_dir / "dataset_manifest.json", report)
    return report


def save_report(report: dict, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(prefix.with_suffix(".json"), report)
    lines = [
        "# 운영 메시지 데이터셋 구성 및 분할", "",
        f"데이터셋: `{report['dataset_id']}`", f"저장 위치: `{report['output_dir']}`", "",
        f"의미 버전: `{report['feature_contract_id']}`, 입력 {report['feature_count']}개, 메시지 {report['total_message_rows']}건", "",
        "## 고정 분할 결과", "",
        "| 분할 | run | 세션 | 메시지 | 정상 0 | 통제된 이상 1 |", "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split in SPLITS:
        values = report["splits"][split]
        lines.append(f"| {split} | {values['runs']} | {values['sessions']} | {values['rows']} | {values['label_distribution']['0']} | {values['label_distribution']['1']} |")
    lines += ["", "행을 무작위로 섞지 않는다. 실제 수집 시작·종료 시각으로 정렬한 반복 묶음의 앞부분은 train, 마지막 지정 묶음은 validation/test로 고정한다.", "", "## 분할 사이 식별자 중복", "", "| 비교 | run 중복 | 세션 중복 | 메시지 중복 |", "| --- | ---: | ---: | ---: |"]
    for pair, values in report["identity_overlaps"].items():
        lines.append(f"| {pair} | {values['runs']} | {values['sessions']} | {values['rows']} |")
    duplicates = report["duplicate_features"]
    lines += ["", "식별자 비중복과 feature 값 비중복은 다른 검사다.",
              f"전체 중복 feature 그룹: {duplicates['duplicate_feature_groups']}, 분할 간 같은 feature 그룹: {duplicates['cross_split_feature_groups']}, 서로 다른 정답의 같은 feature 그룹: {duplicates['conflicting_label_feature_groups']}", "",
              "## 입력과 전처리", "", f"입력 순서: `{', '.join(report['feature_order'])}`", "",
              "정답과 메타데이터는 별도 CSV다. 종료 요약, 오류 문구, 시나리오·세션 ID, timestamp를 모델 입력에 넣지 않는다. 이번 단계에서 scaler나 모델을 fit하지 않았다.",
              f"train에서 값이 하나뿐인 입력: `{', '.join(report['constant_training_features']) or '없음'}`", "",
              "## 제외한 원본", ""]
    for source in report["excluded_sources"]:
        lines.append(f"- `{source.get('run_id', source['campaign_id'])}`: {source['reason']}")
    if not report["excluded_sources"]:
        lines.append("제외한 원본이 없다.")
    lines += ["", "## 저장 및 재현", "",
              "X/y/metadata 세 파일의 같은 행 위치가 같은 메시지다. 파일 SHA-256, 원본 SHA-256과 split_manifest를 dataset_manifest에 보관한다. 결과를 덮어쓰지 않고 새 경로로 버전을 만든다.", "",
              "## 해석 범위", ""]
    lines.extend(f"- {message}" for message in report["limitations"])
    lines += ["", "## 다음 작업", "", "train만으로 전처리기를 fit하고 운영 Logistic Regression/RandomForest를 학습한다. 모델 설정과 threshold는 validation에서 선택하고 test는 최종 비교까지 보관한다.", ""]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
