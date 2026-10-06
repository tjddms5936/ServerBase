from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS_DIR = AI_ROOT / ".deps"
if LOCAL_DEPS_DIR.exists():
    sys.path.insert(0, str(LOCAL_DEPS_DIR))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import sklearn
from threadpoolctl import threadpool_limits

from evaluation.analyze_logistic_errors import (
    ATTACK_CATEGORY_FILE,
    RAW_TEST_FILE,
    analyze_attack_categories,
    format_percent,
    load_inputs,
    markdown_table,
    validate_inputs,
)
from scripts.preprocess_unsw_nb15 import normalize_categorical_missing_values
from training.train_logistic_regression import (
    calculate_metrics,
    relative_path,
    require_files,
    save_json,
)
from training.train_random_forest import (
    DEFAULT_THRESHOLD,
    FEATURE_SCHEMA_FILE,
    METRICS_FILE as RF_METRICS_FILE,
    MODEL_FILE as RF_MODEL_FILE,
    PREPROCESSOR_FILE,
    file_sha256,
)


LR_MODEL_FILE = AI_ROOT / "artifacts" / "baseline_logistic_regression.joblib"
LR_METRICS_FILE = AI_ROOT / "reports" / "baseline_metrics.json"
JSON_REPORT_FILE = AI_ROOT / "reports" / "baseline_comparison.json"
MARKDOWN_REPORT_FILE = AI_ROOT / "reports" / "model_comparison.md"
BATCH_REPEATS = 5
SINGLE_REQUEST_REPEATS = 200
WARMUP_REPEATS = 10
RANDOM_STATE = 42


def check_training_provenance(rf_report: dict[str, Any]) -> None:
    for relative, expected_hash in rf_report["data"]["input_sha256"].items():
        if file_sha256(AI_ROOT / relative) != expected_hash:
            raise ValueError(f"RandomForest 학습 이후 입력 파일이 변경되었습니다: {relative}")
    if file_sha256(RF_MODEL_FILE) != rf_report["artifacts"]["model_sha256"]:
        raise ValueError("RandomForest 모델과 학습 보고서가 일치하지 않습니다.")


def validate_raw_feature_alignment(x_test: Any, raw_test: Any) -> None:
    preprocessor = joblib.load(PREPROCESSOR_FILE)
    schema = json.loads(FEATURE_SCHEMA_FILE.read_text(encoding="utf-8"))
    if list(x_test.columns) != list(preprocessor.get_feature_names_out()):
        raise ValueError("전처리기와 X_test feature 순서가 다릅니다.")

    # label만 같은 순열도 잡도록 원본 feature를 재변환해 모든 row를 비교합니다.
    for start in range(0, len(x_test), 2048):
        raw_features = raw_test.iloc[start:start + 2048][schema["raw_input_features"]].copy()
        normalize_categorical_missing_values(raw_features, schema["categorical_features"])
        transformed = preprocessor.transform(raw_features).astype(np.float32)
        saved = x_test.iloc[start:start + 2048].to_numpy()
        if not np.allclose(transformed, saved, rtol=1e-5, atol=1e-6):
            raise ValueError("원본 feature와 X_test의 row 순서 또는 전처리 값이 다릅니다.")


def attack_probabilities(model: Any, features: Any) -> np.ndarray:
    if not np.array_equal(model.classes_, [0, 1]):
        raise ValueError("비교 모델은 normal=0, attack=1 이진 분류 모델이어야 합니다.")
    return model.predict_proba(features)[:, 1]


def measure_latency(model: Any, x_test: Any, row_indices: np.ndarray) -> dict[str, Any]:
    single_rows = [x_test.iloc[index:index + 1] for index in row_indices]
    # 데이터 선택과 파일 로드는 측정에서 제외하고 같은 predict_proba 호출만 비교합니다.
    for row in single_rows[:WARMUP_REPEATS]:
        model.predict_proba(row)
    single_ms = []
    for row in single_rows:
        started = time.perf_counter_ns()
        model.predict_proba(row)
        single_ms.append((time.perf_counter_ns() - started) / 1_000_000)

    model.predict_proba(x_test)
    batch_ms = []
    for _ in range(BATCH_REPEATS):
        started = time.perf_counter_ns()
        model.predict_proba(x_test)
        batch_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "single_request_median_ms": float(np.median(single_ms)),
        "single_request_p95_ms": float(np.percentile(single_ms, 95)),
        "single_request_samples_ms": single_ms,
        "batch_median_ms": float(np.median(batch_ms)),
        "batch_samples_ms": batch_ms,
        "batch_amortized_per_row_ms": float(np.median(batch_ms) / len(x_test)),
    }


