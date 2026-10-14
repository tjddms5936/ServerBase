from __future__ import annotations

import numpy as np
import pandas as pd

from data_preparation.operational_dataset import FEATURE_NAMES
from training.operational_data import OperationalSplit


GROUP_COLUMNS = ["campaign_id", "run_id", "session_id"]
TRANSFORM_COLUMNS = {
    "raw7_v1": FEATURE_NAMES,
    "without_io5_v1": [name for name in FEATURE_NAMES if name not in {"bytes_received_total", "bytes_sent_total"}],
    "prefix_mean5_v1": ["packet_size", "payload_size", "connection_duration_ms",
                        "prefix_mean_interval_ms", "error_count_total"],
    "prefix_mean_parsed6_v1": ["packet_size", "payload_size", "connection_duration_ms",
                              "prefix_mean_interval_ms", "parsed_bytes_prefix_total", "error_count_total"],
}


def prefix_observations(source: OperationalSplit) -> pd.DataFrame:
    """같은 연결의 과거·현재로만 진단 값을 만들고 원본 행 순서를 유지한다."""
    if (list(source.X.columns) != FEATURE_NAMES or len(source.X) != len(source.metadata) or
            len(source.y) != len(source.X) or not source.X.index.equals(pd.RangeIndex(len(source.X))) or
            not source.metadata.index.equals(source.X.index) or source.X.empty):
        raise ValueError("입력 순서·행 수·행 인덱스가 올바르지 않습니다.")
    metadata = source.metadata
    if (not set(GROUP_COLUMNS + ["request_index", "row_id", "split"]).issubset(metadata.columns) or
            metadata.row_id.duplicated().any() or metadata[GROUP_COLUMNS].isna().any().any() or
            len(set(metadata["split"])) != 1 or set(metadata["split"]) - {"train", "validation"}):
        raise ValueError("prefix 진단은 식별자가 있는 단일 Train 또는 Validation만 허용합니다.")
    if not np.isfinite(source.X.to_numpy()).all() or (source.X < 0).any().any():
        raise ValueError("prefix 입력은 유한한 비음수여야 합니다.")
    result = pd.DataFrame(index=source.X.index, columns=[
        "prefix_mean_interval_ms", "parsed_bytes_prefix_total", "recv_ahead_of_parsed_bytes",
        "recv_counter_same_as_previous", "is_first_message"], dtype="float64")
    for _, group in metadata.groupby(GROUP_COLUMNS, sort=False):
        numeric = pd.to_numeric(group.request_index, errors="raise").to_numpy(dtype="float64")
        if not np.isfinite(numeric).all() or (numeric != np.floor(numeric)).any():
            raise ValueError("request_index는 유한한 정수여야 합니다.")
        # 분석 시 정렬은 허용하되 누락·중복을 평균 값으로 덮지 않는다.
        positions = group.index.to_numpy()[np.argsort(numeric, kind="stable")]
        indices = numeric[np.argsort(numeric, kind="stable")]
        if not np.array_equal(indices, np.arange(1, len(group) + 1)):
            raise ValueError("세션 prefix는 1부터 연속된 메시지 기록이 필요합니다.")
        rows = source.X.loc[positions]
        if (rows.message_interval_ms.iloc[0] != 0 or
                np.diff(rows.connection_duration_ms.to_numpy()).min(initial=0) < 0 or
                any(np.diff(rows[name].to_numpy()).min(initial=0) < 0 for name in
                    ("bytes_received_total", "bytes_sent_total", "error_count_total"))):
            raise ValueError("세션 시간·counter가 역행하거나 첫 간격이 0이 아닙니다.")
        packets = rows.packet_size.to_numpy()
        if (packets != np.floor(packets)).any() or not np.array_equal(packets, rows.payload_size.to_numpy() + 8):
            raise ValueError("프레임 크기와 payload 크기의 관계가 다릅니다.")
        parsed = np.cumsum(packets)
        received = rows.bytes_received_total.to_numpy()
        if (received < parsed).any():
            raise ValueError("관측한 수신 바이트보다 파싱 프레임 합이 큽니다.")
        # 첫 간격 0은 분모에서 제외한다. 뒤쪽 메시지나 종료 통계는 참조하지 않는다.
        means = np.divide(np.cumsum(rows.message_interval_ms.to_numpy()), indices - 1,
                          out=np.zeros(len(rows)), where=indices > 1)
        result.loc[positions] = np.column_stack((means, parsed, received - parsed,
                                                 np.r_[False, received[1:] == received[:-1]], indices == 1))
    result["recv_counter_same_as_previous"] = result.recv_counter_same_as_previous.astype(bool)
    result["is_first_message"] = result.is_first_message.astype(bool)
    return result


def transform_prefix_features(source: OperationalSplit, transform_id: str,
                              observations: pd.DataFrame | None = None) -> pd.DataFrame:
    if transform_id not in TRANSFORM_COLUMNS:
        raise ValueError("사전에 지정하지 않은 입력 변환입니다.")
    prefix = prefix_observations(source) if observations is None else observations
    if not prefix.index.equals(source.X.index):
        raise ValueError("prefix와 원본 입력의 행 순서가 다릅니다.")
    # 원본 v2 필드의 뜻을 바꾸지 않고 파생 값은 별도 열 이름과 변환 버전을 쓴다.
    combined = pd.concat([source.X, prefix], axis=1)
    return combined.loc[:, TRANSFORM_COLUMNS[transform_id]].copy()
