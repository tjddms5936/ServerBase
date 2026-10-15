from __future__ import annotations

import argparse
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
import sklearn
from pydantic import Field
from sklearn.ensemble import RandomForestClassifier

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import inside_path, read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.compare_operational_augmentation import prefix_diagnostics
from evaluation.compare_operational_recovery import make_trial, message_position_counts, rejection_reasons
from evaluation.operational_final_data import expected_test_hashes, load_test, reserve_holdout
from evaluation.operational_final_models import FrozenRuntime
from evaluation.run_operational_final_evaluation import digest
from evaluation.run_operational_recovery_cost import CostConfig, cost_rejections, validate_cost_runs, verify_without_holdout
from training.operational_data import load_operational_data
from training.run_operational_recovery_experiment import matrix_hash
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TestConfig(StrictModel):
    config_version: Literal["v1"]
    experiment_id: Identifier
    dataset_id: Identifier
    dataset_dir: Path
    cost_root: Path
    cost_report: Path
    expected_cost_manifest_sha256: Sha256
    expected_candidate_lock_sha256: Sha256
    artifact_dir: Path
    report_prefix: Path
    holdout_registry_dir: Path
    expected_test_rows: Annotated[int, Field(ge=1, strict=True)]
    candidate_id: Literal["rf_raw7"]
    threshold: Literal[0.7]
    test_rule: Literal["confirm_selected_policy_without_reselection"]
    scope: Literal["local_advisory_integration_only"]


def load_config(path: Path) -> TestConfig:
    values = read_json(path)
    for name in ("dataset_dir", "cost_root", "cost_report", "artifact_dir", "report_prefix", "holdout_registry_dir"):
        values[name] = (path.resolve().parent / values[name]).resolve()
    return TestConfig.model_validate(values)


