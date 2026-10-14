from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
for dependency in (".deps", ".deps_lstm"):
    if (AI_ROOT / dependency).exists():
        sys.path.insert(0, str(AI_ROOT / dependency))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_sequences import load_split, save_split, split_summary, validate_windows
from evaluation.compare_operational_augmentation import prefix_diagnostics
from evaluation.compare_operational_baselines import prediction_transitions
from evaluation.compare_operational_lstm import coverage_metrics
from evaluation.operational_final_data import expected_test_hashes, load_test, make_evaluation_windows, reserve_holdout, validate_evaluation_windows
from evaluation.operational_final_models import FrozenRuntime, freeze_models
from training.operational_data import load_operational_data
from training.run_operational_validation_experiment import CandidateId
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


MIB = 1024 * 1024


class ModelPolicy(StrictModel):
    id: CandidateId
    kind: Literal["logistic_regression", "random_forest", "lstm"]
    candidate_id: CandidateId
    threshold: Annotated[float, Field(gt=0, lt=1, strict=True)]


class Benchmark(StrictModel):
    process_repeats: Annotated[int, Field(ge=1, le=5, strict=True)]
    warmup_repeats: Annotated[int, Field(ge=1, le=100, strict=True)]
    single_request_repeats: Annotated[int, Field(ge=1, le=2000, strict=True)]
    batch_repeats: Annotated[int, Field(ge=1, le=30, strict=True)]
    sampling_seed: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)]
    cpu_threads: Literal[1]
    probability_absolute_tolerance: Annotated[float, Field(gt=0, le=0.0001, strict=True)]
    worker_timeout_seconds: Annotated[int, Field(ge=1, le=600, strict=True)]


class Criteria(StrictModel):
    max_false_positive_rate: Annotated[float, Field(ge=0, le=1, strict=True)]
    min_recall: Annotated[float, Field(ge=0, le=1, strict=True)]
    max_single_request_p95_ms: Annotated[float, Field(gt=0, strict=True)]
    max_process_peak_rss_mib: Annotated[float, Field(gt=0, strict=True)]
    max_deployment_artifacts_mib: Annotated[float, Field(gt=0, strict=True)]
    scope: Literal["local_advisory_integration_only"]


class EvaluationConfig(StrictModel):
    config_version: Literal["v1"]
    experiment_id: Identifier
    dataset_id: Identifier
    dataset_dir: Path
    sequence_dir: Path
    augmentation_report: Path
    lstm_report: Path
    augmentation_root: Path
    lstm_root: Path
    artifact_dir: Path
    holdout_registry_dir: Path
    report_prefix: Path
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)]
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)]
    expected_test_rows: Annotated[int, Field(ge=1, strict=True)]
    window_size: Annotated[int, Field(ge=1, le=64, strict=True)]
    models: Annotated[list[ModelPolicy], Field(min_length=1, max_length=3)]
    benchmark: Benchmark
    criteria: Criteria
    selection_rule: Literal["validation_recall_f1_fpr_then_cost_then_id"]
    test_rule: Literal["confirm_locked_candidate_without_model_or_threshold_reselection"]
    validation_prior_use: Literal["lr_rf_and_lstm_selection_completed"]

    @model_validator(mode="after")
    def validate_models(self) -> EvaluationConfig:
        if len({model.id for model in self.models}) != len(self.models) or len({model.kind for model in self.models}) != len(self.models):
            raise ValueError("최종 비교에는 모델 종류별 고정 정책 하나만 지정합니다.")
        return self


def load_config(path: Path) -> EvaluationConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "sequence_dir", "augmentation_report", "lstm_report", "augmentation_root", "lstm_root",
                 "artifact_dir", "holdout_registry_dir", "report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return EvaluationConfig.model_validate(values)


