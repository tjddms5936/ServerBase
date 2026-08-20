from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS_DIR = AI_ROOT / ".deps"

if LOCAL_DEPS_DIR.exists():
    sys.path.insert(0, str(LOCAL_DEPS_DIR))

import joblib
import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


PROCESSED_DIR = AI_ROOT / "data" / "processed" / "unsw_nb15"
ARTIFACTS_DIR = AI_ROOT / "artifacts"
REPORTS_DIR = AI_ROOT / "reports"

X_TRAIN_FILE = PROCESSED_DIR / "X_train.csv"
Y_TRAIN_FILE = PROCESSED_DIR / "y_train.csv"
X_TEST_FILE = PROCESSED_DIR / "X_test.csv"
Y_TEST_FILE = PROCESSED_DIR / "y_test.csv"

MODEL_FILE = ARTIFACTS_DIR / "baseline_logistic_regression.joblib"
METRICS_FILE = REPORTS_DIR / "baseline_metrics.json"

MAX_ITERATIONS = 1000
REGULARIZATION_STRENGTH = 1.0
RANDOM_STATE = 42


def require_files(paths: list[Path]) -> None:
    missing_files = [path for path in paths if not path.exists()]
    if missing_files:
        formatted_paths = "\n".join(f"  - {path}" for path in missing_files)
        raise FileNotFoundError(
            "전처리된 학습 파일을 찾을 수 없습니다. 먼저 전처리 스크립트를 실행하세요.\n"
            f"{formatted_paths}"
        )


def load_feature_data(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=np.float32)


def load_label_data(path: Path) -> pd.Series:
    data = pd.read_csv(path)
    if "label" not in data.columns:
        raise ValueError(f"label column이 없습니다: {path}")
    return data["label"].astype(np.int8)


def validate_data(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
) -> None:
    if len(x_train) != len(y_train):
        raise ValueError(
            f"train feature와 label의 row 수가 다릅니다: {len(x_train)} != {len(y_train)}"
        )
    if len(x_test) != len(y_test):
        raise ValueError(
            f"test feature와 label의 row 수가 다릅니다: {len(x_test)} != {len(y_test)}"
        )
    if list(x_train.columns) != list(x_test.columns):
        raise ValueError("train과 test의 feature 이름 또는 순서가 다릅니다.")
    if x_train.empty or x_test.empty:
        raise ValueError("train 또는 test 데이터가 비어 있습니다.")
    if x_train.isna().any().any() or x_test.isna().any().any():
        raise ValueError("모델 입력 feature에 결측치가 남아 있습니다.")

    allowed_labels = {0, 1}
    train_labels = set(int(value) for value in y_train.unique())
    test_labels = set(int(value) for value in y_test.unique())
    if not train_labels.issubset(allowed_labels) or not test_labels.issubset(allowed_labels):
        raise ValueError("label은 정상 0 또는 공격 1이어야 합니다.")
    if len(train_labels) < 2 or len(test_labels) < 2:
        raise ValueError("train과 test에는 정상 및 공격 label이 모두 있어야 합니다.")


def calculate_metrics(
    y_true: pd.Series,
    predictions: np.ndarray,
    attack_probabilities: np.ndarray,
) -> dict[str, Any]:
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    true_negative, false_positive, false_negative, true_positive = (
        int(value) for value in matrix.ravel()
    )

    return {
        "accuracy": float(accuracy_score(y_true, predictions)),
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "f1": float(f1_score(y_true, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, attack_probabilities)),
        "confusion_matrix": matrix.astype(int).tolist(),
        "confusion_matrix_labels": ["normal(0)", "attack(1)"],
        "confusion_matrix_details": {
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_positive": true_positive,
        },
    }


def label_distribution(labels: pd.Series) -> dict[str, int]:
    counts = labels.value_counts().sort_index()
    return {str(int(label)): int(count) for label, count in counts.items()}


