from __future__ import annotations

import argparse
import hashlib
import json
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

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_collection.recovery_design import compile_recovery_plans
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_prefix_features import TRANSFORM_COLUMNS, prefix_observations, transform_prefix_features
from evaluation.compare_operational_baselines import load_verified_model
from evaluation.compare_operational_recovery import compare, make_trial, choose_trial, render_report
from training.operational_data import load_operational_data
from training.train_operational_logistic_regression import calculate_metrics, write_json


class RecoveryConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    experiment_id: Identifier
    dataset_id: Identifier
    dataset_dir: Path
    design_path: Path
    previous_plan: Path
    collection_report: Path
    reference_dataset_id: Identifier
    reference_dataset_dir: Path
    reference_model_dir: Path
    artifact_dir: Path
    report_prefix: Path
    comparison_report_prefix: Path
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)] = 7976
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)] = 2737


def load_config(path: Path) -> RecoveryConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "design_path", "previous_plan", "collection_report", "reference_dataset_dir",
                 "reference_model_dir", "artifact_dir", "report_prefix", "comparison_report_prefix"):
        values[name] = (path.parent / values[name]).resolve()
    return RecoveryConfig.model_validate(values)


def verify_provenance(config: RecoveryConfig, data, design: dict) -> tuple[dict, dict]:
    collection = read_json(config.collection_report)
    root = Path(collection["batch_path"])
    registry_path, completion_path = root / "assignment_manifest.json", root / "completion_manifest.json"
    registry, completion = read_json(registry_path), read_json(completion_path)
    manifest_path = config.dataset_dir / "dataset_manifest.json"
    if (collection["status"] != "complete" or completion["status"] != "complete" or
            not collection["quality"]["passed"] or collection["model_training_performed"] is not False or
            collection["trained_model_prediction_performed"] is not False or collection["test_model_evaluated"] is not False or
            Path(collection["dataset_output"]).resolve() != config.dataset_dir.resolve() or
            collection["dataset_manifest_sha256"] != data.input_sha256["dataset_manifest.json"] or
            registry != collection["assignment"] or registry["recovery_plan_snapshot"] != design or
            registry["design_sha256"] != file_hash(config.design_path) or registry["target_dataset_id"] != data.dataset_id):
        raise ValueError("완료 수집·입력·사전 보정 계획의 근거가 다릅니다.")
    evidence = {str(config.collection_report): completion["output_sha256"][str(config.collection_report)],
                str(registry_path): collection["assignment_sha256"],
                str(manifest_path): collection["dataset_manifest_sha256"]}
    if completion["locked_input_sha256"][str(registry_path)] != evidence[str(registry_path)]:
        raise ValueError("수집 전에 고정한 캠페인 역할 registry가 다릅니다.")
    evidence[str(completion_path)] = file_hash(completion_path)
    for name, digest in data.input_sha256.items():
        path = str(config.dataset_dir / name)
        if completion["output_sha256"].get(path) != digest:
            raise ValueError(f"수집 완료 기록과 학습 입력 해시가 다릅니다: {name}")
        evidence[path] = digest
    verify_hashes(evidence)
    for split in ("train", "validation"):
        source = getattr(data, split)
        entries = [entry for entry in registry["campaigns"] if entry["split"] == split]
        if set(source.metadata.campaign_id) != {entry["campaign_id"] for entry in entries}:
            raise ValueError("학습·선정 메시지의 캠페인이 사전 역할과 다릅니다.")
        if len(source.y) != registry["planned_summary"]["splits"][split]["expected_message_rows"]:
            raise ValueError("사전 기대값과 Train/Validation 행 수가 다릅니다.")
    if set(data.train.metadata.campaign_id) & set(data.validation.metadata.campaign_id):
        raise ValueError("Train/Validation 캠페인 식별자가 겹칩니다.")
    # 수집 원본과 Test 파일은 다시 열지 않는다. 완료 근거와 허용한 처리 파일만 검증한다.
    return registry, evidence