def model_array_storage_bytes(model: Any) -> int:
    if hasattr(model, "estimators_"):
        # 트리의 노드와 예측 값 배열을 합산하며, 객체 관리 비용은 포함하지 않습니다.
        size = sum(
            estimator.tree_.__getstate__()["nodes"].nbytes
            + estimator.tree_.__getstate__()["values"].nbytes
            for estimator in model.estimators_
        )
        return int(size + model.classes_.nbytes)
    return int(model.coef_.nbytes + model.intercept_.nbytes + model.classes_.nbytes)


def compare_attack_categories(lr_rows: list[dict], rf_rows: list[dict]) -> list[dict]:
    lr_by_name = {row["attack_category"]: row for row in lr_rows}
    rf_by_name = {row["attack_category"]: row for row in rf_rows}
    if set(lr_by_name) != set(rf_by_name):
        raise ValueError("두 모델의 공격 유형 목록이 다릅니다.")
    rows = []
    for category in sorted(lr_by_name):
        lr = lr_by_name[category]
        rf = rf_by_name[category]
        if lr["total"] != rf["total"]:
            raise ValueError("공격 유형별 비교 표본 수가 다릅니다.")
        rows.append({
            "attack_category": category, "total": lr["total"],
            "logistic_recall": lr["recall"], "random_forest_recall": rf["recall"],
            "recall_delta_percentage_points": (rf["recall"] - lr["recall"]) * 100,
            "logistic_false_negative": lr["false_negative"],
            "random_forest_false_negative": rf["false_negative"],
        })
    return rows


def compare_error_changes(y_test: Any, lr_predictions: np.ndarray, rf_predictions: np.ndarray) -> dict:
    actual = np.asarray(y_test)
    normal = actual == 0
    attack = actual == 1
    return {
        "false_positives_fixed": int((normal & (lr_predictions == 1) & (rf_predictions == 0)).sum()),
        "new_false_positives": int((normal & (lr_predictions == 0) & (rf_predictions == 1)).sum()),
        "false_negatives_fixed": int((attack & (lr_predictions == 0) & (rf_predictions == 1)).sum()),
        "new_false_negatives": int((attack & (lr_predictions == 1) & (rf_predictions == 0)).sum()),
    }