def verify_cost_completion(config: TestConfig, data) -> tuple[dict, dict, dict]:
    manifest_path = config.cost_root / "cost_manifest.json"
    gate_path = config.cost_root / "candidate_lock.json"
    if (file_hash(manifest_path) != config.expected_cost_manifest_sha256 or
            file_hash(gate_path) != config.expected_candidate_lock_sha256):
        raise ValueError("사전에 지정한 비용 완료 기록·후보 잠금 해시가 다릅니다.")
    manifest, gate = read_json(manifest_path), read_json(gate_path)
    if (manifest["status"] != "complete" or manifest["result_status"] != "candidate_locked_pending_independent_test" or
            manifest["candidate_lock_sha256"] != config.expected_candidate_lock_sha256 or
            manifest["test_evaluated"] is not False or gate["status"] != "candidate_locked_before_test" or
            gate["test_files_read"] is not False):
        raise ValueError("독립 Test를 기다리는 완료 비용 기록과 후보 잠금이 필요합니다.")
    protected = {str(manifest_path): file_hash(manifest_path), str(gate_path): file_hash(gate_path)}
    protected.update({str(inside_path(config.cost_root, name)): item["sha256"] for name, item in manifest["artifacts"].items()})
    protected.update(manifest["reports"])
    verify_without_holdout(protected)
    if str(config.cost_report) not in manifest["reports"]:
        raise ValueError("비용 완료 기록이 보호하는 원본 비용 보고서를 지정해야 합니다.")
    report = read_json(config.cost_report)
    protocol = read_json(config.cost_root / "cost_protocol_lock.json")
    cost_config = CostConfig.model_validate(protocol["config"])
    if (report["status"] != "candidate_locked_pending_independent_test" or report["cost_verified"] is not True or
            report["test_evaluated"] is not False or report["deployment_ready"] is not False or
            report["candidate_lock_sha256"] != config.expected_candidate_lock_sha256 or
            Path(report["candidate_lock_path"]).resolve() != gate_path.resolve() or
            cost_config.dataset_dir != config.dataset_dir or cost_config.dataset_id != config.dataset_id or
            gate["dataset_id"] != data.dataset_id or Path(gate["dataset_dir"]).resolve() != config.dataset_dir or
            gate["expected_test_rows"] != config.expected_test_rows or gate["input_sha256"] != data.input_sha256 or
            len(data.train.y) != cost_config.expected_train_rows or len(data.validation.y) != cost_config.expected_validation_rows):
        raise ValueError("비용 보고서·데이터셋·Train/Validation·후보 잠금이 일치하지 않습니다.")
    descriptor = gate["descriptor"]
    if (gate["selected_model_id"] != descriptor["id"] or descriptor != report["descriptor"] or
            descriptor != protocol["descriptor"] or gate["criteria"] != report["criteria"] or
            gate["criteria"] != protocol["criteria"] or gate["selected_trial"] != report["selected_trial"] or
            gate["cost_summary"] != report["cost"]["summary"] or
            descriptor["candidate_id"] != config.candidate_id or descriptor["threshold"] != config.threshold or
            descriptor["kind"] != "random_forest" or descriptor["input_transform_id"] != "raw7_v1" or
            descriptor["requires_ordered_session_prefix"] is not False or gate["test_rule"] != config.test_rule or
            gate["criteria"]["scope"] != config.scope):
        raise ValueError("고정 RF·0.7 정책·원본 입력·승인 기준을 변경할 수 없습니다.")
    expected = expected_test_hashes(config.dataset_dir)
    signature = digest({"descriptor": descriptor, "criteria": gate["criteria"], "test_rule": gate["test_rule"],
                        "expected_test_sha256": expected})
    if (gate["expected_test_sha256"] != expected or protocol["expected_test_sha256"] != expected or
            signature != gate["policy_signature"]):
        raise ValueError("Test 예상 해시·고정 정책 서명이 다릅니다.")
    protected.update(report["protected_sha256"])
    protected.update(gate["pre_test_protected_sha256"])
    verify_without_holdout(protected)
    validate_cost_runs(cost_config, descriptor, data, {descriptor["id"]: report["cost"]})
    if rejection_reasons(gate["selected_trial"], gate["criteria"]) or cost_rejections(gate["cost_summary"], gate["criteria"]):
        raise ValueError("기존 Validation·비용 기준을 통과한 후보만 Test에서 확인합니다.")
    job = read_json(config.cost_root / "cost" / descriptor["id"] / "job.json")
    if (job["descriptor"] != descriptor or job["benchmark"] != cost_config.benchmark.model_dump() or
            job["input_split"] != "validation" or file_hash(Path(job["input_path"])) != job["input_sha256"]):
        raise ValueError("비용 측정의 고정 Validation 입력·정책이 다릅니다.")
    with np.load(job["input_path"], allow_pickle=False) as archive:
        if archive.files != ["X"] or not np.array_equal(archive["X"], data.validation.X.to_numpy()):
            raise ValueError("비용 NPZ가 원본 Validation 입력과 다릅니다.")
    if protocol["validation_matrix_sha256"] != matrix_hash(data.validation.X):
        raise ValueError("비용 단계의 Validation 행렬·열 순서가 다릅니다.")
    return gate, report, protected


def validate_runtime(gate: dict, data) -> FrozenRuntime:
    descriptor = gate["descriptor"]
    runtime = FrozenRuntime(descriptor)
    files = descriptor["files"]
    schema = read_json(Path(files["model_input_schema"]["path"]))
    policy = read_json(Path(files["decision_policy"]["path"]))
    if (not isinstance(runtime.model, RandomForestClassifier) or runtime.model.n_jobs != 1 or
            runtime.scaler is not None or list(runtime.pipeline.named_steps) != ["classifier"] or
            list(runtime.model.classes_) != [0, 1] or list(runtime.model.feature_names_in_) != FEATURE_NAMES or
            schema["model_input_features"] != FEATURE_NAMES or schema["raw_input_features"] != FEATURE_NAMES or
            schema["input_transform_id"] != "raw7_v1" or schema["requires_ordered_session_prefix"] is not False or
            read_json(Path(files["feature_schema"]["path"])) != data.schema or
            read_json(Path(files["label_map"]["path"])) != data.label_map or
            policy["threshold"] != 0.7 or policy["positive_class"] != 1 or policy["comparison"] != ">="):
        raise ValueError("고정 추론 경로의 모델·schema·정답·판정 정책이 다릅니다.")
    # Test를 열기 전에 잠근 모델 하나의 기존 Validation 지표만 재확인한다. 선정·재학습은 하지 않는다.
    replay = make_trial({"candidate_id": descriptor["candidate_id"], "input_transform_id": descriptor["input_transform_id"]},
                        data.validation, runtime.predict_raw(data.validation.X.to_numpy()), runtime.threshold, gate["criteria"])
    if replay != gate["selected_trial"]:
        raise ValueError("Test 전 고정 후보의 기존 Validation 결과를 재현할 수 없습니다.")
    return runtime