def load_reference(config: RecoveryConfig, data, registry: dict) -> tuple[Pipeline, dict, dict]:
    frozen = registry["reference_baseline"]
    old = load_operational_data(config.reference_dataset_dir, config.reference_dataset_id)
    pipeline, training_report, model_evidence, _ = load_verified_model(config.reference_model_dir, "rf", old)
    model_path = config.reference_model_dir / "pipeline.joblib"
    if (frozen["candidate_id"] != "rf_leaf_5" or frozen["threshold"] != 0.3 or
            frozen["deployment_approval"] != "withheld_after_test" or
            file_hash(model_path) != frozen["model_sha256"] or
            old.label_map != data.label_map or old.schema["raw_input_features"] != data.schema["raw_input_features"]):
        raise ValueError("수집 전에 고정한 기준 RF·정답·원본 입력 의미가 다릅니다.")
    verify_hashes(frozen["file_sha256"])
    evidence = dict(frozen["file_sha256"])
    evidence.update({str(config.reference_dataset_dir / name): digest for name, digest in old.input_sha256.items()})
    evidence[str(config.reference_model_dir / "training_manifest.json")] = model_evidence["training_manifest_sha256"]
    evidence[model_evidence["report_path"]] = model_evidence["report_sha256"]
    for name, description in model_evidence["artifacts"].items():
        evidence[str(config.reference_model_dir / name)] = description["sha256"]
    return pipeline, {"candidate_id": "rf_leaf_5", "threshold": 0.3, "model_sha256": frozen["model_sha256"],
                      "previous_train_rows": len(old.train.y), "deployment_approval": "withheld_after_test",
                      "training_parameters": training_report["model_parameters"]}, evidence