def digest(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def summarize_cost(runs: list[dict]) -> dict:
    if not runs or any(run["status"] != "complete" or run["input_split"] != "validation" or not run["single_batch_decisions_equal"] for run in runs):
        raise ValueError("완료된 같은 Validation 비용 측정이 필요합니다.")
    peaks = [run["memory"]["after_benchmark"]["native_peak_working_set_bytes"] for run in runs]
    required = {run["required_model_files_bytes"] for run in runs}
    if len(required) != 1:
        raise ValueError("비용 프로세스 간 모델 파일 크기가 다릅니다.")
    return {"process_runs": len(runs),
            "single_request_median_ms": float(np.median([run["single_request"]["features_to_decision"]["median_ms"] for run in runs])),
            "single_request_worst_process_p95_ms": max(run["single_request"]["features_to_decision"]["p95_ms"] for run in runs),
            "model_only_median_ms": float(np.median([run["single_request"]["model_only"]["median_ms"] for run in runs])),
            "batch_median_ms": float(np.median([run["batch"]["median_ms"] for run in runs])),
            "batch_amortized_per_message_median_ms": float(np.median([run["batch"]["amortized_per_message_median_ms"] for run in runs])),
            "maximum_native_process_peak_rss_bytes": max(peaks) if all(value is not None for value in peaks) else None,
            "required_model_files_bytes": required.pop(), "model_and_scaler_array_bytes": runs[0]["memory"]["model_and_scaler_array_bytes"],
            "memory_scope": "whole_isolated_process_including_framework_inputs_and_benchmark_caches"}


def benchmark_models(config: EvaluationConfig, descriptors: dict, data, windows, directory: Path) -> tuple[dict, dict]:
    directory.mkdir(parents=True, exist_ok=False)
    indices = np.random.default_rng(config.benchmark.sampling_seed).choice(len(data.validation.y),
        size=config.benchmark.single_request_repeats, replace=len(data.validation.y) < config.benchmark.single_request_repeats).tolist()
    costs, evidence = {}, {}
    for name, descriptor in descriptors.items():
        model_dir = directory / name
        model_dir.mkdir()
        input_path = model_dir / "validation_input.npz"
        arrays = {"X": windows.X, "lengths": windows.lengths} if descriptor["kind"] == "lstm" else {"X": data.validation.X.to_numpy()}
        with input_path.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
        job_path = model_dir / "job.json"
        write_json(job_path, {"descriptor": descriptor, "benchmark": config.benchmark.model_dump(), "input_path": str(input_path.resolve()),
                              "input_sha256": file_hash(input_path), "input_split": "validation", "sample_indices": indices})
        runs = []
        for repeat in range(config.benchmark.process_repeats):
            output = model_dir / f"process_{repeat}.json"
            command = [sys.executable, "-X", "utf8", str(AI_ROOT / "evaluation" / "operational_cost_worker.py"),
                       "--job", str(job_path), "--output", str(output)]
            # subprocess.run은 timeout 때 직접 생성한 worker를 종료하고 기다린다. 모델별 프로세스는 순차 실행한다.
            completed = subprocess.run(command, cwd=AI_ROOT, capture_output=True, text=True, encoding="utf-8",
                timeout=config.benchmark.worker_timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if completed.returncode != 0:
                raise ValueError(f"비용 worker 실패: {name}: {completed.stderr[-2000:]}")
            run = read_json(output)
            if run["descriptor"] != descriptor or run["sample_indices"] != indices or run["input_sha256"] != file_hash(input_path):
                raise ValueError("비용 worker의 고정 모델·요청 목록·입력이 다릅니다.")
            runs.append(run)
        costs[name] = {"summary": summarize_cost(runs), "process_records": runs}
        print(f"[최종 평가] {name}: 전처리 포함 단일 요청 중앙값 {costs[name]['summary']['single_request_median_ms']:.3f}ms", flush=True)
    evidence.update({str(path): file_hash(path) for path in directory.rglob("*") if path.is_file()})
    return costs, evidence


def rejection_reasons(metrics: dict, cost: dict, criteria: Criteria) -> list[str]:
    if any(not np.isfinite(metrics[name]) or not 0 <= metrics[name] <= 1 for name in ("false_positive_rate", "recall", "f1")):
        return ["선정 성능 지표가 유효하지 않음"]
    if (not np.isfinite(cost["single_request_worst_process_p95_ms"]) or cost["single_request_worst_process_p95_ms"] < 0 or
            not np.isfinite(cost["required_model_files_bytes"]) or cost["required_model_files_bytes"] < 0):
        return ["선정 비용 지표가 유효하지 않음"]
    reasons = []
    if metrics["false_positive_rate"] > criteria.max_false_positive_rate:
        reasons.append("정상 FPR 상한 초과")
    if metrics["recall"] < criteria.min_recall:
        reasons.append("이상 Recall 하한 미충족")
    if cost["single_request_worst_process_p95_ms"] > criteria.max_single_request_p95_ms:
        reasons.append("전처리 포함 단일 요청 p95 상한 초과")
    peak = cost["maximum_native_process_peak_rss_bytes"]
    if peak is None or not np.isfinite(peak) or peak <= 0 or peak > criteria.max_process_peak_rss_mib * MIB:
        reasons.append("네이티브 프로세스 peak RSS 미측정 또는 상한 초과")
    if cost["required_model_files_bytes"] > criteria.max_deployment_artifacts_mib * MIB:
        reasons.append("필수 모델·전처리·명세 파일 합계 상한 초과")
    return reasons


def select_before_test(descriptors: dict, costs: dict, criteria: Criteria) -> tuple[str | None, dict]:
    reasons = {name: rejection_reasons(model["validation_metrics"], costs[name]["summary"], criteria) for name, model in descriptors.items()}
    eligible = [name for name in descriptors if not reasons[name]]
    if not eligible:
        return None, reasons
    chosen = min(eligible, key=lambda name: (-descriptors[name]["validation_metrics"]["recall"],
        -descriptors[name]["validation_metrics"]["f1"], descriptors[name]["validation_metrics"]["false_positive_rate"],
        costs[name]["summary"]["single_request_worst_process_p95_ms"], costs[name]["summary"]["maximum_native_process_peak_rss_bytes"],
        costs[name]["summary"]["required_model_files_bytes"], name))
    return chosen, reasons


def export_bundle(directory: Path, descriptor: dict, data, gate_hash: str) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    names = {"model": "model.pt" if descriptor["kind"] == "lstm" else "pipeline.joblib", "scaler": "scaler.joblib",
             "feature_schema": "feature_schema.json", "label_map": "label_map.json", "sequence_schema": "sequence_schema.json"}
    files = {}
    for role, source in descriptor["files"].items():
        destination = directory / names[role]
        shutil.copyfile(source["path"], destination)
        if file_hash(destination) != source["sha256"]:
            raise ValueError("배포 묶음 복사 해시가 원본 모델과 다릅니다.")
        files[role] = {"path": destination.name, "sha256": file_hash(destination), "bytes": destination.stat().st_size}
    write_json(directory / "decision_policy.json", {"positive_class": 1, "threshold": descriptor["threshold"], "comparison": ">=",
        "selection": "locked_validation_and_cost_policy_confirmed_on_test", "automatic_blocking_allowed": False})
    files["decision_policy"] = {"path": "decision_policy.json", "sha256": file_hash(directory / "decision_policy.json"),
                                "bytes": (directory / "decision_policy.json").stat().st_size}
    manifest = {"manifest_version": "v1", "status": "approved_for_local_advisory_integration", "model_id": descriptor["id"],
                "kind": descriptor["kind"], "candidate_id": descriptor["candidate_id"], "threshold": descriptor["threshold"],
                "positive_class": 1, "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
                "architecture": descriptor["architecture"], "window_size": descriptor["window_size"] if descriptor["kind"] == "lstm" else None,
                "files": files, "dataset_id": data.dataset_id, "candidate_lock_sha256": gate_hash,
                "local_integration_approved": True, "production_ready": False, "automatic_blocking_allowed": False}
    write_json(directory / "deployment_manifest.json", manifest)
    return {"directory": str(directory.resolve()), "manifest_sha256": file_hash(directory / "deployment_manifest.json"),
            "total_directory_bytes": sum(path.stat().st_size for path in directory.iterdir() if path.is_file()), "manifest": manifest}


def render_report(report: dict) -> str:
    lines = ["# 운영 모델 최종 성능·비용 평가", "", "## 고정 정책과 Test 접근", "",
             "기존 Validation 선정 LR/RF/LSTM 정책을 재검증하고 Validation 입력으로 비용을 측정했다. 학습·threshold 탐색은 하지 않았다.",
             f"Test 접근 전 고정 후보: `{report['selected_model_id']}`. Test에서 다른 모델로 재선정하지 않는다.",
             "승인은 로컬 advisory 연동 한정이며 실제 침입 탐지·운영 배포·자동 차단을 보장하지 않는다.", "",
             "## 비용 측정", "", "| 모델 | 전처리 포함 단일 중앙값 ms | 가장 높은 프로세스 p95 ms | 모델 단독 중앙값 ms | peak RSS MiB | 필수 파일 KiB |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, cost in report["costs"].items():
        values = cost["summary"]
        peak = values["maximum_native_process_peak_rss_bytes"]
        rss = f"{peak / MIB:.2f}" if peak is not None else "측정 불가"
        lines.append(f"| {name} | {values['single_request_median_ms']:.4f} | {values['single_request_worst_process_p95_ms']:.4f} | {values['model_only_median_ms']:.4f} | {rss} | {values['required_model_files_bytes'] / 1024:.2f} |")
    lines += ["", "모델별 별도 프로세스·같은 Validation 행 목록·CPU 계산 스레드 1·워밍업·반복 수를 고정했다. 모델 단독과 전처리 포함 경로를 나눴다.",
              "RSS는 라이브러리·입력·측정 캐시를 포함하는 프로세스 전체다. 주요 배열 크기·필수 파일 저장량과 구분한다.",
              "JSON·HTTP·큐·feature 검증·세션 history 관리는 지연시간에서 제외했다. 배치 시간/행 수는 처리량 지표이지 단일 요청 시간은 아니다.", "",
              "## 고정 Validation 결과", "", "| 모델 | FP | FN | FPR | Recall | F1 | 선정 전 미충족 사유 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for name, descriptor in report["models"].items():
        values, matrix = descriptor["validation_metrics"], descriptor["validation_metrics"]["confusion_matrix"]
        lines.append(f"| {name} | {matrix['fp']} | {matrix['fn']} | {values['false_positive_rate']:.2%} | {values['recall']:.2%} | {values['f1']:.2%} | {'; '.join(report['pre_test_rejections'][name]) or '없음'} |")
    if report["test_evaluated"]:
        lines += ["", "## 고정 Test 결과", "", "| 모델 | FP | FN | FPR | Recall | F1 | ROC-AUC |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name, result in report["test_results"].items():
            values, matrix = result["metrics"], result["metrics"]["confusion_matrix"]
            lines.append(f"| {name} | {matrix['fp']} | {matrix['fn']} | {values['false_positive_rate']:.2%} | {values['recall']:.2%} | {values['f1']:.2%} | {values['roc_auc']:.6f} |")
        lines += ["", "## 잠근 후보의 시나리오별 Test 결과", "", "| 시나리오 | 메시지 | FP | FN | Recall | FPR |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for name, values in report["test_results"][report["selected_model_id"]]["by_scenario"].items():
            recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
            fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
            lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
        lines += ["", f"Test 창과 정답을 원본에서 재검증했다. 이전 Test 사용 여부: `{report['holdout_previously_reserved']}`."]
    lines += ["", "## 승인 결과", "", f"상태: `{report['approval']['status']}`.",
              f"미충족 사유: {'; '.join(report['approval']['reasons']) or '없음'}.",
              "실패하면 다른 Test 최고 모델로 교체하지 않고 배포 묶음을 보류한다.", "", "## 한계", ""]
    lines += [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 다음 작업", "", "승인된 고정 묶음을 FastAPI에서 검증·로딩해 실제 추론으로 연결한다. 승인 보류 시 새 독립 평가를 포함한 별도 개발 계획이 먼저 필요하다.", ""]
    return "\n".join(lines)


def run_evaluation(config: EvaluationConfig) -> dict:
    config = EvaluationConfig.model_validate(config.model_dump())
    report_paths = [config.report_prefix.with_suffix(suffix) for suffix in (".json", ".md", ".predictions.csv")]
    if config.artifact_dir.exists() or any(path.exists() for path in report_paths):
        raise FileExistsError("기존 최종 평가는 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    source_roots = (config.dataset_dir, config.sequence_dir, config.augmentation_root, config.lstm_root)
    if any(path.resolve().is_relative_to(root.resolve()) for path in (*report_paths, config.artifact_dir, config.holdout_registry_dir) for root in source_roots):
        raise ValueError("최종 결과·holdout 기록을 기존 데이터나 모델 내부에 저장할 수 없습니다.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    if len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows:
        raise ValueError("최종 평가의 고정 Train/Validation 행 수가 다릅니다.")
    sequence_manifest = read_json(config.sequence_dir / "sequence_manifest.json")
    if sequence_manifest["status"] != "complete" or sequence_manifest["source_input_sha256"] != data.input_sha256 or sequence_manifest["window_size"] != config.window_size:
        raise ValueError("최종 비교의 시계열 고정 원본·창 설정이 다릅니다.")
    sequence_evidence = {str(config.sequence_dir / name): value["sha256"] for name, value in sequence_manifest["output_files"].items()}
    sequence_evidence[str(config.sequence_dir / "sequence_manifest.json")] = file_hash(config.sequence_dir / "sequence_manifest.json")
    verify_hashes(sequence_evidence)
    windows = load_split(config.sequence_dir, "validation")
    validate_windows(windows, data.validation, config.window_size, sequence_manifest["dataset_id"])
    # 최종 평가용 별도 구현이 학습 때의 창과 완전히 같은지 Test 공개 전에 대조한다.
    validate_evaluation_windows(windows, data.validation, config.window_size, sequence_manifest["dataset_id"])
    descriptors, runtimes, model_evidence = freeze_models(config, data, windows)
    protected = {str(config.dataset_dir / name): value for name, value in data.input_sha256.items()}
    protected.update(sequence_evidence)
    protected.update(model_evidence)
    code_paths = [Path(__file__), AI_ROOT / "evaluation" / "operational_final_data.py", AI_ROOT / "evaluation" / "operational_final_models.py",
                  AI_ROOT / "evaluation" / "operational_cost_worker.py", AI_ROOT / "requirements-evaluation.txt"]
    protected.update({str(path): file_hash(path) for path in code_paths})
    expected_test = expected_test_hashes(config.dataset_dir)
    snapshot = config.model_dump(mode="json")
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    protocol_path = config.artifact_dir / "protocol_lock.json"
    write_json(protocol_path, {"status": "locked_before_cost_and_test", "locked_at_utc": datetime.now(UTC).isoformat(),
        "protocol": snapshot, "protocol_sha256": digest(snapshot), "models": descriptors, "protected_sha256": protected,
        "expected_test_sha256": expected_test, "test_files_read": False, "model_training_performed": False})
    protected[str(protocol_path)] = file_hash(protocol_path)
    costs, cost_evidence = benchmark_models(config, descriptors, data, windows, config.artifact_dir / "benchmark")
    protected.update(cost_evidence)
    verify_hashes(protected)
    selected, reasons = select_before_test(descriptors, costs, config.criteria)
    test_results, test_input, bundle, window_summary, holdout_path = {}, {}, None, None, None
    holdout_used = False
    gate_hash = None
    if selected is not None:
        policy_signature = digest({"models": descriptors, "criteria": config.criteria.model_dump(), "selection_rule": config.selection_rule,
                                   "test_rule": config.test_rule, "selected_model_id": selected})
        gate_path = config.artifact_dir / "candidate_lock.json"
        write_json(gate_path, {"status": "candidate_locked_before_test", "locked_at_utc": datetime.now(UTC).isoformat(),
            "selected_model_id": selected, "models": descriptors, "selection_scope": "validation_and_cost_only",
            "criteria": config.criteria.model_dump(), "policy_signature": policy_signature, "expected_test_sha256": expected_test,
            "pre_test_protected_sha256": protected, "test_files_read": False, "protocol_sha256": digest(snapshot)})
        gate_hash = file_hash(gate_path)
        protected[str(gate_path)] = gate_hash
        holdout_path, holdout_used = reserve_holdout(config.holdout_registry_dir, gate_path, gate_hash)
        protected[str(holdout_path)] = file_hash(holdout_path)
        test, test_input = load_test(config.dataset_dir, data, gate_path, gate_hash, holdout_path)
        if len(test.y) != config.expected_test_rows:
            raise ValueError("Test 행 수가 사전 최종 계획과 다릅니다.")
        protected.update({str(config.dataset_dir / name): value for name, value in test_input.items()})
        test_id = config.experiment_id + "_test_sequence"
        test_windows = make_evaluation_windows(test, config.window_size, test_id)
        test_directory = config.artifact_dir / "test_windows"
        test_directory.mkdir()
        save_split(test_directory, "test", test_windows)
        with np.load(test_directory / "test.npz", allow_pickle=False) as archive:
            from data_preparation.operational_sequences import SequenceSplit
            restored = SequenceSplit(**{name: archive[name] for name in archive.files}, metadata=pd.read_csv(test_directory / "metadata_test.csv", dtype=str, keep_default_na=False))
        validate_evaluation_windows(restored, test, config.window_size, test_id)
        window_summary = split_summary(test_windows)
        predictions = test.metadata.copy()
        predictions["label"], predictions["context_length"] = test.y, test_windows.lengths
        for feature in FEATURE_NAMES:
            predictions[feature] = test.X[feature].to_numpy()
        for name, runtime in runtimes.items():
            probability = runtime.predict_raw(test_windows.X, test_windows.lengths) if runtime.kind == "lstm" else runtime.predict_raw(test.X.to_numpy())
            prediction = (probability >= runtime.threshold).astype("int64")
            # prefix 함수는 validation 필드에 전달한 평가 split만 사용한다. 원본 Test metadata 자체는 바꾸지 않는다.
            from training.operational_data import OperationalData
            evaluation_context = OperationalData(data.dataset_id, data.schema, data.label_map, data.train, test, data.input_sha256)
            test_results[name] = {"metrics": calculate_metrics(test.y, probability, runtime.threshold), "by_scenario": scenario_metrics(test, prediction),
                                 "coverage": coverage_metrics(test.y, prediction, test_windows.lengths, config.window_size),
                                 "prefix": prefix_diagnostics(evaluation_context, probability, runtime.threshold)}
            predictions[f"{name}__probability"] = probability
            predictions[f"{name}__prediction"] = prediction
            predictions[f"{name}__threshold"] = runtime.threshold
        locked_prediction = predictions[f"{selected}__prediction"].to_numpy()
        for name in test_results:
            test_results[name]["transitions_from_locked_candidate"] = prediction_transitions(test.y, locked_prediction, predictions[f"{name}__prediction"].to_numpy())
        approval_reasons = rejection_reasons(test_results[selected]["metrics"], costs[selected]["summary"], config.criteria)
        if not approval_reasons:
            bundle = export_bundle(config.artifact_dir / "deployment_bundle", descriptors[selected], data, gate_hash)
        report_paths[2].parent.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(report_paths[2], index=False, encoding="utf-8", mode="x")
        approval_status = "approved_for_local_advisory_integration" if not approval_reasons else "withheld_after_test"
    else:
        approval_reasons, approval_status = ["Validation·비용 사전 기준을 충족한 후보 없음"], "withheld_before_test"
    verify_hashes(protected)
    report = {"report_version": "v1", "status": "complete" if selected is not None else "incomplete_no_pre_test_candidate",
              "generated_at_utc": datetime.now(UTC).isoformat(), "experiment_id": config.experiment_id, "protocol": snapshot,
              "protocol_sha256": digest(snapshot), "models": descriptors, "costs": costs, "selected_model_id": selected,
              "pre_test_rejections": reasons, "selection_used_test": False, "thresholds_changed": False,
              "candidate_lock_sha256": gate_hash, "test_evaluated": selected is not None, "test_input_sha256": test_input,
              "test_results": test_results, "test_windows": window_summary, "holdout_receipt_path": str(holdout_path) if holdout_path else None,
              "holdout_previously_reserved": holdout_used, "protected_sha256": protected,
              "approval": {"status": approval_status, "reasons": approval_reasons, "production_ready": False, "automatic_blocking_allowed": False},
              "deployment_bundle": bundle, "model_training_performed": False, "fastapi_still_dummy": True,
              "limitations": [
                  "통제된 애플리케이션 메시지 이상이며 실제 침입·공개 UNSW 성능과 혼합하지 않는다.",
                  "작은 독립 캠페인 수·동일 생성기·단일 seed·겹친 창의 상관과 본문 정보 부재가 남는다.",
                  "Test는 고정 정책의 확인과 진단에 사용했다. 결과로 모델·threshold·승인 조건을 재선정하지 않는다.",
                  "비용은 현재 Windows/CPU 환경의 Validation 기반 오프라인 측정이며 API·TCP 종단 부하 성능이 아니다.",
                  "peak RSS는 프로세스 전체이며 모델 단독 메모리나 단일 forward의 증분 peak가 아니다. USS·주요 배열·파일 크기도 별도로 기록한다.",
                  "성능·비용 상한은 사전 로컬 MVP 기준일 뿐 실제 운영 SLA·안전 보장이 아니다. 자동 차단은 승인하지 않는다.",
                  "같은 holdout 재실행은 사용 기록에 표시한다. 바뀐 정책에는 새 독립 평가가 필요하다.",
              ]}
    report_paths[0].parent.mkdir(parents=True, exist_ok=True)
    write_json(report_paths[0], report)
    with report_paths[1].open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    inventory = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                 for path in config.artifact_dir.rglob("*") if path.is_file()}
    write_json(config.artifact_dir / "evaluation_manifest.json", {"status": report["status"], "approval_status": approval_status,
        "selected_model_id": selected, "candidate_lock_sha256": gate_hash, "artifacts": inventory,
        "reports": {str(path): file_hash(path) for path in report_paths if path.exists()}, "test_evaluated": selected is not None,
        "model_training_performed": False, "production_ready": False})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="고정 운영 모델의 Validation 비용·사전 후보 잠금·최종 Test 평가입니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_final_evaluation.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        for name in ("artifact_dir", "report_prefix"):
            if getattr(args, name) is not None:
                values[name] = getattr(args, name).resolve()
        report = run_evaluation(EvaluationConfig.model_validate(values))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"[최종 평가] 실패: {error}", file=sys.stderr)
        return 2
    print(f"[최종 평가] Test 전 고정 후보: {report['selected_model_id']} / 승인: {report['approval']['status']} / 재학습·Test 재선정 없음")
    return 0 if report["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