def validate_test_sessions(source) -> None:
    indices = source.metadata.request_index.astype("int64").to_numpy()
    for group in source.metadata.groupby(["campaign_id", "run_id", "session_id"], sort=False).indices.values():
        group = np.asarray(group)[np.argsort(indices[group], kind="stable")]
        if not np.array_equal(indices[group], np.arange(1, len(group) + 1)):
            raise ValueError("Test 세션 메시지 번호에 누락·중복·잘못된 시작 번호가 있습니다.")
        if source.X.iloc[group[0]].message_interval_ms != 0:
            raise ValueError("Test 첫 메시지의 파싱 간격은 0이어야 합니다.")
        for name in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total"):
            if (np.diff(source.X.iloc[group][name].to_numpy()) < 0).any():
                raise ValueError("Test 세션의 누적 수치가 감소합니다.")


def evaluate_test(source, runtime: FrozenRuntime) -> tuple[dict, pd.DataFrame]:
    probability = runtime.predict_raw(source.X.to_numpy())
    if probability.shape != source.y.shape or not np.isfinite(probability).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("Test 확률의 행 수·유한값·범위가 올바르지 않습니다.")
    prediction = (probability >= runtime.threshold).astype("int64")
    # 캠페인별 결과는 진단용이다. 사전에 없는 캠페인별 추가 승인 기준이나 threshold를 만들지 않는다.
    by_campaign = {}
    for campaign, group in source.metadata.groupby("campaign_id", sort=True).indices.items():
        y, pred = source.y[group], prediction[group]
        normal, anomaly = int((y == 0).sum()), int((y == 1).sum())
        fp, fn = int(((y == 0) & (pred == 1)).sum()), int(((y == 1) & (pred == 0)).sum())
        by_campaign[campaign] = {"rows": len(group), "normal": normal, "anomaly": anomaly, "fp": fp, "fn": fn,
            "false_positive_rate": fp / normal if normal else None, "recall": (anomaly - fn) / anomaly if anomaly else None}
    result = {"metrics": calculate_metrics(source.y, probability, runtime.threshold),
              "by_scenario": scenario_metrics(source, prediction), "by_message_position": message_position_counts(source, prediction),
              "by_campaign": by_campaign,
              "burst_prefix": prefix_diagnostics(SimpleNamespace(validation=source), probability, runtime.threshold)}
    frame = pd.concat([source.metadata.copy(), source.X.copy()], axis=1)
    frame["label"], frame["anomaly_probability"], frame["prediction"], frame["threshold"] = source.y, probability, prediction, runtime.threshold
    frame["error_type"] = np.select([(source.y == 0) & (prediction == 1), (source.y == 1) & (prediction == 0)], ["FP", "FN"], default="correct")
    return result, frame


