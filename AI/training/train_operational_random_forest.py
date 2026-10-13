from __future__ import annotations

import argparse
import platform
import sys
import time
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from training.operational_data import load_operational_data
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


class TrainingConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    dataset_id: Identifier
    dataset_dir: Path
    artifact_dir: Path
    report_prefix: Path
    n_estimators: Annotated[int, Field(ge=1, le=1000, strict=True)] = 100
    criterion: Literal["gini"] = "gini"
    max_depth: Annotated[int, Field(ge=1, strict=True)] | None = None
    min_samples_split: Literal[2] = 2
    min_samples_leaf: Annotated[int, Field(ge=1, strict=True)] = 1
    max_features: Literal["sqrt"] = "sqrt"
    bootstrap: Literal[True] = True
    class_weight: None = None
    random_state: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)] = 42
    n_jobs: Literal[1] = 1
    threshold: Literal[0.5] = 0.5


def load_config(path: Path) -> TrainingConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "artifact_dir", "report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return TrainingConfig.model_validate(values)


def model_parameters(config: TrainingConfig) -> dict:
    names = ("n_estimators", "criterion", "max_depth", "min_samples_split", "min_samples_leaf",
             "max_features", "bootstrap", "class_weight", "random_state", "n_jobs")
    return {name: getattr(config, name) for name in names}


def render_report(report: dict) -> str:
    metrics = report["validation_metrics"]
    parameters = report["model_parameters"]
    lines = ["# 운영 RandomForest 초기 기준 모델", "", "## 학습 범위", "",
             "검증된 동일 운영 feature 7개로 train만 학습했다. RF는 크기 기준으로 분기하는 트리 모델이므로 scaler를 사용하지 않았다.",
             "validation은 예측에만 사용했고 test CSV는 해시 검사에도 열지 않았다. FastAPI는 여전히 dummy 상태다.", "",
             f"- train {report['train_metrics']['rows']}건, validation {metrics['rows']}건",
             f"- 트리 {parameters['n_estimators']}개, max_depth={parameters['max_depth']}, min_samples_leaf={parameters['min_samples_leaf']}",
             f"- max_features={parameters['max_features']}, bootstrap={parameters['bootstrap']}, seed={parameters['random_state']}, n_jobs={parameters['n_jobs']}",
             "- threshold=0.5는 사전에 고정한 초기 기준이며 validation에서 선정하지 않았다.",
             "- 트리 수는 LR의 최적화 반복 횟수와 다른 개념이다. RF에는 LR 같은 수렴 판정을 적용하지 않는다.", "",
             "## validation 결과", "", "| 지표 | 값 |", "| --- | ---: |"]
    for name in ("accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "log_loss", "false_positive_rate"):
        lines.append(f"| {name} | {metrics[name]:.6f} |")
    matrix = metrics["confusion_matrix"]
    lines += ["", f"TN={matrix['tn']}, FP={matrix['fp']}, FN={matrix['fn']}, TP={matrix['tp']}.", "",
              "## 시나리오별 결과", "", "| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["validation_by_scenario"].items():
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.6f}"
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.6f}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## 불순도 감소 기반 중요도", "", "| 입력 | 중요도 |", "| --- | ---: |"]
    for item in report["feature_importances"]:
        lines.append(f"| {item['feature']} | {item['importance']:.6f} |")
    lines += ["", "## 해석과 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 산출물", "", f"모델 묶음: `{report['artifact_dir']}`",
              "", "Pipeline에 RF만 보관하며 전처리는 identity로 명시한다. schema·label map·고정 threshold·메시지별 validation 예측·완료 manifest를 함께 저장한다.",
              "", "## 다음 작업", "", "저장한 LR/RF를 동일 validation에서 비교하고, 그 다음 별도 단계에서 제한된 설정·threshold 후보를 선정한다. test는 최종 비교까지 보류한다.", ""]
    return "\n".join(lines)