def generate_markdown(result: dict[str, Any]) -> str:
    lr = result["models"]["logistic_regression"]
    rf = result["models"]["random_forest"]
    delta = result["random_forest_minus_logistic"]
    changes = result["paired_error_changes"]
    regressed_categories = [
        row for row in result["attack_category_comparison"]
        if row["recall_delta_percentage_points"] < 0
    ]
    regression_notes = []
    for row in regressed_categories:
        rf_fn = rf["metrics"]["confusion_matrix_details"]["false_negative"]
        share = row["random_forest_false_negative"] / rf_fn if rf_fn else 0.0
        regression_notes.extend([
            f"`{row['attack_category']}` Recall은 {format_percent(row['logistic_recall'])}에서 "
            f"{format_percent(row['random_forest_recall'])}로 낮아졌고 FN은 "
            f"{row['logistic_false_negative']:,}건에서 {row['random_forest_false_negative']:,}건으로 늘었다. "
            f"이 공격 유형은 RF 전체 FN의 {format_percent(share)}를 차지한다.",
            "",
        ])
    if not regression_notes:
        regression_notes = ["이번 고정 설정에서 Recall이 낮아진 공격 유형은 없다.", ""]
    metric_rows = []
    for label, key in [
        ("Accuracy", "accuracy"), ("Precision", "precision"), ("Recall", "recall"),
        ("F1", "f1"), ("ROC-AUC", "roc_auc"), ("FPR", "false_positive_rate"),
    ]:
        metric_rows.append([
            label, format_percent(lr["metrics"][key]), format_percent(rf["metrics"][key]),
            f"{delta[key + '_percentage_points']:+.2f} %p",
        ])
    for label, key in [("FP", "false_positive"), ("FN", "false_negative")]:
        metric_rows.append([
            label, f"{lr['metrics']['confusion_matrix_details'][key]:,}",
            f"{rf['metrics']['confusion_matrix_details'][key]:,}", f"{delta[key]:+,}",
        ])
    performance_rows = []
    for label, key in [
        ("단일 요청 중앙값(ms)", "single_request_median_ms"),
        ("단일 요청 p95(ms)", "single_request_p95_ms"),
        ("전체 배치 중앙값(ms)", "batch_median_ms"),
        ("배치 내 row당 평균(ms)", "batch_amortized_per_row_ms"),
    ]:
        performance_rows.append([label, f"{lr['latency'][key]:.6f}", f"{rf['latency'][key]:.6f}"])
    performance_rows.extend([
        ["압축 모델 파일 크기(bytes)", f"{lr['model_file_bytes']:,}", f"{rf['model_file_bytes']:,}"],
        ["주요 학습 배열 크기(bytes)", f"{lr['model_array_storage_bytes']:,}", f"{rf['model_array_storage_bytes']:,}"],
        ["학습 시간 기록(초, 참고용)", f"{lr['historical_training_seconds']:.3f}", f"{rf['historical_training_seconds']:.3f}"],
    ])
    lines = [
        "# Logistic Regression과 RandomForest baseline 비교", "",
        "## 실험 목적", "",
        "비선형 모델이 정상 트래픽 오탐을 줄이면서 공격 탐지율을 유지할 수 있는지 검증한다. "
        "성능 변화와 함께 실시간 추론 비용을 비교한다.", "",
        "## 고정 실험 조건", "",
        f"- 공식 train {result['data']['train_rows']:,}건 / test {result['data']['test_rows']:,}건, feature {result['data']['feature_count']}개",
        "- 두 모델에 동일한 train 기반 전처리와 float32 test 입력 적용",
        "- 공격 확률 >= 0.5이면 공격 판정; 정확히 0.5인 경우도 공격으로 처리",
        "- RandomForest: 트리 100개, max_features=sqrt, max_depth=None, class_weight=None, seed=42",
        "- test 결과를 이용한 설정 또는 threshold 변경 없이 첫 고정 설정 평가",
        "- 원본 test를 재변환해 모든 row의 feature, label, 공격 유형 정렬 검사", "",
        "## 전체 성능", "",
        markdown_table(["지표", "Logistic Regression", "RandomForest", "RF - LR"], metric_rows), "",
        "%p는 퍼센트 포인트 차이이다. FPR은 정상 데이터 중 공격으로 오판한 비율이다.", "",
        "## 같은 row에서 바뀐 오분류", "",
        f"- 기존 FP를 정상으로 수정: {changes['false_positives_fixed']:,}건",
        f"- 기존 TN을 새로 공격으로 오판: {changes['new_false_positives']:,}건",
        f"- 기존 FN을 공격으로 탐지: {changes['false_negatives_fixed']:,}건",
        f"- 기존 TP를 새로 정상으로 오판: {changes['new_false_negatives']:,}건", "",
        "## 공격 유형별 탐지율", "",
        markdown_table(
            ["공격 유형", "표본", "LR Recall", "RF Recall", "차이(%p)", "LR FN", "RF FN"],
            [[row['attack_category'], f"{row['total']:,}", format_percent(row['logistic_recall']),
              format_percent(row['random_forest_recall']), f"{row['recall_delta_percentage_points']:+.2f}",
              f"{row['logistic_false_negative']:,}", f"{row['random_forest_false_negative']:,}"]
             for row in result['attack_category_comparison']],
        ), "",
        "공격 유형 label은 학습 입력이 아닌 사후 분석용이다. Worms 등 작은 표본의 결과는 변동성이 크다.", "",
        "## 지연시간과 저장 비용", "",
        markdown_table(["항목", "Logistic Regression", "RandomForest"], performance_rows), "",
        f"두 모델 모두 predict_proba만 측정했다. CPU 스레드 1개, 워밍업 {WARMUP_REPEATS}회 후 "
        f"같은 test row {SINGLE_REQUEST_REPEATS}개를 요청 단위로 측정했다. 전체 test 배치는 "
        f"워밍업 후 {BATCH_REPEATS}회 측정한 중앙값이다. 데이터 선택과 로딩은 타이머 밖에서 수행했다.", "",
        "배치 총 시간을 row 수로 나눈 값은 배치 처리량 지표이며 단일 요청 지연시간이 아니다. "
        "JSON 변환, 전처리, HTTP 왕복, 큐 대기 시간은 별도 end-to-end 측정이 필요하다.", "",
        "주요 학습 배열 크기는 LR 계수/절편/클래스 및 RF 트리 노드/값/클래스 배열의 합이다. "
        "Python 객체, 라이브러리, 데이터, 임시 버퍼를 제외한 부분 추정치로, 프로세스 총 메모리나 peak RSS가 아니다. "
        "압축 모델 파일 크기는 디스크 저장 비용이다.", "",
        "학습 시간은 서로 다른 실행 시점과 병렬 설정에서 얻은 참고 기록이다. "
        "속도 비교 결론은 이번에 동일 조건으로 재측정한 추론 시간을 기준으로 한다.", "",
        "## 결과 해석", "",
        f"RandomForest의 FP 변화는 {delta['false_positive']:+,}건, FN 변화는 {delta['false_negative']:+,}건이며, "
        f"F1은 {delta['f1_percentage_points']:+.2f}%p, Recall은 {delta['recall_percentage_points']:+.2f}%p 변했다.", "",
        *regression_notes,
        f"RF 정상 트래픽 FPR은 여전히 {format_percent(rf['metrics']['false_positive_rate'])}다. "
        f"단일 요청 중앙값은 LR 대비 {rf['latency']['single_request_median_ms'] / lr['latency']['single_request_median_ms']:.2f}배이며 "
        "모델 저장 및 메모리 비용도 늘었다. 전체 지표 향상과 공격 유형별 취약점, 운영 비용을 함께 평가해야 한다.", "",
        "## 해석 범위와 다음 실험", "",
        "1. 현재 결과는 단일 seed와 고정 설정의 baseline 비교다. test 오분류는 이미 탐색한 자료이므로 후속 모델 선택 근거의 한계를 기록한다.",
        "2. 다음 설정 선택은 공식 train 내부에 validation split을 만들고, 전처리기도 해당 학습 부분에서만 다시 fit한 뒤 수행한다.",
        "3. 두 모델을 validation에서 동일 FPR 또는 동일 Recall로 맞춰 비교해야 threshold 효과와 모델 표현력 효과를 구분하기 쉽다.",
        "4. 여러 seed 또는 교차검증을 통해 변동성을 확인하고, 가능하면 아직 탐색하지 않은 별도 데이터로 최종 검증한다.",
        "5. UNSW-NB15 feature와 현재 C++ 수집 feature의 의미와 가용성을 맞춘 후 운영 inference 모델을 연결한다.", "",
    ]
    return "\n".join(lines)


