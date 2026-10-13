from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
from pydantic import Field
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, average_precision_score, confusion_matrix, f1_score,
    log_loss, precision_score, recall_score, roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from training.operational_data import OperationalSplit, load_operational_data


class TrainingConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    dataset_id: Identifier
    dataset_dir: Path
    artifact_dir: Path
    report_prefix: Path
    C: Annotated[float, Field(gt=0, strict=True)] = 1.0
    max_iter: Annotated[int, Field(ge=1, strict=True)] = 1000
    tol: Annotated[float, Field(gt=0, strict=True)] = 0.0001
    solver: Literal["lbfgs"] = "lbfgs"
    random_state: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)] = 42
    threshold: Literal[0.5] = 0.5


def load_config(path: Path) -> TrainingConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "artifact_dir", "report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return TrainingConfig.model_validate(values)


def calculate_metrics(y: np.ndarray, probability: np.ndarray, threshold: float) -> dict:
    prediction = (probability >= threshold).astype("int64")
    tn, fp, fn, tp = (int(value) for value in confusion_matrix(y, prediction, labels=[0, 1]).ravel())
    return {
        "rows": len(y), "accuracy": float(accuracy_score(y, prediction)),
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "f1": float(f1_score(y, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "average_precision": float(average_precision_score(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "false_positive_rate": fp / (fp + tn), "false_negative_rate": fn / (fn + tp),
        "confusion_matrix": {"label_order": [0, 1], "rows": "actual", "columns": "predicted",
                             "values": [[tn, fp], [fn, tp]], "tn": tn, "fp": fp, "fn": fn, "tp": tp},
    }


def scenario_metrics(split: OperationalSplit, prediction: np.ndarray) -> dict:
    result = {}
    for scenario in sorted(split.metadata["scenario_id"].unique()):
        selected = (split.metadata["scenario_id"] == scenario).to_numpy()
        y, predicted = split.y[selected], prediction[selected]
        negatives, positives = int((y == 0).sum()), int((y == 1).sum())
        fp, fn = int(((y == 0) & (predicted == 1)).sum()), int(((y == 1) & (predicted == 0)).sum())
        result[scenario] = {"rows": len(y), "normal_rows": negatives, "controlled_anomaly_rows": positives,
                            "fp": fp, "fn": fn, "recall": (positives - fn) / positives if positives else None,
                            "false_positive_rate": fp / negatives if negatives else None}
    return result


def write_json(path: Path, document: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def render_report(report: dict) -> str:
    metrics = report["validation_metrics"]
    convergence = report["training"]["convergence"]
    lines = ["# 운영 Logistic Regression 초기 기준 모델", "", "## 이번에 수행한 작업", "",
             "고정 운영 데이터셋의 train만으로 StandardScaler와 Logistic Regression을 학습했다. validation에는 transform과 예측만 수행했다.",
             "정답 0은 정상, 1은 통제된 이상이다. test CSV는 읽지 않았으며 FastAPI는 여전히 dummy 추론을 사용한다.", "",
             "## 학습 조건", "",
             f"- 데이터셋: `{report['dataset_id']}` / 의미 계약: `{report['feature_contract_id']}`",
             f"- train {report['train_metrics']['rows']}건, validation {metrics['rows']}건, 입력 {len(FEATURE_NAMES)}개",
             f"- 최대 반복 {report['config']['max_iter']}회, 실제 반복 {convergence['iterations_used']}회, 수렴 확인: {convergence['converged']}",
             f"- 설정: C={report['config']['C']}, solver={report['config']['solver']}, tol={report['config']['tol']}",
             "- threshold=0.5는 사전에 고정한 baseline 기준이며 validation 최적화로 선택한 값이 아니다.",
             "- 학습 손실은 확률의 오차와 L2 규제를 고려한다. 정답률을 100%로 만드는 반복 작업이 아니다.", "",
             "## validation 결과", "", "| 지표 | 값 |", "| --- | ---: |"]
    for name in ("accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "log_loss", "false_positive_rate"):
        lines.append(f"| {name} | {metrics[name]:.6f} |")
    matrix = metrics["confusion_matrix"]
    lines += ["", f"TN={matrix['tn']}, FP={matrix['fp']}, FN={matrix['fn']}, TP={matrix['tp']}. FP는 정상 오탐, FN은 통제된 이상 누락이다.",
              "", "## 시나리오별 validation 결과", "", "| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["validation_by_scenario"].items():
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.6f}"
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.6f}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## 해석과 한계", ""] + [f"- {item}" for item in report["limitations"]]
    lines += ["", "## 산출물", "", f"모델 묶음: `{report['artifact_dir']}`",
              "", "JSON에 scaler의 평균·분산, 표준화 입력에 대한 가중치 7개·기본값, 실제 반복 수, 입력·코드 해시와 실행 환경을 남겼다.",
              "validation_predictions.csv는 추적 metadata, 정답, 확률, 예측 및 오분류 종류를 보관한다. metadata는 학습 입력이 아니다.",
              "", "## 다음 작업", "", "동일한 train/validation으로 운영 RandomForest를 학습해 LR과 비교한다. 이후 제한된 설정·threshold를 validation에서 선정하며 test는 최종 비교까지 보류한다.", ""]
    return "\n".join(lines)


def train_model(config: TrainingConfig) -> dict:
    report_json, report_md = config.report_prefix.with_suffix(".json"), config.report_prefix.with_suffix(".md")
    if config.artifact_dir.exists() or report_json.exists() or report_md.exists():
        raise FileExistsError("기존 실험은 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(C=config.C, max_iter=config.max_iter, tol=config.tol,
                                          solver=config.solver, random_state=config.random_state, class_weight=None)),
    ])
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ConvergenceWarning)
        # fit은 한 번만 호출한다. validation의 평균·정답은 scaler와 가중치 학습에 사용하지 않는다.
        pipeline.fit(data.train.X, data.train.y)
    fit_seconds = time.perf_counter() - started
    warning_messages = [str(item.message) for item in captured if issubclass(item.category, ConvergenceWarning)]
    scaler, classifier = pipeline.named_steps["scaler"], pipeline.named_steps["classifier"]
    if list(classifier.classes_) != [0, 1]:
        raise ValueError("학습 모델의 클래스 순서가 운영 label map과 다릅니다.")
    probability_train = pipeline.predict_proba(data.train.X)[:, 1]
    probability_validation = pipeline.predict_proba(data.validation.X)[:, 1]
    prediction = (probability_validation >= config.threshold).astype("int64")
    iterations = int(classifier.n_iter_.max())
    converged = not warning_messages
    split_counts = {name: {"rows": len(split.y), "runs": split.metadata["run_id"].nunique(),
                           "sessions": len(split.metadata[["run_id", "session_id"]].drop_duplicates()),
                           "label_distribution": {str(label): int((split.y == label).sum()) for label in (0, 1)}}
                    for name, split in (("train", data.train), ("validation", data.validation))}
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment": "operational_logistic_regression_fixed_baseline", "dataset_id": data.dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "config": config.model_dump(mode="json"), "label_map": data.label_map,
        "fit_scope": "train_only", "evaluated_splits": ["train", "validation"],
        "test_files_read": False, "test_evaluated": False, "threshold_selection": "fixed_baseline_not_tuned",
        "deployment_ready": False, "artifact_dir": str(config.artifact_dir),
        "dataset_splits": split_counts,
        "training": {"fit_seconds": fit_seconds,
                     "convergence": {"max_iter": config.max_iter, "iterations_used": iterations,
                                     "converged": converged, "warnings": warning_messages}},
        "learned_parameters": {
            "coefficient_space": "standardized_features", "positive_class": "controlled_anomaly",
            "weights": dict(zip(FEATURE_NAMES, classifier.coef_[0].tolist())), "intercept": float(classifier.intercept_[0]),
            "scaler_mean": dict(zip(FEATURE_NAMES, scaler.mean_.tolist())),
            "scaler_variance": dict(zip(FEATURE_NAMES, scaler.var_.tolist())),
            "scaler_scale": dict(zip(FEATURE_NAMES, scaler.scale_.tolist())),
            "scaler_samples_seen": int(scaler.n_samples_seen_),
            "constant_training_features": [name for name, variance in zip(FEATURE_NAMES, scaler.var_) if variance == 0],
        },
        "train_metrics": calculate_metrics(data.train.y, probability_train, config.threshold),
        "validation_metrics": calculate_metrics(data.validation.y, probability_validation, config.threshold),
        "validation_by_scenario": scenario_metrics(data.validation, prediction),
        "input_sha256": data.input_sha256,
        "code_sha256": {name: file_hash(AI_ROOT / "training" / name)
                        for name in ("operational_data.py", "train_operational_logistic_regression.py")},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "scikit_learn": sklearn.__version__, "numpy": np.__version__,
                        "pandas": pd.__version__, "joblib": joblib.__version__},
        "limitations": [
            "통제된 시나리오 정답을 학습한 초기 결과이며 실제 침입 탐지 성능이 아니다.",
            "분할별 run·세션·메시지 수는 dataset_splits에 기록한다. 같은 세션의 메시지는 독립 표본이 아니다.",
            f"Train 캠페인 {data.train.metadata['campaign_id'].nunique()}개, Validation 캠페인 {data.validation.metadata['campaign_id'].nunique()}개다. 동일 생성기 특성에 적응했을 수 있으며 외부 도메인 일반화는 검증하지 않았다.",
            "error_count_total은 train에서 상수 0이다. 현재 본문의 검증 실패는 snapshot 이후라 이 입력에 반영되지 않는다.",
            "packet_size와 payload_size는 8바이트 차이인 중복 정보다. 가중치를 원인 또는 독립적인 feature 중요도로 해석하지 않는다.",
            "train/validation 손실은 규제항을 제외한 평균 log loss다. solver의 수렴 기준이나 전체 최적화 목적값과 동일하지 않다.",
            "test 최종 평가·설정/threshold 선정·지연시간/메모리 비교·FastAPI 모델 연결은 아직 하지 않았다.",
        ],
    }
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    pipeline_path = config.artifact_dir / "pipeline.joblib"
    joblib.dump(pipeline, pipeline_path, compress=3)
    # 저장 후 다시 로딩해 scaler와 모델이 함께 보관되고 예측이 유지되는지 확인한다.
    reloaded = joblib.load(pipeline_path)
    if not np.array_equal(probability_validation, reloaded.predict_proba(data.validation.X)[:, 1]):
        raise ValueError("저장한 Pipeline의 재로딩 예측이 다릅니다.")
    schema = {**data.schema, "preprocessing": {"status": "fitted", "fit_scope": "train_only",
                                              "kind": "StandardScaler", "stored_in": "pipeline.joblib:scaler"}}
    write_json(config.artifact_dir / "feature_schema.json", schema)
    write_json(config.artifact_dir / "label_map.json", data.label_map)
    write_json(config.artifact_dir / "decision_policy.json", {
        "positive_class": 1, "threshold": config.threshold, "comparison": ">=",
        "selection": "fixed_baseline_not_tuned", "deployment_ready": False,
    })
    predictions = data.validation.metadata.copy()
    predictions["label"] = data.validation.y
    predictions["anomaly_probability"] = probability_validation
    predictions["predicted_label"] = prediction
    predictions["error_type"] = np.where(prediction == data.validation.y, "correct",
                                         np.where(prediction == 1, "false_positive", "false_negative"))
    predictions.to_csv(config.artifact_dir / "validation_predictions.csv", index=False, encoding="utf-8")
    report["artifacts"] = {path.name: {"sha256": file_hash(path), "bytes": path.stat().st_size}
                           for path in sorted(config.artifact_dir.iterdir())}
    report_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    with report_md.open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    # 완료 표시는 모든 산출물과 보고서 저장이 성공한 뒤에만 기록한다. 실패한 부분 출력은 완료가 아니다.
    write_json(config.artifact_dir / "training_manifest.json", {
        "status": "complete", "converged": converged, "deployment_ready": False,
        "dataset_id": data.dataset_id, "feature_contract_id": data.schema["feature_contract_id"],
        "artifacts": report["artifacts"], "input_sha256": data.input_sha256,
        "reports": {str(path): file_hash(path) for path in (report_json, report_md)},
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 train 전용 LR 학습과 validation 평가. test는 읽지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_logistic_regression.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        values = config.model_dump()
        for name in ("artifact_dir", "report_prefix"):
            override = getattr(args, name)
            if override is not None:
                values[name] = override.resolve()
        report = train_model(TrainingConfig.model_validate(values))
    except (OSError, ValueError, KeyError) as error:
        print(f"[운영 LR] 실패: {error}", file=sys.stderr)
        return 2
    convergence = report["training"]["convergence"]
    metrics = report["validation_metrics"]
    print(f"[운영 LR] 학습 {report['train_metrics']['rows']}건 / validation {metrics['rows']}건 / test 미사용")
    print(f"[운영 LR] 반복 {convergence['iterations_used']}/{convergence['max_iter']}회 / 수렴 {convergence['converged']}")
    print(f"[운영 LR] validation F1={metrics['f1']:.6f}, Recall={metrics['recall']:.6f}, FPR={metrics['false_positive_rate']:.6f}")
    print(f"[운영 LR] 산출물: {report['artifact_dir']}")
    return 0 if convergence["converged"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
