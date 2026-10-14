from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
for dependency in (".deps", ".deps_lstm"):
    if (AI_ROOT / dependency).exists():
        sys.path.insert(0, str(AI_ROOT / dependency))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
import torch
from pydantic import Field, model_validator
from sklearn.preprocessing import StandardScaler

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import inside_path, read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_sequences import GROUP_COLUMNS, SEQUENCE_CONTRACT, load_split, validate_partitions, validate_windows
from evaluation.compare_operational_baselines import load_verified_model
from evaluation.compare_operational_lstm import compare, render_report
from training.operational_data import load_operational_data
from training.operational_lstm import cpu_runtime, fit_candidate, transform_windows
from training.run_operational_validation_experiment import CandidateId, SelectionRule, choose_trial, eligibility_reasons
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


class LstmCandidate(StrictModel):
    id: CandidateId
    hidden_size: Annotated[int, Field(ge=1, le=128, strict=True)]
    num_layers: Literal[1]
    dropout: Literal[0.0]
    bidirectional: Literal[False]


class SequenceProcessing(StrictModel):
    valid_steps_only: Literal[True]
    readout: Literal["last_valid_timestep"]
    hidden_state_policy: Literal["reset_to_zero_for_each_window"]
    output_head: Literal["linear_hidden_to_one_logit"]


class Preprocessing(StrictModel):
    kind: Literal["StandardScaler"]
    fit_scope: Literal["unique_original_train_rows_only"]
    transform_scope: Literal["valid_steps_only_then_zero_pad"]
    tensor_dtype: Literal["float32"]
    missing_value_policy: Literal["reject_without_zero_fill"]


class Training(StrictModel):
    max_epochs: Annotated[int, Field(ge=1, le=30, strict=True)]
    batch_size: Annotated[int, Field(ge=1, le=1024, strict=True)]
    optimizer: Literal["Adam"]
    learning_rate: Annotated[float, Field(gt=0, le=1, strict=True)]
    weight_decay: Annotated[float, Field(ge=0, le=1, strict=True)]
    loss: Literal["BCEWithLogitsLoss"]
    positive_class_weight: Literal[1.0]
    shuffle_train_windows: Literal[True]
    checkpoint_rule: Literal["minimum_validation_loss"]
    early_stopping_patience: Annotated[int, Field(ge=1, le=30, strict=True)]
    early_stopping_min_delta: Literal[0.0]
    refit_with_validation: Literal[False]


class LstmPlan(StrictModel):
    plan_version: Literal["v1"]
    status: Literal["design_only_not_trained"]
    scope: str
    sequence_dataset_id: Identifier
    sequence_dataset_dir: Path
    base_dataset_id: Identifier
    base_dataset_dir: Path
    reference_selection: Path
    reference_comparison: Path
    framework: Literal["pytorch"]
    device: Literal["cpu"]
    cpu_threads: Literal[1]
    random_seed: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)]
    input_features: Literal[7]
    window_size: Annotated[int, Field(ge=1, le=64, strict=True)]
    padding_side: Literal["right"]
    target_policy: Literal["current_message_label"]
    sequence_processing: SequenceProcessing
    preprocessing: Preprocessing
    candidates: Annotated[list[LstmCandidate], Field(min_length=2, max_length=2)]
    training: Training
    thresholds: Annotated[list[Annotated[float, Field(gt=0, lt=1, strict=True)]], Field(min_length=1, max_length=15)]
    selection_rule: SelectionRule
    validation_prior_use: Literal["baseline_audit_and_lr_rf_selection_completed"]
    test_access_allowed: Literal[False]
    test_model_evaluated: Literal[False]
    model_training_performed: Literal[False]
    limitations: list[str]

    @model_validator(mode="after")
    def validate_candidates(self) -> LstmPlan:
        if (len({candidate.id for candidate in self.candidates}) != 2 or
                len({candidate.hidden_size for candidate in self.candidates}) != 2 or
                self.thresholds != sorted(set(self.thresholds)) or 0.5 not in self.thresholds):
            raise ValueError("두 후보의 id·hidden 크기는 서로 달라야 하며 threshold는 중복 없이 기본 0.5를 포함해야 합니다.")
        return self