def export_approved_bundle(directory: Path, gate: dict, data, verdict_path: Path) -> dict:
    verdict = read_json(verdict_path)
    gate_path = Path(verdict["candidate_lock_path"])
    if (verdict["status"] != "approved_for_local_advisory_integration" or verdict["reasons"] or
            verdict["policy_signature"] != gate["policy_signature"] or read_json(gate_path) != gate or
            file_hash(gate_path) != verdict["candidate_lock_sha256"]):
        raise ValueError("잠근 정책의 승인 판정이 확인돼야 배포 묶음을 만듭니다.")
    directory.mkdir(parents=True, exist_ok=False)
    files = {}
    for role in ("model", "feature_schema", "model_input_schema", "label_map"):
        original = gate["descriptor"]["files"][role]
        name = Path(original["path"]).name
        target = directory / name
        shutil.copyfile(original["path"], target)
        if file_hash(target) != original["sha256"]:
            raise ValueError("승인 묶음 복사 결과가 잠근 원본과 다릅니다.")
        files[role] = {"path": name, "sha256": file_hash(target), "bytes": target.stat().st_size}
    decision = directory / "decision_policy.json"
    write_json(decision, {"candidate_id": "rf_raw7", "threshold": 0.7, "positive_class": 1, "comparison": ">=",
        "input_transform_id": "raw7_v1", "selection": "locked_validation_and_cost_policy_confirmed_on_test",
        "scope": "local_advisory_integration_only", "test_confirmed": True, "automatic_blocking_allowed": False})
    files["decision_policy"] = {"path": decision.name, "sha256": file_hash(decision), "bytes": decision.stat().st_size}
    required_bytes = sum(item["bytes"] for item in files.values())
    if required_bytes > gate["criteria"]["max_deployment_artifacts_mib"] * 1024 * 1024:
        raise ValueError("승인 정책을 포함한 실제 필수 파일 합계가 사전 예산을 넘습니다.")
    manifest = {"manifest_version": "operational_rf_deployment_v1", "status": "approved_for_local_advisory_integration",
        "model_id": gate["selected_model_id"], "kind": "random_forest", "candidate_id": "rf_raw7", "threshold": 0.7,
        "positive_class": 1, "comparison": ">=", "dataset_id": data.dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "input_transform_id": "raw7_v1", "requires_ordered_session_prefix": False, "preprocessing": "identity_no_scaler",
        "files": files, "required_files_bytes": required_bytes,
        "candidate_lock_sha256": verdict["candidate_lock_sha256"],
        "policy_signature": gate["policy_signature"], "test_verdict_path": str(verdict_path.resolve()),
        "test_verdict_sha256": file_hash(verdict_path), "local_integration_approved": True,
        "requires_complete_evaluation_manifest": True,
        "evaluation_manifest_path": str((directory.parent / "evaluation_manifest.json").resolve()),
        "fastapi_integration_implemented": False, "production_ready": False, "automatic_blocking_allowed": False,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "scikit_learn": sklearn.__version__}}
    write_json(directory / "deployment_manifest.json", manifest)
    return {"directory": str(directory.resolve()), "manifest_sha256": file_hash(directory / "deployment_manifest.json"),
            "required_files_bytes": required_bytes, "total_directory_bytes": sum(path.stat().st_size for path in directory.iterdir()), "manifest": manifest}


