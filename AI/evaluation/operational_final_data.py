from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from data_collection.augmentation import verify_hashes
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, METADATA_COLUMNS, file_hash, fingerprint
from data_preparation.operational_sequences import GROUP_COLUMNS, SEQUENCE_COLUMNS, SequenceSplit
from inference_api.payload_validation import OPERATIONAL_CONTRACT
from inference_api.schemas import MessageFeatureValues
from training.operational_data import OperationalData, OperationalSplit
from training.train_operational_logistic_regression import write_json


TEST_FILES = ("X_test.csv", "y_test.csv", "metadata_test.csv")


def expected_test_hashes(directory: Path) -> dict:
    # 이미 공개된 manifest의 예상 해시만 읽는다. 이 단계에서는 Test CSV를 열지 않는다.
    manifest = read_json(directory / "dataset_manifest.json")
    return {name: manifest["output_files"][name]["sha256"] for name in TEST_FILES}


def holdout_key(expected: dict) -> str:
    return hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def reserve_holdout(registry: Path, gate_path: Path, gate_hash: str) -> tuple[Path, bool]:
    if file_hash(gate_path) != gate_hash:
        raise ValueError("Test 접근 전 후보 잠금 해시가 변경됐습니다.")
    gate = read_json(gate_path)
    if gate["status"] != "candidate_locked_before_test" or gate["test_files_read"] is not False:
        raise ValueError("Test를 열기 전에 후보를 먼저 잠가야 합니다.")
    path = registry / f"{holdout_key(gate['expected_test_sha256'])}.json"
    registry.mkdir(parents=True, exist_ok=True)
    record = {"status": "reserved_after_candidate_lock", "reserved_at_utc": datetime.now(UTC).isoformat(),
              "expected_test_sha256": gate["expected_test_sha256"], "policy_signature": gate["policy_signature"],
              "selected_model_id": gate["selected_model_id"], "first_candidate_lock": str(gate_path.resolve()),
              "first_candidate_lock_sha256": gate_hash,
              "notice": "접근 예약도 holdout 사용으로 기록한다. 재실행은 같은 고정 정책 검증이며 새 독립 평가가 아니다."}
    try:
        write_json(path, record)
        previously_used = False
    except FileExistsError:
        existing = read_json(path)
        if (existing["expected_test_sha256"] != record["expected_test_sha256"] or
                existing["policy_signature"] != record["policy_signature"] or
                existing["selected_model_id"] != record["selected_model_id"]):
            raise ValueError("이미 사용 예약된 Test입니다. 모델·threshold·승인 기준 변경에는 새 독립 평가가 필요합니다.")
        previously_used = True
    return path, previously_used


def load_test(directory: Path, data: OperationalData, gate_path: Path, gate_hash: str, receipt: Path) -> tuple[OperationalSplit, dict]:
    if file_hash(gate_path) != gate_hash:
        raise ValueError("Test 접근 전 후보 잠금 해시가 다릅니다.")
    gate = read_json(gate_path)
    record = read_json(receipt)
    if (gate["status"] != "candidate_locked_before_test" or gate["test_files_read"] is not False or
            record["status"] != "reserved_after_candidate_lock" or record["policy_signature"] != gate["policy_signature"] or
            record["expected_test_sha256"] != gate["expected_test_sha256"] or record["selected_model_id"] != gate["selected_model_id"]):
        raise ValueError("Test 후보 잠금·holdout 사용 기록이 일치하지 않습니다.")
    verify_hashes(gate["pre_test_protected_sha256"])
    manifest = read_json(directory / "dataset_manifest.json")
    if (file_hash(directory / "dataset_manifest.json") != data.input_sha256["dataset_manifest.json"] or
            expected_test_hashes(directory) != gate["expected_test_sha256"] or manifest["dataset_id"] != data.dataset_id):
        raise ValueError("잠근 원본 Test manifest가 현재 데이터셋과 다릅니다.")
    evidence, frames = {}, {}
    for name, columns in zip(TEST_FILES, (FEATURE_NAMES, ["label"], METADATA_COLUMNS), strict=True):
        content = (directory / name).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != gate["expected_test_sha256"][name]:
            raise ValueError(f"Test 파일 SHA-256 불일치: {name}")
        evidence[name] = digest
        frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        description = manifest["output_files"][name]
        if (list(frame.columns) != columns or description["columns"] != columns or frame.empty or
                len(frame) != description["rows"] or frame.eq("").any().any()):
            raise ValueError(f"Test CSV 행·열·빈 값이 다릅니다: {name}")
        frames[name] = frame
    raw, targets, metadata = (frames[name] for name in TEST_FILES)
    if (not (len(raw) == len(targets) == len(metadata) == manifest["splits"]["test"]["rows"]) or
            set(targets.label) != {"0", "1"} or set(metadata["split"]) != {"test"} or
            metadata.row_id.duplicated().any() or metadata.duplicated(GROUP_COLUMNS + ["request_index"]).any()):
        raise ValueError("Test X/y/metadata 정렬·정답·분할·중복 검사가 실패했습니다.")
    expected_dtypes = data.schema["feature_dtypes"]
    values = []
    for index, row in raw.iterrows():
        typed = {name: int(row[name]) if expected_dtypes[name] == "integer" else float(row[name]) for name in FEATURE_NAMES}
        typed = MessageFeatureValues.model_validate(typed).model_dump()
        if (fingerprint(typed) != metadata.at[index, "feature_hash"] or
                typed["packet_size"] != typed["payload_size"] + OPERATIONAL_CONTRACT["application_header_size_bytes"]):
            raise ValueError("Test 입력 해시·행 정렬·프레임 크기가 다릅니다.")
        values.append([typed[name] for name in FEATURE_NAMES])
    assignments = read_json(directory / "split_manifest.json")
    entries = {entry["run_id"]: entry for entry in assignments["runs"] if entry["split"] == "test"}
    if set(entries) != set(metadata.run_id):
        raise ValueError("Test run 배치가 사전 분할과 다릅니다.")
    for run_id, rows in metadata.groupby("run_id", sort=False):
        entry = entries[run_id]
        if len(rows) != entry["rows"] or rows.session_id.nunique() != entry["sessions"]:
            raise ValueError("Test run의 메시지·세션 수가 다릅니다.")
        for name in ("campaign_id", "scenario_id", "repeat_index", "block_id"):
            if set(rows[name]) != {str(entry[name])}:
                raise ValueError(f"Test run 배치 정보가 다릅니다: {name}")
    y = targets.label.astype("int64").to_numpy()
    if {str(label): int((y == label).sum()) for label in (0, 1)} != manifest["splits"]["test"]["label_distribution"]:
        raise ValueError("Test 정답 분포가 고정 manifest와 다릅니다.")
    for name, columns in (("캠페인", ["campaign_id"]), ("run", ["run_id"]), ("세션", GROUP_COLUMNS), ("행", ["row_id"])):
        test_ids = set(metadata[columns].itertuples(index=False, name=None))
        for other in (data.train, data.validation):
            if test_ids & set(other.metadata[columns].itertuples(index=False, name=None)):
                raise ValueError(f"Test와 Train/Validation의 {name}이 섞여 있습니다.")
    return OperationalSplit(pd.DataFrame(values, columns=FEATURE_NAMES, dtype="float64"), y, metadata), evidence