class ExperimentConfig(StrictModel):
    config_version: Literal["v1"]
    experiment_id: Identifier
    plan_path: Path
    artifact_dir: Path
    report_prefix: Path
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)]
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)]


def load_config(path: Path) -> ExperimentConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("plan_path", "artifact_dir", "report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return ExperimentConfig.model_validate(values)


def load_plan(path: Path) -> LstmPlan:
    values = read_json(path)
    for name in ("sequence_dataset_dir", "base_dataset_dir", "reference_selection", "reference_comparison"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return LstmPlan.model_validate(values)


def load_sequence_data(plan: LstmPlan, data) -> tuple[dict, dict, dict]:
    root = plan.sequence_dataset_dir
    manifest = read_json(root / "sequence_manifest.json")
    known = {"train.npz", "validation.npz", "metadata_train.csv", "metadata_validation.csv",
             "sequence_schema.json", "window_protocol_lock.json"}
    if (manifest.get("status") != "complete" or manifest.get("dataset_id") != plan.sequence_dataset_id or
            manifest.get("source_dataset_id") != data.dataset_id or manifest.get("source_input_sha256") != data.input_sha256 or
            manifest.get("window_size") != plan.window_size or manifest.get("test_files_read") is not False or
            manifest.get("test_windows_generated") is not False or manifest.get("preprocessing_fitted") is not False or
            set(manifest["output_files"]) != known):
        raise ValueError("시계열 완료 상태·원본·창 설정·Train/Validation 파일 구성이 다릅니다.")
    evidence = {str(root / "sequence_manifest.json"): file_hash(root / "sequence_manifest.json")}
    evidence.update({str(root / name): manifest["output_files"][name]["sha256"] for name in known})
    verify_hashes(evidence)
    schema = read_json(root / "sequence_schema.json")
    if (schema.get("sequence_contract_id") != SEQUENCE_CONTRACT or schema.get("base_feature_contract_id") != data.schema["feature_contract_id"] or
            schema.get("dataset_id") != plan.sequence_dataset_id or schema.get("base_dataset_id") != data.dataset_id or
            schema.get("feature_order") != FEATURE_NAMES or schema.get("feature_units") != data.schema["feature_units"] or
            schema.get("input_shape") != ["batch", plan.window_size, 7] or schema.get("input_dtype") != "float64_raw_not_scaled" or
            schema.get("padding") != {"side": "right", "raw_value": 0.0, "mask_valid": True, "mask_padding": False,
                                      "source_position_padding": -1, "minimum_valid_length": 1} or
            schema.get("group_columns") != GROUP_COLUMNS or schema.get("time_order") != "request_index_ascending_contiguous_from_1" or
            schema.get("anchor_alignment") != "original_message_row_order" or
            schema.get("stride") != 1 or schema.get("include_current_message") is not True or
            schema.get("history_direction") != "past_and_current_only" or schema.get("target_policy") != "current_message_label" or
            schema.get("preprocessing") != {"status": "not_fitted", "future_fit_scope": "unique_original_train_rows_only_not_flattened_windows",
                                            "future_transform_scope": "valid_steps_only_then_zero_pad", "tensor_dtype_after_transform": "float32"} or
            schema.get("test_files_read") is not False or schema.get("test_windows_generated") is not False):
        raise ValueError("시계열 입력 계약이 LSTM 계획과 다릅니다.")
    splits = {name: load_split(root, name) for name in ("train", "validation")}
    for name, windows in splits.items():
        validate_windows(windows, getattr(data, name), plan.window_size, plan.sequence_dataset_id)
    validate_partitions(splits["train"], splits["validation"])
    return splits, schema, evidence


def load_reference(plan: LstmPlan, data) -> tuple[np.ndarray, dict, dict]:
    root = plan.reference_selection.parent
    manifest_path = root / "experiment_manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("status") != "complete" or manifest.get("input_sha256") != data.input_sha256:
        raise ValueError("기준 RF 실험과 현재 고정 데이터셋이 다릅니다.")
    evidence = {str(manifest_path): file_hash(manifest_path), str(plan.reference_comparison): file_hash(plan.reference_comparison)}
    evidence.update({str(inside_path(root, name)): entry["sha256"] for name, entry in manifest["artifacts"].items()})
    evidence.update(manifest["reports"])
    verify_hashes(evidence)
    selection = read_json(plan.reference_selection)
    selected = selection["selected_trial"]
    if (selection.get("status") != "selected" or not selected or selected["kind"] != "random_forest" or
            selection.get("rule") != plan.selection_rule.model_dump() or selection.get("threshold") != selected["threshold"] or
            selection.get("feature_order") != FEATURE_NAMES or selection.get("label_map") != data.label_map or
            selection.get("test_evaluated") is not False):
        raise ValueError("기준 모델은 동일 정답·선정 규칙의 보강 RF여야 합니다.")
    model_path = inside_path(root, selection["model_path"])
    if file_hash(model_path) != selection["model_sha256"]:
        raise ValueError("선정 RF 모델 해시가 다릅니다.")
    pipeline, report, model_evidence, saved = load_verified_model(model_path.parent, "rf", data)
    probability = pipeline.predict_proba(data.validation.X)[:, 1]
    metrics = calculate_metrics(data.validation.y, probability, selected["threshold"])
    comparison = read_json(plan.reference_comparison)
    previous = comparison["comparison"]["models"]["best_rf_validation"]
    if (metrics != selected["metrics"] or comparison["selection"]["selected_trial"] != selected or
            previous["candidate_id"] != selected["candidate_id"] or previous["threshold"] != selected["threshold"] or
            previous["model_sha256"] != selection["model_sha256"] or previous["validation_metrics"] != metrics or
            calculate_metrics(data.validation.y, probability, 0.5) != report["validation_metrics"] or
            saved.row_id.tolist() != data.validation.metadata.row_id.tolist() or
            not np.array_equal(saved.label.to_numpy(), data.validation.y) or
            not np.allclose(saved.anomaly_probability.to_numpy(), probability, rtol=0, atol=1e-14)):
        raise ValueError("선정 RF의 재예측·기존 비교·원본 행 정렬이 다릅니다.")
    return probability, {"candidate_id": selected["candidate_id"], "threshold": selected["threshold"],
                         "model_sha256": selection["model_sha256"], "validation_metrics": metrics,
                         "verified_model": model_evidence}, evidence


def run_experiment(config: ExperimentConfig) -> dict:
    config = ExperimentConfig.model_validate(config.model_dump())
    report_json, report_md = (config.report_prefix.with_suffix(suffix) for suffix in (".json", ".md"))
    predictions_path = config.report_prefix.with_suffix(".predictions.csv")
    if config.artifact_dir.exists() or any(path.exists() for path in (report_json, report_md, predictions_path)):
        raise FileExistsError("기존 LSTM 실험은 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    plan = load_plan(config.plan_path)
    protected_roots = [plan.base_dataset_dir, plan.sequence_dataset_dir, plan.reference_selection.parent]
    if any(path.resolve().is_relative_to(root.resolve()) for path in (config.artifact_dir, report_json, report_md, predictions_path)
           for root in protected_roots):
        raise ValueError("고정 원본·시계열 데이터·기존 RF 산출물 내부에 출력할 수 없습니다.")
    data = load_operational_data(plan.base_dataset_dir, plan.base_dataset_id)
    if len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows:
        raise ValueError("LSTM 계획의 Train/Validation 행 수가 다릅니다.")
    windows, raw_schema, sequence_evidence = load_sequence_data(plan, data)
    rf_probability, reference, reference_evidence = load_reference(plan, data)
    protected = {str(plan.base_dataset_dir / name): digest for name, digest in data.input_sha256.items()}
    protected.update(sequence_evidence)
    protected.update(reference_evidence)
    protected[str(config.plan_path)] = file_hash(config.plan_path)
    code_paths = [AI_ROOT / "training" / name for name in ("operational_lstm.py", "run_operational_lstm_experiment.py", "operational_data.py",
                                                          "train_operational_logistic_regression.py", "run_operational_validation_experiment.py")]
    code_paths += [AI_ROOT / "evaluation" / name for name in ("compare_operational_lstm.py", "compare_operational_baselines.py", "compare_operational_augmentation.py")]
    code_paths += [AI_ROOT / "data_preparation" / "operational_sequences.py", AI_ROOT / "requirements-lstm.txt"]
    code_hashes = {str(path.relative_to(AI_ROOT)): file_hash(path) for path in code_paths}
    protected.update({str(AI_ROOT / name): digest for name, digest in code_hashes.items()})
    snapshot = {"execution_config": config.model_dump(mode="json"),
                "execution_plan": plan.model_dump(mode="json", exclude={"status", "scope", "limitations", "model_training_performed", "test_model_evaluated"}),
                "source_plan_is_previous_design_record": True, "fit_scope": "original_train_only",
                "selection_scope": "lstm_candidates_on_validation_only", "test_evaluated": False}
    protocol_hash = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    lock_path = config.artifact_dir / "protocol_lock.json"
    with cpu_runtime(plan.random_seed, plan.cpu_threads):
        environment = {"python": platform.python_version(), "platform": platform.platform(), "pytorch": str(torch.__version__),
                       "cuda_build": torch.version.cuda, "device": "cpu", "cpu_intraop_threads": torch.get_num_threads(),
                       "cpu_interop_threads": torch.get_num_interop_threads(), "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
                       "scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__}
        # scaler fit과 첫 gradient 갱신 전에 실제 계획·입력·기준 모델·코드·환경을 잠근다.
        write_json(lock_path, {"status": "locked_before_scaler_and_model_fit", "locked_at_utc": datetime.now(UTC).isoformat(),
                               "protocol_sha256": protocol_hash, "protocol": snapshot, "protected_sha256": protected, "environment": environment})
        protected[str(lock_path)] = file_hash(lock_path)
        scaler = StandardScaler().fit(data.train.X)
        if int(scaler.n_samples_seen_) != len(data.train.y):
            raise ValueError("scaler가 원본 Train 행 수와 다른 표본으로 학습됐습니다.")
        scaler_path = config.artifact_dir / "scaler.joblib"
        joblib.dump(scaler, scaler_path, compress=3)
        transformed = {name: transform_windows(scaler, value) for name, value in windows.items()}
        reloaded_scaler = joblib.load(scaler_path)
        for name in windows:
            if not np.array_equal(transformed[name], transform_windows(reloaded_scaler, windows[name])):
                raise ValueError("저장 scaler 재로딩의 입력 변환이 다릅니다.")
        fitted_schema = {**raw_schema, "input_dtype": "float32_scaled_valid_steps_only",
                         "preprocessing": {"status": "fitted", **plan.preprocessing.model_dump(), "stored_in": "scaler.joblib",
                                           "samples_seen": len(data.train.y)}}
        write_json(config.artifact_dir / "sequence_schema.json", fitted_schema)
        write_json(config.artifact_dir / "feature_schema.json", data.schema)
        write_json(config.artifact_dir / "label_map.json", data.label_map)
        candidates, trials, probabilities = [], [], {}
        for candidate in plan.candidates:
            specification = candidate.model_dump()
            candidate_dir = config.artifact_dir / "candidates" / candidate.id
            training, train_probability, probability = fit_candidate(specification, plan.training.model_dump(), plan.random_seed,
                                                                     candidate_dir, transformed["train"], windows["train"],
                                                                     transformed["validation"], windows["validation"])
            probabilities[candidate.id] = probability
            candidate_trials = []
            for threshold in plan.thresholds:
                metrics = calculate_metrics(data.validation.y, probability, threshold)
                reasons = eligibility_reasons(metrics, True, plan.selection_rule)
                candidate_trials.append({"candidate_id": candidate.id, "kind": "lstm", "threshold": threshold,
                                         "training_acceptable": True, "eligible": not reasons, "rejection_reasons": reasons,
                                         "metrics": metrics, "validation_by_scenario": scenario_metrics(data.validation, (probability >= threshold).astype("int64"))})
            trials.extend(candidate_trials)
            candidate_report = {"id": candidate.id, "specification": specification, "training": training,
                                "train_metrics": calculate_metrics(data.train.y, train_probability, 0.5),
                                "default_validation_metrics": calculate_metrics(data.validation.y, probability, 0.5),
                                "best_feasible_trial": choose_trial(candidate_trials, plan.selection_rule),
                                "model_path": f"candidates/{candidate.id}/model.pt", "model_sha256": file_hash(candidate_dir / "model.pt")}
            candidates.append(candidate_report)
            write_json(candidate_dir / "candidate_report.json", candidate_report)
            prediction = windows["validation"].metadata.copy()
            prediction["label"], prediction["anomaly_probability"] = data.validation.y, probability
            prediction["predicted_label"] = (probability >= 0.5).astype("int64")
            prediction["threshold"] = 0.5
            prediction.to_csv(candidate_dir / "validation_predictions.csv", index=False, encoding="utf-8", mode="x")
    selected = choose_trial(trials, plan.selection_rule)
    selection = {"status": "selected" if selected else "no_feasible_candidate", "selected_trial": selected,
                 "rule": plan.selection_rule.model_dump(), "eligible_trial_count": sum(trial["eligible"] for trial in trials),
                 "selection_scope": "lstm_validation_only_not_final_deployment", "threshold": selected["threshold"] if selected else None,
                 "positive_class": 1, "comparison": ">=", "model_path": None, "model_sha256": None,
                 "scaler_path": "scaler.joblib", "scaler_sha256": file_hash(scaler_path),
                 "sequence_schema_path": "sequence_schema.json", "sequence_contract_id": SEQUENCE_CONTRACT,
                 "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
                 "label_map": data.label_map, "refit_with_validation": False, "test_evaluated": False,
                 "final_model_selected": False, "deployment_ready": False, "protocol_sha256": protocol_hash}
    if selected:
        relative = f"candidates/{selected['candidate_id']}/model.pt"
        selection.update(model_path=relative, model_sha256=file_hash(config.artifact_dir / relative))
        prediction = windows["validation"].metadata.copy()
        prediction["label"], prediction["anomaly_probability"] = data.validation.y, probabilities[selected["candidate_id"]]
        prediction["predicted_label"] = (probabilities[selected["candidate_id"]] >= selected["threshold"]).astype("int64")
        prediction["threshold"] = selected["threshold"]
        prediction.to_csv(config.artifact_dir / "selected_validation_predictions.csv", index=False, encoding="utf-8", mode="x")
    write_json(config.artifact_dir / "selection.json", selection)
    comparison, comparison_frame = compare(data, windows["validation"].lengths, plan.window_size, rf_probability,
                                          reference["threshold"], probabilities, candidates, selected)
    verify_hashes(protected)
    inventory = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                 for path in sorted(config.artifact_dir.rglob("*")) if path.is_file()}
    report = {"report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
              "experiment_id": config.experiment_id, "dataset_id": data.dataset_id, "sequence_dataset_id": plan.sequence_dataset_id,
              "train_rows": len(data.train.y), "validation_rows": len(data.validation.y), "window_size": plan.window_size,
              "feature_order": FEATURE_NAMES, "protocol": snapshot, "protocol_sha256": protocol_hash,
              "input_sha256": data.input_sha256, "protected_sha256": protected, "code_sha256": code_hashes, "environment": environment,
              "fit_scope": "unique_original_train_rows_only", "scaler": {"samples_seen": int(scaler.n_samples_seen_),
                  "mean": dict(zip(FEATURE_NAMES, scaler.mean_.tolist())), "variance": dict(zip(FEATURE_NAMES, scaler.var_.tolist())),
                  "scale": dict(zip(FEATURE_NAMES, scaler.scale_.tolist())), "reload_transform_equal": True},
              "candidates": candidates, "trials": trials, "selection": selection, "reference_rf": reference, "comparison": comparison,
              "artifacts": inventory, "artifact_dir": str(config.artifact_dir), "model_training_performed": True,
              "test_files_read": False, "test_evaluated": False, "refit_with_validation": False, "deployment_ready": False,
              "limitations": [
                  "Validation은 이미 LR/RF 진단·선정에 사용했고 이번에도 epoch·후보·threshold를 선택했다. 최고 점수는 독립 최종 성능이 아니다.",
                  "RF는 현재 메시지, LSTM은 최근 과거·현재 창을 사용한다. 같은 판단 대상·원시 입력 의미지만 사용 문맥과 모델이 함께 달라졌다.",
                  "겹친 창은 독립 표본이 아니다. 생성기 편향과 작은 캠페인 수, 단일 seed 결과의 한계가 남는다.",
                  "첫 메시지와 짧은 malformed 세션도 유지했다. 없는 과거나 본문 필드를 LSTM이 복원했다고 해석하지 않는다.",
                  "scaler와 가중치 갱신은 Train만 사용했다. Validation에서는 gradient 없이 손실·확률을 관측하고 최적 epoch를 선택했다.",
                  "LSTM 후보 내 선정은 기존 RF 교체나 최종 모델 확정이 아니다. 개선이 없으면 결과를 그대로 기록한다.",
                  "Test·추론 지연시간·메모리·실제 FastAPI 모델 연결은 이번 작업에서 수행하지 않았다.",
              ]}
    report_json.parent.mkdir(parents=True, exist_ok=True)
    comparison_frame.to_csv(predictions_path, index=False, encoding="utf-8", mode="x")
    write_json(report_json, report)
    with report_md.open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    # 미선정도 정상적인 실험 결과다. 완료와 최종 배포 승인을 구분한다.
    write_json(config.artifact_dir / "experiment_manifest.json", {"status": "complete", "experiment_id": config.experiment_id,
        "selection_status": selection["status"], "protocol_sha256": protocol_hash, "input_sha256": data.input_sha256,
        "artifacts": inventory, "reports": {str(path): file_hash(path) for path in (report_json, report_md, predictions_path)},
        "test_evaluated": False, "deployment_ready": False})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 LSTM Train 전용 학습·Validation 선정과 RF 비교. Test는 읽지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_lstm_experiment.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        for name in ("artifact_dir", "report_prefix"):
            if getattr(args, name) is not None:
                values[name] = getattr(args, name).resolve()
        report = run_experiment(ExperimentConfig.model_validate(values))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"[운영 LSTM] 실패: {error}", file=sys.stderr)
        return 2
    selected = report["selection"]["selected_trial"]
    if selected:
        metrics = selected["metrics"]
        print(f"[운영 LSTM] 선정 {selected['candidate_id']} / threshold={selected['threshold']} / FPR={metrics['false_positive_rate']:.6f}, Recall={metrics['recall']:.6f}, F1={metrics['f1']:.6f}")
    else:
        print("[운영 LSTM] 실험 완료 / 기준 충족 LSTM 없음 / 기준은 유지합니다.")
    print(f"[운영 LSTM] Train {report['train_rows']} / Validation {report['validation_rows']} / Test 미사용 / RF 자동 교체 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