def main() -> int:
    require_files([
        LR_MODEL_FILE, RF_MODEL_FILE, LR_METRICS_FILE, RF_METRICS_FILE,
        PREPROCESSOR_FILE, FEATURE_SCHEMA_FILE, RAW_TEST_FILE, ATTACK_CATEGORY_FILE,
    ])
    lr_report = json.loads(LR_METRICS_FILE.read_text(encoding="utf-8"))
    rf_report = json.loads(RF_METRICS_FILE.read_text(encoding="utf-8"))
    check_training_provenance(rf_report)
    print("[Comparison] 모델, 원본 test, 전처리 결과의 정합성을 검사합니다.", flush=True)
    lr_model, x_test, y_test, categories, raw_test = load_inputs()
    rf_model = joblib.load(RF_MODEL_FILE)
    for model in [lr_model, rf_model]:
        validate_inputs(model, x_test, y_test, categories, raw_test)
    validate_raw_feature_alignment(x_test, raw_test)
    row_indices = np.random.default_rng(RANDOM_STATE).choice(
        len(x_test), size=SINGLE_REQUEST_REPEATS, replace=len(x_test) < SINGLE_REQUEST_REPEATS,
    )

    models = {}
    predictions = {}
    category_results = {}
    # RF 작업 스레드와 BLAS/OpenMP 스레드를 모두 1개로 고정합니다.
    rf_model.set_params(n_jobs=1)
    with threadpool_limits(limits=1):
        for name, model, path, report in [
            ("logistic_regression", lr_model, LR_MODEL_FILE, lr_report),
            ("random_forest", rf_model, RF_MODEL_FILE, rf_report),
        ]:
            print(f"[Comparison] {name} 성능과 지연시간을 측정합니다.", flush=True)
            probabilities = attack_probabilities(model, x_test)
            predicted = (probabilities >= DEFAULT_THRESHOLD).astype(np.int8)
            metrics = calculate_metrics(y_test, predicted, probabilities)
            if metrics["confusion_matrix"] != report["metrics"]["confusion_matrix"]:
                raise ValueError(f"모델 재예측과 저장된 학습 지표가 다릅니다: {name}")
            details = metrics["confusion_matrix_details"]
            metrics["false_positive_rate"] = details["false_positive"] / int((y_test == 0).sum())
            metrics["false_negative_rate"] = details["false_negative"] / int((y_test == 1).sum())
            models[name] = {
                "metrics": metrics, "latency": measure_latency(model, x_test, row_indices),
                "model_path": relative_path(path), "model_sha256": file_sha256(path),
                "model_file_bytes": path.stat().st_size,
                "model_array_storage_bytes": model_array_storage_bytes(model),
                "historical_training_seconds": report["timing_seconds"]["training"],
            }
            predictions[name] = predicted
            category_results[name] = analyze_attack_categories(y_test, categories, predicted, probabilities)

    lr_metrics = models["logistic_regression"]["metrics"]
    rf_metrics = models["random_forest"]["metrics"]
    delta = {
        key + "_percentage_points": (rf_metrics[key] - lr_metrics[key]) * 100
        for key in ["accuracy", "precision", "recall", "f1", "roc_auc", "false_positive_rate", "false_negative_rate"]
    }
    delta.update({
        key: rf_metrics["confusion_matrix_details"][key] - lr_metrics["confusion_matrix_details"][key]
        for key in ["false_positive", "false_negative"]
    })
    result = {
        "schema_version": 1, "generated_at_utc": datetime.now(UTC).isoformat(),
        "data": {
            "dataset": "UNSW-NB15", "train_rows": rf_report["data"]["train_rows"],
            "test_rows": len(x_test), "feature_count": x_test.shape[1],
            "raw_feature_alignment_verified": True,
            "rf_input_sha256": rf_report["data"]["input_sha256"],
            "logistic_provenance_note": "과거 LR 보고서에는 학습 입력 hash가 없어 당시 입력 동일성은 완전히 증명할 수 없습니다. 현재 모델 재예측과 저장된 confusion matrix를 대조했습니다.",
        },
        "protocol": {
            "threshold": DEFAULT_THRESHOLD, "positive_rule": "attack_probability >= threshold",
            "inference_threads": 1, "inference_method": "predict_proba",
            "warmup_repeats": WARMUP_REPEATS, "single_request_repeats": SINGLE_REQUEST_REPEATS,
            "batch_repeats": BATCH_REPEATS, "row_sampling_seed": RANDOM_STATE,
            "excluded_latency_costs": ["file_loading", "preprocessing", "row_selection", "HTTP", "queue_wait", "JSON"],
            "memory_note": "주요 학습 배열 크기 부분 추정치. Python 객체 및 프로세스 전체 메모리는 제외합니다.",
            "training_time_note": "서로 다른 시점과 병렬 조건의 참고 기록이며 동등 조건의 학습 속도 비교가 아닙니다.",
        },
        "environment": {
            "python": platform.python_version(), "scikit_learn": sklearn.__version__,
            "numpy": np.__version__, "logical_cpu_count": os.cpu_count(), "platform": platform.platform(),
        },
        "models": models, "random_forest_minus_logistic": delta,
        "attack_category_comparison": compare_attack_categories(
            category_results["logistic_regression"], category_results["random_forest"],
        ),
        "paired_error_changes": compare_error_changes(
            y_test, predictions["logistic_regression"], predictions["random_forest"],
        ),
    }
    save_json(JSON_REPORT_FILE, result)
    MARKDOWN_REPORT_FILE.write_text(generate_markdown(result), encoding="utf-8")
    print(f"[Comparison] 비교 완료: FP {delta['false_positive']:+,}건, FN {delta['false_negative']:+,}건")
    print(f"  - JSON: {JSON_REPORT_FILE}")
    print(f"  - 보고서: {MARKDOWN_REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
