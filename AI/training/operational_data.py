from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from data_collection.quality import parse_json
from data_preparation.operational_dataset import FEATURE_NAMES, METADATA_COLUMNS, file_hash, fingerprint
from evaluation.audit_feature_alignment import assess_model_schema
from inference_api.payload_validation import CONTRACT_FILE, OPERATIONAL_CONTRACT
from inference_api.schemas import MessageFeatureValues


@dataclass
class OperationalSplit:
    X: pd.DataFrame
    y: np.ndarray
    metadata: pd.DataFrame


@dataclass
class OperationalData:
    dataset_id: str
    schema: dict
    label_map: dict
    train: OperationalSplit
    validation: OperationalSplit
    input_sha256: dict[str, str]


def load_operational_data(directory: Path, dataset_id: str) -> OperationalData:
    # 최종 평가를 보호하려고 허용한 파일만 읽는다. test CSV는 해시 검사에도 열지 않는다.
    manifest_bytes = (directory / "dataset_manifest.json").read_bytes()
    manifest = parse_json(manifest_bytes.decode("utf-8"))
    evidence = {"dataset_manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}
    if manifest.get("status") != "complete" or manifest.get("dataset_id") != dataset_id:
        raise ValueError("완료된 지정 데이터셋 manifest가 필요합니다.")
    event = OPERATIONAL_CONTRACT["events"]["message"]
    if (manifest.get("feature_order") != FEATURE_NAMES or
            manifest.get("feature_contract_id") != event["feature_contract_id"] or
            manifest.get("contract_sha256") != file_hash(CONTRACT_FILE) or
            manifest.get("preprocessing_fitted") is not False):
        raise ValueError("데이터셋의 입력 순서·의미 버전·전처리 상태가 현재 계약과 다릅니다.")

    def read_verified(name: str) -> bytes:
        content = (directory / name).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if manifest["output_files"][name]["sha256"] != digest:
            raise ValueError(f"데이터 파일 SHA-256 불일치: {name}")
        evidence[name] = digest
        return content

    schema = parse_json(read_verified("feature_schema.json").decode("utf-8"))
    label_map = parse_json(read_verified("label_map.json").decode("utf-8"))
    assignments = parse_json(read_verified("split_manifest.json").decode("utf-8"))
    compatible = assess_model_schema(OPERATIONAL_CONTRACT, "message", schema)
    expected_dtypes = {name: OPERATIONAL_CONTRACT["features"][name]["dtype"] for name in FEATURE_NAMES}
    if (not compatible["schema_compatible"] or schema.get("dataset_id") != dataset_id or
            schema.get("feature_dtypes") != expected_dtypes or
            schema.get("missing_value_policy") != "reject_without_zero_fill" or
            schema.get("preprocessing", {}).get("status") != "not_fitted"):
        raise ValueError("운영 feature schema가 현재 입력 계약과 호환되지 않습니다.")
    if label_map != {"normal": 0, "controlled_anomaly": 1}:
        raise ValueError("운영 정답은 normal=0, controlled_anomaly=1이어야 합니다.")

    def read_csv(name: str, columns: list[str]) -> pd.DataFrame:
        frame = pd.read_csv(io.BytesIO(read_verified(name)), dtype=str, keep_default_na=False)
        description = manifest["output_files"][name]
        if (list(frame.columns) != columns or description["columns"] != columns or
                len(frame) != description["rows"] or frame.empty or frame.eq("").any().any()):
            raise ValueError(f"CSV 열·행 수·빈 값 검사 실패: {name}")
        return frame

    partitions = {}
    for split in ("train", "validation"):
        raw = read_csv(f"X_{split}.csv", FEATURE_NAMES)
        targets = read_csv(f"y_{split}.csv", ["label"])
        metadata = read_csv(f"metadata_{split}.csv", METADATA_COLUMNS)
        if not (len(raw) == len(targets) == len(metadata) == manifest["splits"][split]["rows"]):
            raise ValueError(f"X/y/metadata 행 수가 다릅니다: {split}")
        if set(targets["label"]) != {"0", "1"}:
            raise ValueError(f"정답은 0/1 두 클래스가 모두 필요합니다: {split}")
        if metadata["row_id"].duplicated().any() or set(metadata["split"]) != {split}:
            raise ValueError(f"메시지 중복 또는 split metadata 불일치: {split}")
        if metadata.duplicated(["run_id", "session_id", "request_index"]).any():
            raise ValueError(f"같은 세션 메시지 식별자가 중복되었습니다: {split}")

        values = []
        for index, row in raw.iterrows():
            # 정수는 해시 대조까지 정수로 보존하고, 학습 배열을 만들 때만 float64로 변환한다.
            typed = {name: int(row[name]) if expected_dtypes[name] == "integer" else float(row[name])
                     for name in FEATURE_NAMES}
            typed = MessageFeatureValues.model_validate(typed).model_dump()
            if fingerprint(typed) != metadata.at[index, "feature_hash"]:
                raise ValueError(f"입력과 metadata 행 정렬 또는 feature 해시 불일치: {split}/{index}")
            if typed["packet_size"] != typed["payload_size"] + OPERATIONAL_CONTRACT["application_header_size_bytes"]:
                raise ValueError("메시지 프레임과 payload 크기 관계가 다릅니다.")
            values.append([typed[name] for name in FEATURE_NAMES])

        run_entries = {item["run_id"]: item for item in assignments["runs"] if item["split"] == split}
        if set(run_entries) != set(metadata["run_id"]):
            raise ValueError(f"고정 run 배치와 metadata가 다릅니다: {split}")
        for run_id, rows in metadata.groupby("run_id", sort=False):
            entry = run_entries[run_id]
            if len(rows) != entry["rows"] or rows["session_id"].nunique() != entry["sessions"]:
                raise ValueError("run별 메시지·세션 수가 고정 분할과 다릅니다.")
            for name in ("campaign_id", "scenario_id", "repeat_index", "block_id"):
                if set(rows[name]) != {str(entry[name])}:
                    raise ValueError(f"run 배치 정보가 다릅니다: {name}")
        y = targets["label"].astype("int64").to_numpy()
        distribution = {str(label): int((y == label).sum()) for label in (0, 1)}
        if distribution != manifest["splits"][split]["label_distribution"]:
            raise ValueError(f"정답 분포가 고정 manifest와 다릅니다: {split}")
        partitions[split] = OperationalSplit(pd.DataFrame(values, columns=FEATURE_NAMES, dtype="float64"), y, metadata)

    train, validation = partitions["train"], partitions["validation"]
    for columns in (["run_id"], ["run_id", "session_id"], ["row_id"]):
        left = set(train.metadata[columns].itertuples(index=False, name=None))
        right = set(validation.metadata[columns].itertuples(index=False, name=None))
        if left & right:
            raise ValueError(f"train/validation 식별자 누출: {columns}")
    return OperationalData(dataset_id, schema, label_map, train, validation, evidence)
