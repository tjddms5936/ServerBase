from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS_DIR = AI_ROOT / ".deps"
if LOCAL_DEPS_DIR.exists():
    sys.path.insert(0, str(LOCAL_DEPS_DIR))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier

from training.train_logistic_regression import (
    X_TEST_FILE,
    X_TRAIN_FILE,
    Y_TEST_FILE,
    Y_TRAIN_FILE,
    calculate_metrics,
    label_distribution,
    load_feature_data,
    load_label_data,
    relative_path,
    require_files,
    save_json,
    validate_data,
)


MODEL_FILE = AI_ROOT / "artifacts" / "baseline_random_forest.joblib"
METRICS_FILE = AI_ROOT / "reports" / "random_forest_metrics.json"
FEATURE_SCHEMA_FILE = AI_ROOT / "artifacts" / "feature_schema_unsw_nb15.json"
PREPROCESSOR_FILE = AI_ROOT / "artifacts" / "preprocessor_unsw_nb15.joblib"
DEFAULT_THRESHOLD = 0.5
MODEL_PARAMETERS = {
    "n_estimators": 100,
    "criterion": "gini",
    "max_depth": None,
    "min_samples_split": 2,
    "min_samples_leaf": 1,
    "max_features": "sqrt",
    "bootstrap": True,
    "class_weight": None,
    "random_state": 42,
    "n_jobs": min(4, os.cpu_count() or 1),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    inputs = [
        X_TRAIN_FILE, Y_TRAIN_FILE, X_TEST_FILE, Y_TEST_FILE,
        FEATURE_SCHEMA_FILE, PREPROCESSOR_FILE,
    ]
    require_files(inputs)
    print("[RandomForest] 전처리된 train/test 데이터를 불러옵니다.", flush=True)
    load_started = time.perf_counter()
    x_train = load_feature_data(X_TRAIN_FILE)
    y_train = load_label_data(Y_TRAIN_FILE)
    x_test = load_feature_data(X_TEST_FILE)
    y_test = load_label_data(Y_TEST_FILE)
    loading_seconds = time.perf_counter() - load_started
    validate_data(x_train, y_train, x_test, y_test)
    if not np.isfinite(x_train.to_numpy()).all() or not np.isfinite(x_test.to_numpy()).all():
        raise ValueError("입력 feature에 무한대 또는 결측치가 있습니다.")
    schema = json.loads(FEATURE_SCHEMA_FILE.read_text(encoding="utf-8"))
    if schema["processed_features"] != list(x_train.columns):
        raise ValueError("전처리 schema와 학습 입력의 feature 순서가 다릅니다.")

    # 설정은 test 결과를 보기 전에 고정하며, test 데이터는 학습에 넣지 않습니다.
    print(f"[RandomForest] 트리 100개를 학습합니다. 설정: {MODEL_PARAMETERS}", flush=True)
    model = RandomForestClassifier(**MODEL_PARAMETERS)
    training_started = time.perf_counter()
    model.fit(x_train, y_train)
    training_seconds = time.perf_counter() - training_started

    prediction_started = time.perf_counter()
    attack_index = int(np.flatnonzero(model.classes_ == 1)[0])
    probabilities = model.predict_proba(x_test)[:, attack_index]
    # 두 모델 모두 공격 확률이 0.5 이상이면 공격으로 판정하도록 기준을 맞춥니다.
    predictions = (probabilities >= DEFAULT_THRESHOLD).astype(np.int8)
    prediction_seconds = time.perf_counter() - prediction_started
    metrics = calculate_metrics(y_test, predictions, probabilities)
    details = metrics["confusion_matrix_details"]
    metrics["false_positive_rate"] = details["false_positive"] / int((y_test == 0).sum())
    metrics["false_negative_rate"] = details["false_negative"] / int((y_test == 1).sum())

    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_FILE, compress=3)
    importances = sorted(
        (
            {"feature": feature, "importance": float(importance)}
            for feature, importance in zip(x_train.columns, model.feature_importances_, strict=True)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset": {
            "name": "UNSW-NB15", "split": "official training/testing split",
            "target": "label", "label_map": {"normal": 0, "attack": 1},
        },
        "model": {
            "name": type(model).__name__, "parameters": MODEL_PARAMETERS,
            "threshold": DEFAULT_THRESHOLD, "positive_rule": "attack_probability >= threshold",
        },
        "data": {
            "train_rows": len(x_train), "test_rows": len(x_test),
            "feature_count": x_train.shape[1], "dtype": "float32",
            "train_label_distribution": label_distribution(y_train),
            "test_label_distribution": label_distribution(y_test),
            "input_sha256": {relative_path(path): file_sha256(path) for path in inputs},
        },
        "metrics": metrics,
        "timing_seconds": {
            "data_loading": loading_seconds, "training": training_seconds,
            "test_predict_proba": prediction_seconds,
        },
        "environment": {
            "python": platform.python_version(), "scikit_learn": sklearn.__version__,
            "numpy": np.__version__, "logical_cpu_count": os.cpu_count(),
            "platform": platform.platform(),
        },
        "feature_importances": importances,
        "feature_importance_note": "불순도 감소 기반 중요도이며, 인과관계나 검증된 원인으로 해석하지 않습니다.",
        "artifacts": {
            "model_path": relative_path(MODEL_FILE), "metrics_path": relative_path(METRICS_FILE),
            "model_size_bytes": MODEL_FILE.stat().st_size, "model_sha256": file_sha256(MODEL_FILE),
        },
    }
    save_json(METRICS_FILE, result)
    print("[RandomForest] 학습과 평가가 완료되었습니다.")
    for name in ["accuracy", "precision", "recall", "f1", "roc_auc", "false_positive_rate"]:
        print(f"  - {name}: {metrics[name]:.6f}")
    print(f"  - FP: {details['false_positive']}, FN: {details['false_negative']}")
    print(f"  - 학습 시간: {training_seconds:.3f}초")
    print(f"  - 모델: {MODEL_FILE}")
    print(f"  - 지표: {METRICS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