def matrix_hash(frame: pd.DataFrame) -> str:
    header = json.dumps(list(frame.columns), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(header + frame.to_numpy(dtype="<f8").tobytes(order="C")).hexdigest()


def input_schema(transform_id: str, data) -> dict:
    stateful = transform_id in {"prefix_mean5_v1", "prefix_mean_parsed6_v1"}
    return {"schema_version": "recovery_model_input_v1", "dataset_id": data.dataset_id,
            "raw_feature_contract_id": data.schema["feature_contract_id"], "raw_input_features": FEATURE_NAMES,
            "input_transform_id": transform_id, "model_input_features": TRANSFORM_COLUMNS[transform_id],
            "preprocessing": "identity_no_scaler", "requires_ordered_session_prefix": stateful,
            "group_key": ["campaign_id", "run_id", "session_id"], "order": "request_index",
            "derivations": {"prefix_mean_interval_ms": "첫 간격을 제외한 과거·현재 간격 합 / (request_index - 1), 첫 메시지는 0",
                            "parsed_bytes_prefix_total": "같은 세션의 과거·현재 packet_size 합"} if stateful else {},
            "derived_units": {"prefix_mean_interval_ms": "ms", "parsed_bytes_prefix_total": "byte"} if stateful else {},
            "history_scope": "past_and_current_only" if stateful else "current_raw_snapshot",
            "missing_history_policy": "reject_without_zero_fill", "live_transform_implemented": False,
            "excluded_predictor_fields": ["label", "scenario_id", "campaign_id", "run_id", "session_id", "request_index"],
            "metadata_usage": "세션 식별자는 상태 경계에, request_index는 순서·누락 검사와 평균 분모 계산에 사용한다. 원본 식별자를 모델 입력 열에 직접 넣지 않는다."}


def fit_candidate(root: Path, candidate: dict, data, train_X: pd.DataFrame, validation_X: pd.DataFrame,
                  parameters: dict, lock_path: Path) -> tuple[dict, np.ndarray]:
    if not lock_path.is_file():
        raise ValueError("학습 전에 실험 protocol을 고정해야 합니다.")
    directory = root / "candidates" / candidate["id"]
    directory.mkdir(parents=True, exist_ok=False)
    pipeline = Pipeline([("classifier", RandomForestClassifier(**parameters))])
    started = time.perf_counter()
    # 후보마다 정확히 한 번, 변환한 새 Train과 독립 정답만 fit한다.
    pipeline.fit(train_X, data.train.y)
    seconds = time.perf_counter() - started
    classifier = pipeline.named_steps["classifier"]
    if list(pipeline.classes_) != [0, 1] or list(pipeline.feature_names_in_) != list(train_X.columns):
        raise ValueError("학습 모델의 클래스·입력 순서가 다릅니다.")
    probability_train = pipeline.predict_proba(train_X)[:, 1]
    probability_validation = pipeline.predict_proba(validation_X)[:, 1]
    model_path = directory / "pipeline.joblib"
    joblib.dump(pipeline, model_path, compress=3)
    restored = joblib.load(model_path)
    if (not np.array_equal(probability_train, restored.predict_proba(train_X)[:, 1]) or
            not np.array_equal(probability_validation, restored.predict_proba(validation_X)[:, 1])):
        raise ValueError("후보 저장 후 Train/Validation 확률이 달라졌습니다.")
    write_json(directory / "model_input_schema.json", input_schema(candidate["input_transform_id"], data))
    write_json(directory / "default_decision_policy.json", {
        "positive_class": 1, "threshold": 0.5, "comparison": ">=", "role": "default_not_selected_policy",
        "deployment_ready": False})
    summary = {**candidate, "model_path": str(model_path), "model_sha256": file_hash(model_path),
               "train_rows": len(data.train.y), "fit_scope": "new_train_only", "fit_calls": 1,
               "fit_seconds": seconds, "trees_fitted": len(classifier.estimators_),
               "model_parameters": classifier.get_params(), "input_columns": list(train_X.columns),
               "train_matrix_sha256": matrix_hash(train_X), "validation_matrix_sha256": matrix_hash(validation_X),
               "total_tree_nodes": sum(tree.tree_.node_count for tree in classifier.estimators_),
               "tree_depth_minimum": min(tree.tree_.max_depth for tree in classifier.estimators_),
               "tree_depth_maximum": max(tree.tree_.max_depth for tree in classifier.estimators_),
               "feature_importances": dict(zip(train_X.columns, classifier.feature_importances_.tolist())),
               "constant_train_features": [name for name in train_X.columns if train_X[name].nunique() == 1],
               "train_default_metrics": calculate_metrics(data.train.y, probability_train, 0.5),
               "validation_default_metrics": calculate_metrics(data.validation.y, probability_validation, 0.5),
               "test_evaluated": False, "scaler_fitted": False, "deployment_ready": False}
    write_json(directory / "training_summary.json", summary)
    saved = data.validation.metadata.copy()
    saved["label"], saved["anomaly_probability"] = data.validation.y, probability_validation
    saved["default_threshold"], saved["default_prediction"] = 0.5, (probability_validation >= 0.5).astype("int64")
    saved.to_csv(directory / "validation_predictions.csv", index=False, encoding="utf-8", mode="x")
    replay = pd.read_csv(directory / "validation_predictions.csv", float_precision="round_trip", dtype={"row_id": str})
    if (replay.row_id.tolist() != data.validation.metadata.row_id.tolist() or
            not np.array_equal(replay.label.to_numpy(), data.validation.y) or
            not np.array_equal(replay.anomaly_probability.to_numpy(), probability_validation)):
        raise ValueError("후보 예측 CSV의 행·정답·확률이 다릅니다.")
    return summary, probability_validation


def run(config: RecoveryConfig, config_path: Path | None = None) -> dict:
    outputs = [prefix.with_suffix(extension) for prefix in (config.report_prefix, config.comparison_report_prefix)
               for extension in (".json", ".md")]
    if len({path.resolve() for path in outputs}) != 4:
        raise ValueError("학습 보고서와 비교 보고서는 서로 다른 경로여야 합니다.")
    if config.artifact_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 보정 실험을 덮어쓰지 않습니다. 새 artifact-dir와 두 보고서 경로를 지정하세요.")
    if any(path.resolve().is_relative_to(root.resolve()) for path in [config.artifact_dir, *outputs]
           for root in (config.dataset_dir, config.reference_dataset_dir, config.reference_model_dir)):
        raise ValueError("기존 데이터셋·모델 내부에는 실험 산출물을 저장하지 않습니다.")
    design = read_json(config.design_path)
    compile_recovery_plans(design, read_json(config.previous_plan))
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    if len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows:
        raise ValueError("Train/Validation 행 수가 사전 고정 설정과 다릅니다.")
    registry, protected = verify_provenance(config, data, design)
    raw_root = Path(registry["campaigns"][0]["campaign_dir"]).parent
    if any(path.resolve().is_relative_to(raw_root.resolve()) for path in [config.artifact_dir, *outputs]):
        raise ValueError("원본 수집 batch에는 실험 산출물을 저장하지 않습니다.")
    reference, reference_description, reference_evidence = load_reference(config, data, registry)
    protected.update(reference_evidence)
    protected.update({str(config.dataset_dir / name): digest for name, digest in data.input_sha256.items()})
    sources = [config.design_path, config.previous_plan, Path(__file__).resolve(),
               AI_ROOT / "evaluation" / "compare_operational_recovery.py",
               AI_ROOT / "evaluation" / "compare_operational_baselines.py",
               AI_ROOT / "evaluation" / "compare_operational_augmentation.py",
               AI_ROOT / "training" / "operational_data.py", AI_ROOT / "training" / "train_operational_logistic_regression.py",
               AI_ROOT / "data_preparation" / "operational_prefix_features.py",
               AI_ROOT / "data_collection" / "recovery_design.py"]
    if config_path is not None:
        sources.append(config_path.resolve())
    protected.update({str(path): file_hash(path) for path in sources})
    experiment, criteria = design["development_experiment"], design["approval_criteria"]
    prefixes = {name: prefix_observations(getattr(data, name)) for name in ("train", "validation")}
    matrices = {item["id"]: {name: transform_prefix_features(getattr(data, name), item["input_transform_id"], prefixes[name])
                             for name in ("train", "validation")} for item in experiment["candidates"]}
    protocol = {"config": config.model_dump(mode="json"), "recovery_plan": design,
                "reference": reference_description, "full_rf_parameters": RandomForestClassifier(**experiment["model_parameters"]).get_params(),
                "environment": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                                "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__},
                "transformed_input_sha256": {key: {name: matrix_hash(frame) for name, frame in value.items()}
                                               for key, value in matrices.items()},
                "new_validation_prior_use": "collection_quality_and_fixed_input_checks_only_no_model_predictions",
                "fit_scope": "new_train_only", "cost_measured": False, "test_files_read": False}
    protocol_hash = hashlib.sha256(json.dumps(protocol, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    verify_hashes(protected)
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    lock_path = config.artifact_dir / "protocol_lock.json"
    write_json(lock_path, {"status": "locked_before_fit_and_new_validation_predictions", "locked_at_utc": datetime.now(UTC).isoformat(),
                           "protocol_sha256": protocol_hash, "protocol": protocol, "protected_sha256": protected})
    protected[str(lock_path)] = file_hash(lock_path)
    try:
        write_json(config.artifact_dir / "raw_feature_schema.json", data.schema)
        write_json(config.artifact_dir / "label_map.json", data.label_map)
        reference_probability = reference.predict_proba(data.validation.X)[:, 1]
        candidates, probabilities, trials = [], {}, []
        for item in experiment["candidates"]:
            verify_hashes(protected)
            summary, probability = fit_candidate(config.artifact_dir, item, data, matrices[item["id"]]["train"],
                                                matrices[item["id"]]["validation"], experiment["model_parameters"], lock_path)
            verify_hashes(protected)
            candidates.append(summary)
            probabilities[item["id"]] = probability
            for threshold in experiment["thresholds"]:
                trials.append(make_trial({"candidate_id": item["id"], "input_transform_id": item["input_transform_id"]},
                                         data.validation, probability, threshold, criteria))
            print(f"[보정 학습] {item['id']}: Train {len(data.train.y)}건, 입력 {len(summary['input_columns'])}개, 트리 {summary['trees_fitted']}개", flush=True)
        if len(trials) != experiment["maximum_trials"]:
            raise ValueError("실제 정책 조합 수가 사전 계획과 다릅니다.")
        comparison, predictions = compare(data, experiment["candidates"], probabilities, reference_probability,
                                          trials, criteria, prefixes["validation"])
        selected = choose_trial(trials, criteria)
        selection = {"status": "selected_pending_cost_and_test" if selected is not None else "no_feasible_candidate",
                     "selected_trial": selected, "feasible_trial_count": sum(trial["validation_feasible"] for trial in trials),
                     "cost_verified": False, "test_confirmed": False, "deployment_ready": False}
        write_json(config.artifact_dir / "selection.json", {
            **selection, "model_path": candidates[next(index for index, item in enumerate(candidates)
                          if item["id"] == selected["candidate_id"])]["model_path"] if selected else None,
            "protocol_sha256": protocol_hash, "positive_class": 1, "comparison": ">=",
            "raw_feature_schema_path": "raw_feature_schema.json", "label_map_path": "label_map.json",
            "input_transform_id": selected["input_transform_id"] if selected else None,
            "threshold": selected["threshold"] if selected else None,
            "model_input_schema_path": f"candidates/{selected['candidate_id']}/model_input_schema.json" if selected else None})
        csv_path = config.artifact_dir / "comparison_predictions.csv"
        predictions.to_csv(csv_path, index=False, encoding="utf-8", mode="x")
        verify_hashes(protected)
        report = {"report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
                  "experiment_id": config.experiment_id, "artifact_dir": str(config.artifact_dir), "dataset_id": data.dataset_id,
                  "train_rows": len(data.train.y), "validation_rows": len(data.validation.y),
                  "candidate_count": len(candidates), "trial_count": len(trials), "candidates": candidates, "trials": trials,
                  "protocol": protocol, "protocol_sha256": protocol_hash, "input_sha256": data.input_sha256,
                  "selection": selection, "comparison": comparison, "reference": reference_description,
                  "comparison_csv": {"path": str(csv_path), "sha256": file_hash(csv_path), "rows": len(predictions)},
                  "protected_sha256": protected, "fit_scope": "new_train_only", "refit_with_validation": False,
                  "test_files_read": False, "test_evaluated": False, "raw_records_read": False,
                  "cost_measured": False, "live_prefix_state_implemented": False,
                  "fastapi_still_dummy": True, "deployment_ready": False,
                  "limitations": [
                      "새 Validation에서 설정·threshold를 선택한 개발 결과이며 독립 Test 성능이 아니다.",
                      "입력 표현과 학습 데이터·타이밍·분포 변화가 함께 작용한다. 같은 0.3 재학습 대조와 선정 효과를 구분하며 인과관계를 단정하지 않는다.",
                      "평균 파싱 간격도 네트워크 도착 간격이 아니며 첫 메시지의 과거 정보를 복원하지 못한다.",
                      "본문 유효성·공격 의도를 관측하지 않는다. malformed 성능만으로 본문 검증을 학습했다고 주장하지 않는다.",
                      "Train 3개·Validation 1개 캠페인은 같은 생성기·장비다. 메시지는 독립 표본이 아니며 실제 외부 침입 일반화를 보장하지 않는다.",
                      "동일 입력 값의 반복과 상충 정답은 수집 품질 단계에서 보고했고 삭제하거나 정답을 바꾸지 않았다.",
                      "불순도 감소 중요도는 학습 분기의 통계이며 개별 오탐 원인이나 인과적 기여를 증명하지 않는다.",
                      "헤더 거부는 메시지 관측 밖이며 AI 메시지 탐지 성능에 합치지 않는다.",
                      "비용·온라인 prefix 상태·누락 감지·독립 Test 승인·FastAPI 모델 교체는 후속 작업이다.",
                  ]}
        for prefix, comparison_only in ((config.report_prefix, False), (config.comparison_report_prefix, True)):
            prefix.parent.mkdir(parents=True, exist_ok=True)
            write_json(prefix.with_suffix(".json"), report)
            with prefix.with_suffix(".md").open("x", encoding="utf-8") as stream:
                stream.write(render_report(report, comparison_only))
        verify_hashes(protected)
        inventory = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                     for path in sorted(config.artifact_dir.rglob("*")) if path.is_file()}
        write_json(config.artifact_dir / "experiment_manifest.json", {
            "status": "complete", "protocol_sha256": protocol_hash, "selection_status": selection["status"],
            "input_sha256": data.input_sha256, "artifacts": inventory,
            "reports": {str(path): file_hash(path) for path in outputs}, "test_evaluated": False, "deployment_ready": False})
        return report
    except BaseException as error:
        write_json(config.artifact_dir / "experiment_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="보정 RF 후보 4개를 새 Train만으로 학습하고 새 Validation에서 20개 정책을 비교합니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_recovery_experiment.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    parser.add_argument("--comparison-report-prefix", type=Path)
    args = parser.parse_args()
    values = load_config(args.config).model_dump()
    for name in ("artifact_dir", "report_prefix", "comparison_report_prefix"):
        if getattr(args, name) is not None:
            values[name] = getattr(args, name).resolve()
    report = run(RecoveryConfig.model_validate(values), args.config)
    selected = report["selection"]["selected_trial"]
    print(f"[보정 실험] 선정: {selected['candidate_id']} / threshold={selected['threshold']}" if selected else "[보정 실험] 제약 충족 후보 없음")
    print("[보정 실험] 새 Test·비용·배포 승인은 아직 수행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
