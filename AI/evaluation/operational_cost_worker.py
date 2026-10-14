from __future__ import annotations

import argparse
import gc
import os
import platform
import sys
import time
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
for name in (".deps", ".deps_lstm"):
    if (AI_ROOT / name).exists():
        sys.path.insert(0, str(AI_ROOT / name))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
import psutil
import sklearn
from threadpoolctl import threadpool_info, threadpool_limits

from data_collection.quality import read_json
from data_preparation.operational_dataset import file_hash
from evaluation.operational_final_models import FrozenRuntime
from training.train_operational_logistic_regression import write_json


def memory_snapshot() -> dict:
    process = psutil.Process()
    memory = process.memory_info()
    try:
        unique = getattr(process.memory_full_info(), "uss", None)
    except psutil.Error:
        unique = None
    return {"rss_bytes": int(memory.rss), "native_peak_working_set_bytes": getattr(memory, "peak_wset", None),
            "uss_bytes": int(unique) if unique is not None else None,
            "private_commit_bytes": getattr(memory, "private", None)}


def latency_summary(samples: list[float]) -> dict:
    values = np.asarray(samples, dtype="float64")
    if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("지연시간 측정 표본이 유효하지 않습니다.")
    return {"samples_ms": samples, "median_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95)),
            "mean_ms": float(np.mean(values)), "minimum_ms": float(values.min()), "maximum_ms": float(values.max())}


def probability_agreement(actual: np.ndarray, expected: np.ndarray, threshold: float, tolerance: float) -> bool:
    # float32 배치 연산의 작은 수치 차이만 허용한다. threshold를 넘나드는 판단 차이는 허용하지 않는다.
    return bool(np.allclose(actual, expected, rtol=0, atol=tolerance) and
                np.array_equal(actual >= threshold, expected >= threshold))


def measure(job_path: Path) -> dict:
    job = read_json(job_path)
    if file_hash(Path(job["input_path"])) != job["input_sha256"] or job["input_split"] != "validation":
        raise ValueError("비용 worker는 잠근 Validation 입력만 사용합니다.")
    descriptor, settings = job["descriptor"], job["benchmark"]
    with np.load(job["input_path"], allow_pickle=False) as archive:
        expected = {"X", "lengths"} if descriptor["kind"] == "lstm" else {"X"}
        if set(archive.files) != expected:
            raise ValueError("비용 입력 배열 구성이 다릅니다.")
        raw = archive["X"]
        lengths = archive["lengths"] if "lengths" in archive.files else None
    indices = np.asarray(job["sample_indices"], dtype="int64")
    if len(indices) != settings["single_request_repeats"] or ((indices < 0) | (indices >= len(raw))).any():
        raise ValueError("비용 측정의 요청 행 목록이 다릅니다.")
    gc.collect()
    before = memory_snapshot()
    runtime = FrozenRuntime(descriptor)
    gc.collect()
    after_load = memory_snapshot()
    with threadpool_limits(limits=settings["cpu_threads"]):
        prepared = runtime.prepare(raw, lengths)
        raw_rows = [(raw[index:index + 1], lengths[index:index + 1] if lengths is not None else None) for index in indices]
        if descriptor["kind"] == "lstm":
            ready_rows = [(prepared[0][index:index + 1], prepared[1][index:index + 1]) for index in indices]
        elif isinstance(prepared, pd.DataFrame):
            ready_rows = [prepared.iloc[index:index + 1] for index in indices]
        else:
            ready_rows = [prepared[index:index + 1] for index in indices]
        reference = runtime.predict_raw(raw, lengths)
        tolerance = settings["probability_absolute_tolerance"]
        if not probability_agreement(reference, runtime.predict_ready(prepared), runtime.threshold, tolerance):
            raise ValueError("전처리 포함 경로와 모델 단독 경로의 확률이 다릅니다.")
        for index in range(settings["warmup_repeats"]):
            position = index % len(raw_rows)
            runtime.predict_raw(*raw_rows[position]) >= runtime.threshold
            runtime.predict_ready(ready_rows[position]) >= runtime.threshold
        samples = {"features_to_decision": [], "model_only": []}
        single_equal = True
        maximum_probability_difference = 0.0
        for position, index in enumerate(indices):
            started = time.perf_counter_ns()
            probability = runtime.predict_raw(*raw_rows[position])
            predicted = probability >= runtime.threshold
            samples["features_to_decision"].append((time.perf_counter_ns() - started) / 1_000_000)
            started = time.perf_counter_ns()
            ready_probability = runtime.predict_ready(ready_rows[position])
            ready_predicted = ready_probability >= runtime.threshold
            samples["model_only"].append((time.perf_counter_ns() - started) / 1_000_000)
            maximum_probability_difference = max(maximum_probability_difference, float(np.max(np.abs(probability - reference[index:index + 1]))))
            if (not probability_agreement(probability, reference[index:index + 1], runtime.threshold, tolerance) or
                    not np.array_equal(predicted, ready_predicted)):
                single_equal = False
        if not single_equal:
            raise ValueError("단일 요청과 배치 경로의 확률 또는 threshold 판단이 다릅니다.")
        runtime.predict_raw(raw, lengths)
        batch = []
        for _ in range(settings["batch_repeats"]):
            started = time.perf_counter_ns()
            runtime.predict_raw(raw, lengths) >= runtime.threshold
            batch.append((time.perf_counter_ns() - started) / 1_000_000)
        pools = threadpool_info()
    peak = memory_snapshot()
    torch_loaded = "torch" in sys.modules
    return {"status": "complete", "model_id": descriptor["id"], "kind": descriptor["kind"], "pid": os.getpid(),
            "input_split": "validation", "input_rows": len(raw), "input_sha256": job["input_sha256"],
            "sample_indices": job["sample_indices"], "descriptor": descriptor,
            "single_request": {name: latency_summary(values) for name, values in samples.items()},
            "batch": {**latency_summary(batch), "amortized_per_message_median_ms": float(np.median(batch) / len(raw))},
            "memory": {"before_model_load": before, "after_model_load": after_load, "after_benchmark": peak,
                       "loaded_rss_delta_bytes": after_load["rss_bytes"] - before["rss_bytes"],
                       "native_peak_scope": "process_start_through_benchmark_snapshot_including_imports_inputs_caches",
                       "model_and_scaler_array_bytes": runtime.array_storage_bytes()},
            "required_model_files_bytes": sum(value["bytes"] for value in descriptor["files"].values()),
            "single_batch_decisions_equal": True, "torch_imported": torch_loaded,
            "maximum_single_batch_probability_absolute_difference": maximum_probability_difference,
            "probability_absolute_tolerance": tolerance,
            "environment": {"python": platform.python_version(), "platform": platform.platform(), "numpy": np.__version__,
                            "scikit_learn": sklearn.__version__, "psutil": psutil.__version__, "logical_cpu_count": os.cpu_count(),
                            "cpu_threads": settings["cpu_threads"], "threadpools": pools,
                            "torch": str(sys.modules["torch"].__version__) if torch_loaded else None},
            "excluded_latency_costs": ["file_loading", "row_selection", "history_buffer_management", "feature_validation", "JSON", "HTTP", "queue_wait"],
            "test_files_read": False, "model_training_performed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="모델 한 개만 로드하는 독립 비용 측정 worker입니다.")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        write_json(args.output, measure(args.job))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"[비용 worker] 실패: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