def render_report(report: dict) -> str:
    metrics, approval = report["test"]["metrics"], report["approval"]
    matrix = metrics["confusion_matrix"]
    lines = ["# 고정 RF·0.7의 독립 Test 승인 확인", "", "## 평가 정책과 접근 기록", "",
        "Validation·비용으로 잠근 원본 7개 RF·threshold 0.7 한 개만 평가했다. 재학습·후보 비교·threshold 탐색·기준 완화는 하지 않았다.",
        f"Test {metrics['rows']:,}건. 이전 접근 예약 여부: `{report['holdout_previously_reserved']}`.",
        f"이번 확인의 성격: `{report['evaluation_kind']}`. 같은 정책 재확인은 새 독립 평가라고 주장하지 않는다.",
        "비용 완료 manifest·후보 잠금·정책 서명·보호 해시를 먼저 확인하고 평가 protocol·holdout 접근 예약을 남긴 뒤 Test CSV를 열었다.", "",
        "## 전체 Test 결과", "", "| 지표 | 결과 |", "| --- | ---: |"]
    for name in ("accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "false_positive_rate", "false_negative_rate"):
        lines.append(f"| {name} | {metrics[name]:.6f} |")
    lines += ["", f"TN {matrix['tn']:,} · FP {matrix['fp']:,} · FN {matrix['fn']:,} · TP {matrix['tp']:,}.",
        "", "## 시나리오별 결과", "", "| 시나리오 | 메시지 | FP | FN | 정상 FPR | 이상 Recall |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["test"]["by_scenario"].items():
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {fpr} | {recall} |")
    lines += ["", "## 승인 판단", "", f"상태: `{approval['status']}`.", f"미충족 사유: {'; '.join(approval['reasons']) or '없음'}.",
        "전체 FPR 10% 이하·정상 묶음 FPR 10% 이하·Recall 50% 이상과 잠근 Validation 비용 기준을 그대로 적용했다.",
        f"기존 비용은 p95 {report['cost_summary']['single_request_worst_process_p95_ms']:.4f}ms·프로세스 peak RSS {report['cost_summary']['maximum_native_process_peak_rss_bytes'] / (1024 * 1024):.2f}MiB이며 이번에 재측정하거나 Test 비용으로 재선정하지 않았다.",
        "", "## 캠페인별 진단", "", "| 캠페인 | 메시지 | FP | FN | 정상 FPR | 이상 Recall |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["test"]["by_campaign"].items():
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {fpr} | {recall} |")
    lines += ["", "캠페인·시나리오·첫 메시지 지표는 누락·편향 진단이다. 사전에 없던 추가 승인 기준으로 정책을 바꾸지 않는다.",
        f"메시지 평가 coverage: {report['coverage']['campaigns']}개 캠페인·{report['coverage']['runs']}개 run·{report['coverage']['sessions']}개 세션.",
        f"메시지 snapshot이 없는 헤더 거부 run {report['coverage']['excluded_header_runs']}개는 모델 성능 분모에 넣지 않았다.",
        "", "## 한계와 상태", ""]
    lines += [f"- {value}" for value in report["limitations"]]
    if report["deployment_bundle"] is not None:
        lines += ["", "모델·원본 schema·모델 입력 schema·label map·0.7 정책과 승인 manifest를 별도 deployment_bundle에 저장했다.",
            "원본 후보 묶음·학습 기록은 수정하지 않았다. 패키지는 로컬 advisory 연동용이며 FastAPI는 아직 dummy다.",
            "", "## 다음 작업", "", "승인 묶음의 해시·입력 계약·threshold를 검증해 FastAPI 실제 추론에 연결한다. 남은 큰 작업 묶음은 3개다."]
    else:
        lines += ["", "승인 묶음은 생성하지 않았다. 다른 후보나 threshold로 대체하지 않는다.",
            "", "## 다음 작업", "", "실패 기준과 관측 한계를 정리해 추가 개발 또는 제한된 시연 범위를 별도로 결정한다. 같은 Test에 맞춘 재선정은 하지 않는다. 남은 큰 작업 묶음은 4개다."]
    return "\n".join(lines) + "\n"


def run(config: TestConfig, config_path: Path | None = None) -> dict:
    config = TestConfig.model_validate(config.model_dump())
    outputs = [config.report_prefix.with_suffix(suffix) for suffix in (".json", ".md", ".predictions.csv")]
    if config.artifact_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 Test 평가·실패 기록은 덮어쓰지 않습니다. 같은 정책 재확인은 별도 출력 경로를 사용하세요.")
    roots = (config.dataset_dir, config.cost_root, config.holdout_registry_dir)
    if (any(path.resolve().is_relative_to(root.resolve()) for path in [config.artifact_dir, *outputs] for root in roots) or
            any(path.resolve().is_relative_to(config.artifact_dir.resolve()) for path in outputs) or
            any(config.holdout_registry_dir.resolve().is_relative_to(root.resolve())
                for root in (config.dataset_dir, config.cost_root, config.artifact_dir))):
        raise ValueError("원본 데이터·비용·holdout 기록과 평가 출력 경로는 분리해야 합니다.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    gate, _, protected = verify_cost_completion(config, data)
    raw_roots = [Path(name).resolve().parent for name in protected if Path(name).name == "assignment_manifest.json"]
    if any(path.resolve().is_relative_to(root) for path in [config.artifact_dir, *outputs, config.holdout_registry_dir] for root in raw_roots):
        raise ValueError("원본 수집 batch에는 Test 평가 결과를 저장하지 않습니다.")
    runtime = validate_runtime(gate, data)
    source_files = [Path(__file__).resolve(), AI_ROOT / "tests" / "test_operational_recovery_test.py"]
    if config_path is not None:
        source_files.append(config_path.resolve())
    protected.update({str(path): file_hash(path) for path in source_files})
    verify_without_holdout(protected)
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    reserved = False
    try:
        protocol_path = config.artifact_dir / "evaluation_protocol_lock.json"
        write_json(protocol_path, {"status": "locked_before_holdout_reservation_and_test", "locked_at_utc": datetime.now(UTC).isoformat(),
            "config": config.model_dump(mode="json"), "candidate_lock_sha256": config.expected_candidate_lock_sha256,
            "policy_signature": gate["policy_signature"], "protected_sha256": protected,
            "test_files_read": False, "model_training_performed": False, "selection_used_test": False})
        protected[str(protocol_path)] = file_hash(protocol_path)
        gate_path = config.cost_root / "candidate_lock.json"
        receipt, previously_used = reserve_holdout(config.holdout_registry_dir, gate_path, config.expected_candidate_lock_sha256)
        reserved = True
        protected[str(receipt)] = file_hash(receipt)
        verify_without_holdout(protected)
        test, test_evidence = load_test(config.dataset_dir, data, gate_path, config.expected_candidate_lock_sha256, receipt)
        if len(test.y) != config.expected_test_rows:
            raise ValueError("Test 행 수가 잠근 계획과 다릅니다.")
        validate_test_sessions(test)
        protected.update({str(config.dataset_dir / name): value for name, value in test_evidence.items()})
        result, predictions = evaluate_test(test, runtime)
        reasons = rejection_reasons(result, gate["criteria"]) + cost_rejections(gate["cost_summary"], gate["criteria"])
        status = "approved_for_local_advisory_integration" if not reasons else "withheld_after_test"
        evaluation_kind = "same_policy_recheck_of_exposed_test" if previously_used else "first_locked_policy_confirmation"
        verdict_path = config.artifact_dir / "test_verdict.json"
        write_json(verdict_path, {"status": status, "reasons": reasons, "candidate_lock_path": str(gate_path),
            "candidate_lock_sha256": config.expected_candidate_lock_sha256, "policy_signature": gate["policy_signature"],
            "test_input_sha256": test_evidence, "metrics": result["metrics"], "by_scenario": result["by_scenario"],
            "criteria": gate["criteria"], "cost_summary": gate["cost_summary"], "holdout_receipt_path": str(receipt),
            "holdout_previously_reserved": previously_used, "evaluation_kind": evaluation_kind,
            "production_ready": False, "automatic_blocking_allowed": False})
        verify_hashes(protected)
        bundle = export_approved_bundle(config.artifact_dir / "deployment_bundle", gate, data, verdict_path) if not reasons else None
        outputs[0].parent.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(outputs[2], index=False, encoding="utf-8", mode="x")
        restored = pd.read_csv(outputs[2], float_precision="round_trip", dtype={"row_id": str})
        if (restored.row_id.tolist() != test.metadata.row_id.tolist() or
                not np.array_equal(restored.label.to_numpy(), test.y) or
                not np.array_equal(restored.anomaly_probability.to_numpy(), predictions.anomaly_probability.to_numpy()) or
                not np.array_equal(restored.prediction.to_numpy(), predictions.prediction.to_numpy()) or
                not np.all(restored.threshold.to_numpy() == 0.7)):
            raise ValueError("Test 예측 CSV 저장 후 행·정답·확률·판단·threshold가 달라졌습니다.")
        manifest = read_json(config.dataset_dir / "dataset_manifest.json")
        test_campaigns = set(test.metadata.campaign_id)
        excluded = [entry for entry in manifest.get("excluded_sources", []) if entry["campaign_id"] in test_campaigns]
        report = {"report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
            "experiment_id": config.experiment_id, "dataset_id": data.dataset_id, "selected_model_id": gate["selected_model_id"],
            "candidate_id": "rf_raw7", "threshold": 0.7, "feature_order": FEATURE_NAMES, "descriptor": gate["descriptor"],
            "candidate_lock_sha256": config.expected_candidate_lock_sha256, "cost_manifest_sha256": config.expected_cost_manifest_sha256,
            "policy_signature": gate["policy_signature"], "evaluation_protocol_lock_sha256": file_hash(protocol_path),
            "test": result, "criteria": gate["criteria"], "cost_summary": gate["cost_summary"],
            "validation_reference": gate["selected_trial"], "test_input_sha256": test_evidence,
            "coverage": {"rows": len(test.y), "campaigns": test.metadata.campaign_id.nunique(), "runs": test.metadata.run_id.nunique(),
                "sessions": len(test.metadata.drop_duplicates(["campaign_id", "run_id", "session_id"])),
                "excluded_header_runs": sum(entry["scenario_id"] == "invalid_frame_size" for entry in excluded), "excluded_sources": excluded},
            "approval": {"status": status, "reasons": reasons, "local_integration_approved": not reasons,
                         "production_ready": False, "automatic_blocking_allowed": False},
            "deployment_bundle": bundle, "holdout_receipt_path": str(receipt), "holdout_previously_reserved": previously_used,
            "evaluation_kind": evaluation_kind, "protected_sha256": protected,
            "test_evaluated": True, "selection_used_test": False, "thresholds_changed": False,
            "model_training_performed": False, "cost_remeasured": False, "fastapi_still_dummy": True,
            "remaining_major_stages": 3 if not reasons else 4,
            "limitations": ["같은 생성기·장비의 별도 캠페인 2개를 평가했다. 메시지 5,407건을 독립 시행 5,407개로 해석하지 않는다.",
                "통제된 애플리케이션 메시지 이상 실험이며 공개 UNSW 성능이나 실제 외부 침입 탐지 성능과 합치지 않는다.",
                "첫 메시지와 malformed의 부족한 과거·본문 정보를 raw7이 복원하지 못한다. 시나리오별 누락을 전체 평균과 함께 확인한다.",
                "Test 결과로 후보·threshold·성능 기준을 바꾸지 않았다. 같은 Test는 이제 노출된 자료이며 바뀐 정책에는 새 독립 평가가 필요하다.",
                "비용은 이전 Validation의 오프라인 측정이며 JSON·HTTP·큐·FastAPI/TCP 종단 부하는 포함하지 않는다.",
                "승인은 로컬 advisory 연동만 허용한다. FastAPI 교체·실제 운영·자동 차단은 이번 단계에서 수행하지 않는다."]}
        write_json(outputs[0], report)
        with outputs[1].open("x", encoding="utf-8") as stream:
            stream.write(render_report(report))
        verify_hashes(protected)
        inventory = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                     for path in sorted(config.artifact_dir.rglob("*")) if path.is_file()}
        write_json(config.artifact_dir / "evaluation_manifest.json", {"status": "complete", "approval_status": status,
            "artifacts": inventory, "reports": {str(path): file_hash(path) for path in outputs},
            "candidate_lock_sha256": config.expected_candidate_lock_sha256, "policy_signature": gate["policy_signature"],
            "test_evaluated": True, "model_training_performed": False, "production_ready": False})
        return report
    except BaseException as error:
        write_json(config.artifact_dir / "evaluation_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}",
            "holdout_reserved_this_attempt": reserved, "production_ready": False,
            "notice": "예약 이후 실패·중단도 holdout 사용 기록을 지우지 않는다. 완료 manifest 없는 묶음은 사용하지 않는다."})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="비용으로 잠근 RF·0.7 한 개를 새 Test에서 독립 승인 확인합니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_recovery_test.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    values = load_config(args.config).model_dump()
    for name in ("artifact_dir", "report_prefix"):
        if getattr(args, name) is not None:
            values[name] = getattr(args, name).resolve()
    report = run(TestConfig.model_validate(values), args.config)
    print(f"[보정 Test] {report['approval']['status']}")
    print(f"[보정 Test] 남은 큰 작업 묶음: {report['remaining_major_stages']}개. FastAPI는 아직 dummy입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
