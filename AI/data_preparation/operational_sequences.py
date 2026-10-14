from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, METADATA_COLUMNS, file_hash
from training.operational_data import OperationalSplit, load_operational_data
from training.train_operational_logistic_regression import write_json


SPLITS = ("train", "validation")
SEQUENCE_CONTRACT = "server_application_message_sequence_v1"
GROUP_COLUMNS = ["campaign_id", "run_id", "session_id"]
SEQUENCE_COLUMNS = ["window_id", "context_length", "context_start_request_index", "source_row_ids"]
ARRAY_NAMES = {"X", "y", "lengths", "mask", "source_positions"}


class SequenceConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    dataset_id: Identifier
    source_dataset_id: Identifier
    source_dataset_dir: Path
    output_dir: Path
    window_size: Annotated[int, Field(ge=1, le=64, strict=True)] = 8
    stride: Literal[1] = 1
    padding_side: Literal["right"] = "right"
    target_policy: Literal["current_message_label"] = "current_message_label"
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)] = 1538
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)] = 513


@dataclass
class SequenceSplit:
    X: np.ndarray
    y: np.ndarray
    lengths: np.ndarray
    mask: np.ndarray
    source_positions: np.ndarray
    metadata: pd.DataFrame


def load_config(path: Path) -> SequenceConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("source_dataset_dir", "output_dir"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return SequenceConfig.model_validate(values)


def ordered_groups(source: OperationalSplit) -> list[np.ndarray]:
    if (list(source.X.columns) != FEATURE_NAMES or not len(source.X) or
            len(source.X) != len(source.metadata) or source.y.shape != (len(source.X),) or
            not np.issubdtype(source.y.dtype, np.integer) or not np.isin(source.y, [0, 1]).all() or
            not np.isfinite(source.X.to_numpy()).all() or (source.X.to_numpy() < 0).any()):
        raise ValueError("시계열 원본의 입력 순서·행 수·정답·수치가 올바르지 않습니다.")
    metadata = source.metadata.reset_index(drop=True)
    if (not set(METADATA_COLUMNS).issubset(metadata.columns) or metadata[METADATA_COLUMNS].isna().any().any() or
            metadata[METADATA_COLUMNS].astype(str).eq("").any().any() or metadata.row_id.duplicated().any() or
            metadata["split"].nunique() != 1 or metadata["split"].iloc[0] not in SPLITS):
        raise ValueError("시계열 metadata에 누락·중복·분할 혼입이 있습니다.")
    if (metadata.groupby("run_id")["campaign_id"].nunique() > 1).any():
        raise ValueError("하나의 run에 서로 다른 캠페인이 섞여 있습니다.")
    try:
        indices = metadata.request_index.astype("int64").to_numpy()
        if not np.array_equal(metadata.request_index.astype(str).to_numpy(), indices.astype(str)):
            raise ValueError("정수 메시지 번호만 허용합니다.")
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("메시지 번호는 1부터 시작하는 정수여야 합니다.") from error
    groups = []
    for positions in metadata.groupby(GROUP_COLUMNS, sort=False).indices.values():
        positions = np.asarray(positions, dtype="int64")
        positions = positions[np.argsort(indices[positions], kind="stable")]
        if not np.array_equal(indices[positions], np.arange(1, len(positions) + 1)):
            raise ValueError("세션 메시지 번호에 누락·중복이 있습니다. 창으로 연결하지 않습니다.")
        ordered = source.X.iloc[positions]
        for name in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total"):
            if np.any(np.diff(ordered[name].to_numpy()) < 0):
                raise ValueError(f"세션의 누적 입력이 감소합니다: {name}")
        groups.append(positions)
    return groups


def make_windows(source: OperationalSplit, window_size: int, dataset_id: str) -> SequenceSplit:
    if type(window_size) is not int or not 1 <= window_size <= 64:
        raise ValueError("창 크기는 1~64 사이의 정수여야 합니다.")
    groups = ordered_groups(source)
    count = len(source.X)
    raw = source.X.to_numpy(dtype="float64")
    X = np.zeros((count, window_size, len(FEATURE_NAMES)), dtype="float64")
    lengths = np.zeros(count, dtype="int64")
    positions = np.full((count, window_size), -1, dtype="int64")
    metadata = source.metadata.reset_index(drop=True).copy()
    starts = np.zeros(count, dtype="int64")
    row_ids = metadata.row_id.to_numpy()
    traces = [None] * count
    for group in groups:
        for offset, anchor in enumerate(group):
            history = group[max(0, offset + 1 - window_size):offset + 1]
            length = len(history)
            # 현재까지 관측된 메시지만 앞쪽에 놓는다. 뒤쪽 0은 mask로 제외할 padding이지 결측값 대체가 아니다.
            X[anchor, :length] = raw[history]
            positions[anchor, :length] = history
            lengths[anchor] = length
            starts[anchor] = offset + 2 - length
            traces[anchor] = json.dumps(row_ids[history].tolist(), ensure_ascii=False, separators=(",", ":"))
    mask = np.arange(window_size)[None, :] < lengths[:, None]
    metadata["window_id"] = dataset_id + ":" + metadata.row_id
    metadata["context_length"] = lengths
    metadata["context_start_request_index"] = starts
    metadata["source_row_ids"] = traces
    windows = SequenceSplit(X, source.y.copy(), lengths, mask, positions, metadata)
    validate_windows(windows, source, window_size, dataset_id)
    return windows


def validate_windows(windows: SequenceSplit, source: OperationalSplit, window_size: int, dataset_id: str) -> None:
    count = len(source.X)
    expected_shapes = {"X": (count, window_size, len(FEATURE_NAMES)), "y": (count,), "lengths": (count,),
                       "mask": (count, window_size), "source_positions": (count, window_size)}
    for name, shape in expected_shapes.items():
        array = getattr(windows, name)
        expected_dtype = np.dtype("float64") if name == "X" else np.dtype("bool") if name == "mask" else np.dtype("int64")
        if array.shape != shape or array.dtype != expected_dtype:
            raise ValueError(f"시계열 배열의 shape·dtype가 다릅니다: {name}")
    if (not np.isfinite(windows.X).all() or not np.array_equal(windows.y, source.y) or
            np.any((windows.lengths < 1) | (windows.lengths > window_size)) or
            not np.array_equal(windows.mask, np.arange(window_size)[None, :] < windows.lengths[:, None]) or
            np.any(windows.X[~windows.mask] != 0) or np.any(windows.source_positions[~windows.mask] != -1)):
        raise ValueError("정답·유효 길이·mask·padding 계약이 다릅니다.")
    if list(windows.metadata.columns) != METADATA_COLUMNS + SEQUENCE_COLUMNS:
        raise ValueError("시계열 metadata 열 순서가 다릅니다.")
    source_metadata = source.metadata.reset_index(drop=True)
    if not windows.metadata[METADATA_COLUMNS].astype(str).reset_index(drop=True).equals(source_metadata[METADATA_COLUMNS].astype(str)):
        raise ValueError("현재 메시지와 시계열 anchor의 행 순서·metadata가 다릅니다.")
    raw = source.X.to_numpy(dtype="float64")
    for group in ordered_groups(source):
        for offset, anchor in enumerate(group):
            expected = group[max(0, offset + 1 - window_size):offset + 1]
            length = len(expected)
            meta = windows.metadata.iloc[anchor]
            if (windows.lengths[anchor] != length or not np.array_equal(windows.source_positions[anchor, :length], expected) or
                    not np.array_equal(windows.X[anchor, :length], raw[expected]) or
                    str(meta.window_id) != f"{dataset_id}:{source_metadata.iloc[anchor].row_id}" or
                    int(meta.context_length) != length or int(meta.context_start_request_index) != offset + 2 - length or
                    read_trace(meta.source_row_ids) != source_metadata.iloc[expected].row_id.tolist()):
                raise ValueError("시계열 창에 미래·다른 세션·잘못된 순서 또는 원본 값이 포함됐습니다.")


def read_trace(value: str) -> list[str]:
    result = json.loads(value)
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ValueError("창의 원본 행 추적은 문자열 목록이어야 합니다.")
    return result


def split_summary(windows: SequenceSplit) -> dict:
    metadata = windows.metadata
    width = windows.X.shape[1]
    scenarios = {}
    for scenario, rows in metadata.groupby("scenario_id", sort=True):
        indices = rows.index.to_numpy()
        scenarios[scenario] = {"windows": len(rows), "partial_windows": int((windows.lengths[indices] < width).sum()),
                               "length_one_windows": int((windows.lengths[indices] == 1).sum()),
                               "normal": int((windows.y[indices] == 0).sum()), "controlled_anomaly": int((windows.y[indices] == 1).sum())}
    return {"shape": list(windows.X.shape), "windows": len(windows.y),
            "campaigns": metadata.campaign_id.nunique(), "runs": metadata.run_id.nunique(),
            "sessions": len(metadata[GROUP_COLUMNS].drop_duplicates()),
            "label_distribution": {str(label): int((windows.y == label).sum()) for label in (0, 1)},
            "full_windows": int((windows.lengths == width).sum()), "partial_windows": int((windows.lengths < width).sum()),
            "length_one_windows": int((windows.lengths == 1).sum()),
            "context_length_distribution": {str(length): int((windows.lengths == length).sum()) for length in range(1, width + 1)},
            "valid_steps": int(windows.mask.sum()), "padded_steps": int((~windows.mask).sum()), "by_scenario": scenarios}


def save_split(directory: Path, split: str, windows: SequenceSplit) -> None:
    with (directory / f"{split}.npz").open("xb") as stream:
        np.savez_compressed(stream, **{name: getattr(windows, name) for name in sorted(ARRAY_NAMES)})
    windows.metadata.to_csv(directory / f"metadata_{split}.csv", index=False, encoding="utf-8", mode="x")


def load_split(directory: Path, split: str) -> SequenceSplit:
    if split not in SPLITS:
        raise ValueError("이번 시계열 준비 단계는 Train/Validation만 불러옵니다.")
    with np.load(directory / f"{split}.npz", allow_pickle=False) as archive:
        if set(archive.files) != ARRAY_NAMES:
            raise ValueError("시계열 NPZ의 배열 구성이 다릅니다.")
        arrays = {name: archive[name] for name in ARRAY_NAMES}
    metadata = pd.read_csv(directory / f"metadata_{split}.csv", dtype=str, keep_default_na=False)
    return SequenceSplit(**arrays, metadata=metadata)


def validate_partitions(train: SequenceSplit, validation: SequenceSplit) -> dict:
    overlaps = {}
    for name, columns in (("campaigns", ["campaign_id"]), ("runs", ["run_id"]),
                          ("sessions", GROUP_COLUMNS), ("rows", ["row_id"])):
        left = set(train.metadata[columns].itertuples(index=False, name=None))
        right = set(validation.metadata[columns].itertuples(index=False, name=None))
        overlaps[name] = len(left & right)
    if any(overlaps.values()):
        raise ValueError("시계열 Train/Validation에 같은 캠페인·run·세션·행이 섞여 있습니다.")
    return overlaps


def build_dataset(config: SequenceConfig, report_prefix: Path) -> dict:
    outputs = [report_prefix.with_suffix(suffix) for suffix in (".json", ".md")]
    if config.output_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 시계열 데이터셋·보고서를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
    if any(path.resolve().is_relative_to(config.source_dataset_dir.resolve()) for path in [config.output_dir, *outputs]):
        raise ValueError("고정 원본 메시지 데이터셋 내부에 시계열 출력을 저장하지 않습니다.")
    data = load_operational_data(config.source_dataset_dir, config.source_dataset_id)
    if len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows:
        raise ValueError("원본 Train/Validation 행 수가 시계열 계획과 다릅니다.")
    base_manifest = read_json(config.source_dataset_dir / "dataset_manifest.json")
    if base_manifest["config_snapshot"]["split_policy"] != "preassigned_campaign_holdout":
        raise ValueError("시계열 준비는 사전 캠페인 holdout 메시지 데이터셋을 사용합니다.")
    protected = {str(config.source_dataset_dir / name): digest for name, digest in data.input_sha256.items()}
    config.output_dir.mkdir(parents=True, exist_ok=False)
    lock_path = config.output_dir / "window_protocol_lock.json"
    write_json(lock_path, {"status": "locked_before_window_build", "created_at_utc": datetime.now(UTC).isoformat(),
                           "protocol": config.model_dump(mode="json"), "source_input_sha256": data.input_sha256})
    lock_hash = file_hash(lock_path)
    windows = {split: make_windows(getattr(data, split), config.window_size, config.dataset_id) for split in SPLITS}
    overlaps = validate_partitions(windows["train"], windows["validation"])
    summaries = {split: split_summary(value) for split, value in windows.items()}
    for split in SPLITS:
        save_split(config.output_dir, split, windows[split])
        restored = load_split(config.output_dir, split)
        validate_windows(restored, getattr(data, split), config.window_size, config.dataset_id)
    schema = {
        "schema_version": "v1", "sequence_contract_id": SEQUENCE_CONTRACT, "dataset_id": config.dataset_id,
        "base_dataset_id": data.dataset_id, "base_feature_contract_id": data.schema["feature_contract_id"],
        "feature_order": FEATURE_NAMES, "feature_units": data.schema["feature_units"], "window_size": config.window_size,
        "input_shape": ["batch", config.window_size, len(FEATURE_NAMES)], "input_dtype": "float64_raw_not_scaled",
        "stride": 1, "include_current_message": True, "history_direction": "past_and_current_only",
        "group_columns": GROUP_COLUMNS, "time_order": "request_index_ascending_contiguous_from_1",
        "padding": {"side": "right", "raw_value": 0.0, "mask_valid": True, "mask_padding": False,
                    "source_position_padding": -1, "minimum_valid_length": 1},
        "target_policy": "current_message_label", "anchor_alignment": "original_message_row_order",
        "preprocessing": {"status": "not_fitted", "future_fit_scope": "unique_original_train_rows_only_not_flattened_windows",
                          "future_transform_scope": "valid_steps_only_then_zero_pad", "tensor_dtype_after_transform": "float32"},
        "excluded_model_metadata": METADATA_COLUMNS + SEQUENCE_COLUMNS + ["label", "session_summary"],
        "test_windows_generated": False, "test_files_read": False,
    }
    write_json(config.output_dir / "sequence_schema.json", schema)
    for path, digest in protected.items():
        if file_hash(Path(path)) != digest:
            raise ValueError("시계열 구성 도중 원본 데이터가 변경되었습니다.")
    if file_hash(lock_path) != lock_hash:
        raise ValueError("시계열 구성 도중 창 설정 잠금이 변경되었습니다.")
    inventory = {path.name: {"sha256": file_hash(path), "bytes": path.stat().st_size}
                 for path in sorted(config.output_dir.iterdir()) if path.is_file()}
    report = {"report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
              "dataset_id": config.dataset_id, "source_dataset_id": data.dataset_id, "output_dir": str(config.output_dir.resolve()),
              "window_size": config.window_size, "feature_order": FEATURE_NAMES, "schema": schema,
              "splits": summaries, "identity_overlaps": overlaps, "source_input_sha256": data.input_sha256,
              "config_snapshot": config.model_dump(mode="json"), "output_files": inventory,
              "code_sha256": {"operational_sequences.py": file_hash(Path(__file__)),
                              "operational_data.py": file_hash(Path(__file__).parents[1] / "training" / "operational_data.py")},
              "preprocessing_fitted": False, "model_training_performed": False, "model_prediction_performed": False,
              "test_files_read": False, "test_windows_generated": False, "raw_jsonl_read": False,
              "limitations": [
                  "메시지 하나당 하나의 창을 만들지만 같은 세션의 겹치는 창은 독립 표본이 아니다.",
                  "mask와 lengths는 현재까지의 유효 관측 수다. 세션 종료 뒤 알게 되는 최종 길이를 입력에 넣지 않는다.",
                  "오른쪽 padding은 결측값을 0으로 대체한 것이 아니다. 이후 학습에서도 padding을 scaler fit이나 유효 timestep으로 사용하면 안 된다.",
                  "원본 Train 행으로만 scaler를 fit해야 한다. 창을 펼치면 이전 메시지가 반복되고 padding도 포함돼 통계가 달라진다.",
                  "본문 정보는 여전히 입력에 없다. 길이 1 세션이나 최초 메시지에는 존재하지 않는 과거를 만들어 넣지 않는다.",
                  "Train/Validation만 구성했다. Test는 최종 비교 단계에서 같은 고정 규칙으로 별도 처리한다.",
              ]}
    # 저장 후 재로딩·원본 대조가 통과한 뒤에만 데이터셋 완료를 표시한다.
    write_json(config.output_dir / "sequence_manifest.json", report)
    report_prefix.parent.mkdir(parents=True, exist_ok=True)
    with outputs[1].open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    write_json(outputs[0], report)
    return report


def render_report(report: dict) -> str:
    lines = ["# 운영 메시지 시계열 창 구성", "", f"창 크기: {report['window_size']}, 입력: 7개 원본 수치, 오른쪽 padding.",
             "현재 메시지까지의 최근 기록만 사용하고, 원본 메시지 하나당 하나의 창과 같은 정답을 유지한다.", "",
             "| 분할 | 창 shape | 세션 | 완전한 창 | 짧은 창 | 길이 1 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for split, values in report["splits"].items():
        lines.append(f"| {split} | {values['shape']} | {values['sessions']} | {values['full_windows']} | {values['partial_windows']} | {values['length_one_windows']} |")
    lines += ["", "## 정답·관측 coverage", "", "| 분할 | 정상 | 통제된 이상 |", "| --- | ---: | ---: |"]
    for split, values in report["splits"].items():
        lines.append(f"| {split} | {values['label_distribution']['0']} | {values['label_distribution']['1']} |")
    lines += ["", "부분 창과 길이 1 메시지를 버리지 않아 모든 원본 메시지를 유지한다. source_positions와 원본 row_id 목록으로 모든 유효 timestep의 출처를 추적한다.",
              "", "## 검증", "", f"분할 간 식별자 중복: `{report['identity_overlaps']}`",
              "누락·중복 메시지 번호, 누적값 감소, 다른 세션·캠페인·split, 미래 메시지, 잘못된 mask·padding·정답·행 정렬은 거부한다.",
              "NPZ를 pickle 없이 재로딩하고 유효 값·원본 위치·현재 정답을 다시 대조했다. 원본 입력과 출력 해시를 보관한다.",
              "", "## 수행하지 않은 작업", "", "scaler fit·LSTM 학습·모델 예측·Test CSV 접근은 하지 않았다. 기존 모델·API·입력 의미 계약을 변경하지 않았다.",
              "", "## 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 다음 작업", "", "원본 Train 1,538건으로만 scaler를 fit하고 유효 timestep만 변환한다. 유효 lengths와 오른쪽 mask를 이용해 LSTM을 직접 학습하고 같은 Validation 메시지와 선정 RF를 비교한다.", ""]
    return "\n".join(lines)