def make_evaluation_windows(source: OperationalSplit, width: int, dataset_id: str) -> SequenceSplit:
    # 학습용 로더의 Test 금지와 과거 코드 해시를 유지하려고 최종 평가의 창 구성만 분리한다.
    if (type(width) is not int or not 1 <= width <= 64 or list(source.X.columns) != FEATURE_NAMES or
            source.y.shape != (len(source.X),) or len(source.metadata) != len(source.X) or not len(source.X) or
            not np.isfinite(source.X.to_numpy()).all() or (source.X.to_numpy() < 0).any() or
            not np.issubdtype(source.y.dtype, np.integer) or not np.isin(source.y, [0, 1]).all()):
        raise ValueError("최종 평가 창의 입력·정답·크기·수치가 올바르지 않습니다.")
    metadata = source.metadata.reset_index(drop=True).copy()
    if (list(metadata.columns) != METADATA_COLUMNS or metadata.isna().any().any() or metadata.astype(str).eq("").any().any() or
            metadata.row_id.duplicated().any() or metadata["split"].nunique() != 1 or
            metadata["split"].iloc[0] not in {"train", "validation", "test"} or
            (metadata.groupby("run_id").campaign_id.nunique() > 1).any()):
        raise ValueError("최종 평가 창의 식별자·분할·metadata가 다릅니다.")
    indices = metadata.request_index.astype("int64").to_numpy()
    if not np.array_equal(metadata.request_index.astype(str).to_numpy(), indices.astype(str)):
        raise ValueError("메시지 번호는 정수여야 합니다.")
    raw = source.X.to_numpy(dtype="float64")
    X = np.zeros((len(raw), width, len(FEATURE_NAMES)), dtype="float64")
    positions = np.full((len(raw), width), -1, dtype="int64")
    lengths, starts, traces = np.zeros(len(raw), dtype="int64"), np.zeros(len(raw), dtype="int64"), [None] * len(raw)
    for group in metadata.groupby(GROUP_COLUMNS, sort=False).indices.values():
        group = np.asarray(group, dtype="int64")
        group = group[np.argsort(indices[group], kind="stable")]
        if not np.array_equal(indices[group], np.arange(1, len(group) + 1)):
            raise ValueError("평가 세션의 메시지 번호에 누락·중복이 있습니다.")
        for name in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total"):
            if (np.diff(source.X.iloc[group][name].to_numpy()) < 0).any():
                raise ValueError("평가 세션의 누적 수치가 감소합니다.")
        for offset, anchor in enumerate(group):
            history = group[max(0, offset + 1 - width):offset + 1]
            length = len(history)
            X[anchor, :length], positions[anchor, :length] = raw[history], history
            lengths[anchor], starts[anchor] = length, offset + 2 - length
            traces[anchor] = json.dumps(metadata.iloc[history].row_id.tolist(), ensure_ascii=False, separators=(",", ":"))
    metadata["window_id"] = dataset_id + ":" + metadata.row_id
    metadata["context_length"], metadata["context_start_request_index"], metadata["source_row_ids"] = lengths, starts, traces
    return SequenceSplit(X, source.y.copy(), lengths, np.arange(width)[None, :] < lengths[:, None], positions, metadata)


def validate_evaluation_windows(windows: SequenceSplit, source: OperationalSplit, width: int, dataset_id: str) -> None:
    expected = make_evaluation_windows(source, width, dataset_id)
    for name in ("X", "y", "lengths", "mask", "source_positions"):
        actual, correct = getattr(windows, name), getattr(expected, name)
        if actual.dtype != correct.dtype or not np.array_equal(actual, correct):
            raise ValueError(f"평가 창에 미래·다른 세션·잘못된 padding·정답이 있습니다: {name}")
    if (list(windows.metadata.columns) != METADATA_COLUMNS + SEQUENCE_COLUMNS or
            not windows.metadata.astype(str).reset_index(drop=True).equals(expected.metadata.astype(str))):
        raise ValueError("평가 창의 원본 추적·행 순서가 다릅니다.")