def train_model(config: TrainingConfig) -> dict:
    report_json, report_md = config.report_prefix.with_suffix(".json"), config.report_prefix.with_suffix(".md")
    if config.artifact_dir.exists() or report_json.exists() or report_md.exists():
        raise FileExistsError("기존 실험은 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    parameters = model_parameters(config)
    # LR과 같은 원본 입력을 사용하되, RF에는 불필요한 표준화를 추가하지 않는다.
    pipeline = Pipeline([("classifier", RandomForestClassifier(**parameters))])
    started = time.perf_counter()
    pipeline.fit(data.train.X, data.train.y)
    fit_seconds = time.perf_counter() - started
    classifier = pipeline.named_steps["classifier"]
    if list(classifier.classes_) != [0, 1]:
        raise ValueError("학습 모델의 클래스 순서가 운영 정답과 다릅니다.")
    probability_train = pipeline.predict_proba(data.train.X)[:, 1]
    probability_validation = pipeline.predict_proba(data.validation.X)[:, 1]
    prediction = (probability_validation >= config.threshold).astype("int64")
    split_counts = {name: {"rows": len(split.y), "runs": split.metadata["run_id"].nunique(),
                           "sessions": len(split.metadata[["run_id", "session_id"]].drop_duplicates()),
                           "label_distribution": {str(label): int((split.y == label).sum()) for label in (0, 1)}}
                    for name, split in (("train", data.train), ("validation", data.validation))}
    depths = [tree.tree_.max_depth for tree in classifier.estimators_]
    nodes = [tree.tree_.node_count for tree in classifier.estimators_]
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment": "operational_random_forest_fixed_baseline", "dataset_id": data.dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "config": config.model_dump(mode="json"), "model_parameters": parameters, "label_map": data.label_map,
        "fit_scope": "train_only", "preprocessing": "identity_no_scaler", "evaluated_splits": ["train", "validation"],
        "test_files_read": False, "test_evaluated": False, "threshold_selection": "fixed_baseline_not_tuned",
        "deployment_ready": False, "artifact_dir": str(config.artifact_dir), "dataset_splits": split_counts,
        "training": {"fit_seconds": fit_seconds, "trees_fitted": len(classifier.estimators_),
                     "convergence": {"applicable": False, "reason": "RF는 지정한 트리 수를 구성하며 LR의 수렴 반복과 다르다."},
                     "tree_depth": {"minimum": int(min(depths)), "maximum": int(max(depths)), "mean": float(np.mean(depths))},
                     "total_tree_nodes": int(sum(nodes))},
        "constant_training_features": [name for name in FEATURE_NAMES if data.train.X[name].nunique() == 1],
        "train_metrics": calculate_metrics(data.train.y, probability_train, config.threshold),
        "validation_metrics": calculate_metrics(data.validation.y, probability_validation, config.threshold),
        "validation_by_scenario": scenario_metrics(data.validation, prediction),
        "feature_importances": sorted([{"feature": name, "importance": float(value)}
                                       for name, value in zip(FEATURE_NAMES, classifier.feature_importances_, strict=True)],
                                      key=lambda item: (-item["importance"], item["feature"])),
        "input_sha256": data.input_sha256,
        "code_sha256": {name: file_hash(AI_ROOT / "training" / name) for name in
                        ("operational_data.py", "train_operational_logistic_regression.py", "train_operational_random_forest.py")},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "scikit_learn": sklearn.__version__, "numpy": np.__version__,
                        "pandas": pd.__version__, "joblib": joblib.__version__},
        "limitations": [
            "통제된 시나리오 정답의 초기 검증 결과이며 실제 침입 탐지 성능이 아니다.",
            f"Train 캠페인 {data.train.metadata['campaign_id'].nunique()}개, Validation 캠페인 {data.validation.metadata['campaign_id'].nunique()}개다. 동일 생성기 편향이 남으며 메시지마다 독립 표본인 것은 아니다.",
            "불순도 감소 기반 중요도는 학습 분기의 사용 정도다. 값의 종류가 많은 입력에 편향될 수 있고 인과관계나 원인으로 해석하지 않는다.",
            "packet_size와 payload_size는 중복 정보라 중요도를 나누어 가질 수 있다. error_count_total은 train에서 상수 0이다.",
            "현재 snapshot 이후 발생한 본문 오류는 입력에 포함되지 않는다. RF 도입만으로 관측 불가능한 정보를 복원하지 못한다.",
            "트리 수·설정·threshold 탐색, 최종 test 평가, 지연시간·메모리 비교와 FastAPI 모델 연결은 아직 하지 않았다.",
        ],
    }
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    model_path = config.artifact_dir / "pipeline.joblib"
    joblib.dump(pipeline, model_path, compress=3)
    if not np.array_equal(probability_validation, joblib.load(model_path).predict_proba(data.validation.X)[:, 1]):
        raise ValueError("저장 후 RF 예측이 달라졌습니다.")
    write_json(config.artifact_dir / "feature_schema.json", {
        **data.schema, "preprocessing": {"status": "not_required", "fit_scope": "not_applicable",
                                        "kind": "identity", "stored_in": "pipeline.joblib"},
    })
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
    # 저장 검증과 보고서까지 끝난 뒤에만 완료 상태를 남긴다.
    write_json(config.artifact_dir / "training_manifest.json", {
        "status": "complete", "deployment_ready": False, "dataset_id": data.dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "artifacts": report["artifacts"],
        "input_sha256": data.input_sha256, "reports": {str(path): file_hash(path) for path in (report_json, report_md)},
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 RF 고정 초기 학습과 validation 평가. test는 읽지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_random_forest.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        for name in ("artifact_dir", "report_prefix"):
            override = getattr(args, name)
            if override is not None:
                values[name] = override.resolve()
        report = train_model(TrainingConfig.model_validate(values))
    except (OSError, ValueError, KeyError) as error:
        print(f"[운영 RF] 실패: {error}", file=sys.stderr)
        return 2
    metrics = report["validation_metrics"]
    matrix = metrics["confusion_matrix"]
    print(f"[운영 RF] train {report['train_metrics']['rows']}건 / validation {metrics['rows']}건 / test 미사용")
    print(f"[운영 RF] 트리 {report['training']['trees_fitted']}개 / threshold=0.5")
    print(f"[운영 RF] F1={metrics['f1']:.6f}, Recall={metrics['recall']:.6f}, FPR={metrics['false_positive_rate']:.6f}")
    print(f"[운영 RF] FP={matrix['fp']}, FN={matrix['fn']} / 산출물: {report['artifact_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