def relative_path(path: Path) -> str:
    return path.relative_to(AI_ROOT).as_posix()


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    required_files = [X_TRAIN_FILE, Y_TRAIN_FILE, X_TEST_FILE, Y_TEST_FILE]
    require_files(required_files)

    print("[Baseline] 전처리된 데이터를 불러옵니다.")
    load_started = time.perf_counter()
    x_train = load_feature_data(X_TRAIN_FILE)
    y_train = load_label_data(Y_TRAIN_FILE)
    x_test = load_feature_data(X_TEST_FILE)
    y_test = load_label_data(Y_TEST_FILE)
    load_seconds = time.perf_counter() - load_started

    validate_data(x_train, y_train, x_test, y_test)
    print(f"  - train: {len(x_train)} rows, {x_train.shape[1]} features")
    print(f"  - test: {len(x_test)} rows, {x_test.shape[1]} features")

    model = LogisticRegression(
        C=REGULARIZATION_STRENGTH,
        class_weight=None,
        max_iter=MAX_ITERATIONS,
        random_state=RANDOM_STATE,
        solver="lbfgs",
    )

    print("[Baseline] Logistic Regression 학습을 시작합니다.")
    convergence_messages: list[str] = []
    training_started = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(x_train, y_train)
        for warning in caught_warnings:
            if issubclass(warning.category, ConvergenceWarning):
                convergence_messages.append(str(warning.message))
    training_seconds = time.perf_counter() - training_started

    print("[Baseline] 테스트 데이터 예측과 평가를 시작합니다.")
    prediction_started = time.perf_counter()
    predictions = model.predict(x_test)
    attack_probabilities = model.predict_proba(x_test)[:, 1]
    prediction_seconds = time.perf_counter() - prediction_started
    metrics = calculate_metrics(y_test, predictions, attack_probabilities)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_FILE, compress=3)
    model_size_bytes = MODEL_FILE.stat().st_size

    iterations = int(np.max(model.n_iter_))
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset": {
            "name": "UNSW-NB15",
            "split": "official training/testing split",
            "target": "label",
            "label_map": {"normal": 0, "attack": 1},
        },
        "model": {
            "name": "LogisticRegression",
            "solver": "lbfgs",
            "max_iter": MAX_ITERATIONS,
            "iterations_used": iterations,
            "converged": len(convergence_messages) == 0,
            "convergence_warnings": convergence_messages,
            "C": REGULARIZATION_STRENGTH,
            "class_weight": None,
            "random_state": RANDOM_STATE,
        },
        "data": {
            "train_rows": int(len(x_train)),
            "test_rows": int(len(x_test)),
            "feature_count": int(x_train.shape[1]),
            "dtype": "float32",
            "train_label_distribution": label_distribution(y_train),
            "test_label_distribution": label_distribution(y_test),
        },
        "metrics": metrics,
        "timing_seconds": {
            "data_loading": load_seconds,
            "training": training_seconds,
            "test_prediction_total": prediction_seconds,
            "test_prediction_per_sample": prediction_seconds / len(x_test),
        },
        "artifacts": {
            "model_path": relative_path(MODEL_FILE),
            "metrics_path": relative_path(METRICS_FILE),
            "model_size_bytes": model_size_bytes,
        },
    }
    save_json(METRICS_FILE, result)

    details = metrics["confusion_matrix_details"]
    print("[Baseline] 학습과 평가가 완료되었습니다.")
    print(f"  - accuracy: {metrics['accuracy']:.6f}")
    print(f"  - precision: {metrics['precision']:.6f}")
    print(f"  - recall: {metrics['recall']:.6f}")
    print(f"  - F1: {metrics['f1']:.6f}")
    print(f"  - ROC-AUC: {metrics['roc_auc']:.6f}")
    print(
        "  - confusion matrix: "
        f"TN={details['true_negative']}, FP={details['false_positive']}, "
        f"FN={details['false_negative']}, TP={details['true_positive']}"
    )
    print(f"  - training time: {training_seconds:.3f} seconds")
    print(f"  - model: {MODEL_FILE}")
    print(f"  - metrics: {METRICS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
