from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import Field
from sklearn.ensemble import RandomForestClassifier

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import inside_path, read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.compare_operational_recovery import choose_trial, make_trial, rejection_reasons
from evaluation.operational_cost_worker import latency_summary
from evaluation.operational_final_data import TEST_FILES, expected_test_hashes
from evaluation.operational_final_models import FrozenRuntime
from evaluation.run_operational_final_evaluation import Benchmark, MIB, benchmark_models, digest, summarize_cost
from training.operational_data import load_operational_data
from training.run_operational_recovery_experiment import matrix_hash
from training.train_operational_logistic_regression import write_json


class CostConfig(StrictModel):
    config_version: Literal["v1"]
    experiment_id: Identifier
    dataset_id: Identifier
    dataset_dir: Path
    training_root: Path
    training_report: Path
    design_path: Path
    artifact_dir: Path
    report_prefix: Path
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)]
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)]
    expected_test_rows: Annotated[int, Field(ge=1, strict=True)]
    candidate_id: Literal["rf_raw7"]
    input_transform_id: Literal["raw7_v1"]
    threshold: Literal[0.7]
    benchmark: Benchmark
    test_rule: Literal["confirm_selected_policy_without_reselection"]
    scope: Literal["local_advisory_integration_only"]


def load_config(path: Path) -> CostConfig:
    values = read_json(path)
    for name in ("dataset_dir", "training_root", "training_report", "design_path", "artifact_dir", "report_prefix"):
        values[name] = (path.resolve().parent / values[name]).resolve()
    return CostConfig.model_validate(values)


def verify_without_holdout(evidence: dict) -> None:
    # 과거 manifest 안의 예상 Test 해시는 읽어도 실제 Test 파일이나 수집 레코드는 해시 검사에도 열지 않는다.
    for name in evidence:
        path = Path(name)
        if (path.name in TEST_FILES or path.suffix == ".jsonl" or path.name == "collection_summary.json" or
                "test" in path.stem.split("_") and path.suffix in {".csv", ".npz"}):
            raise ValueError("비용 단계에는 Test·수집 레코드 파일을 열 수 없습니다.")
    verify_hashes(evidence)


def training_evidence(config: CostConfig, data) -> tuple[dict, dict, dict]:
    root = config.training_root
    manifest_path = root / "experiment_manifest.json"
    manifest = read_json(manifest_path)
    if (manifest["status"] != "complete" or manifest["input_sha256"] != data.input_sha256 or
            manifest["test_evaluated"] is not False or manifest["deployment_ready"] is not False):
        raise ValueError("완료된 미배포 학습 실험과 현재 Train/Validation 입력이 일치해야 합니다.")
    evidence = {str(manifest_path): file_hash(manifest_path)}
    evidence.update({str(inside_path(root, name)): item["sha256"] for name, item in manifest["artifacts"].items()})
    evidence.update(manifest["reports"])
    verify_without_holdout(evidence)
    if str(config.training_report) not in evidence:
        raise ValueError("학습 완료 manifest가 보호하는 원본 보고서를 지정해야 합니다.")
    report = read_json(config.training_report)
    lock = read_json(root / "protocol_lock.json")
    selection = read_json(root / "selection.json")
    if (report["status"] != "complete" or report["dataset_id"] != data.dataset_id or
            report["input_sha256"] != data.input_sha256 or lock["protocol"] != report["protocol"] or
            lock["protocol_sha256"] != report["protocol_sha256"] or
            manifest["protocol_sha256"] != report["protocol_sha256"] or
            selection["protocol_sha256"] != report["protocol_sha256"] or
            digest(report["protocol"]) != report["protocol_sha256"]):
        raise ValueError("학습 protocol·완료 manifest·선정·보고서가 일치하지 않습니다.")
    evidence.update(report["protected_sha256"])
    verify_without_holdout(evidence)
    if (read_json(config.design_path) != report["protocol"]["recovery_plan"] or
            selection["status"] != "selected_pending_cost_and_test" or
            report["selection"]["selected_trial"] != selection["selected_trial"] or
            choose_trial(report["trials"], report["protocol"]["recovery_plan"]["approval_criteria"]) != selection["selected_trial"]):
        raise ValueError("수집 전 계획과 Validation 선정 규칙을 유지해야 합니다.")
    chosen = selection["selected_trial"]
    if (chosen["candidate_id"] != config.candidate_id or chosen["threshold"] != config.threshold or
            chosen["input_transform_id"] != config.input_transform_id or
            selection["threshold"] != config.threshold or selection["positive_class"] != 1 or
            selection["comparison"] != ">="):
        raise ValueError("이미 선정한 원본 RF·0.7 정책 외에는 비용 단계에서 교체하지 않습니다.")
    return report, selection, evidence


def freeze_selected(config: CostConfig, data, report: dict, selection: dict, directory: Path) -> tuple[dict, dict]:
    chosen = selection["selected_trial"]
    source = config.training_root / "candidates" / config.candidate_id
    schema = read_json(source / "model_input_schema.json")
    summary = read_json(source / "training_summary.json")
    raw_schema = read_json(config.training_root / "raw_feature_schema.json")
    label_map = read_json(config.training_root / "label_map.json")
    if (schema["input_transform_id"] != "raw7_v1" or schema["model_input_features"] != FEATURE_NAMES or
            schema["raw_input_features"] != FEATURE_NAMES or schema["requires_ordered_session_prefix"] is not False or
            schema["preprocessing"] != "identity_no_scaler" or raw_schema != data.schema or label_map != data.label_map or
            summary["train_rows"] != len(data.train.y) or summary["fit_scope"] != "new_train_only" or
            summary["model_sha256"] != file_hash(source / "pipeline.joblib") or
            summary["train_matrix_sha256"] != matrix_hash(data.train.X) or
            summary["validation_matrix_sha256"] != matrix_hash(data.validation.X)):
        raise ValueError("선정 모델의 원본 입력 의미·Train 전용 학습·저장 행렬이 다릅니다.")
    if Path(selection["model_path"]).resolve() != (source / "pipeline.joblib").resolve():
        raise ValueError("선정 정책의 모델 경로가 다릅니다.")
    directory.mkdir(parents=True, exist_ok=False)
    roles = {"model": (source / "pipeline.joblib", "pipeline.joblib"),
             "feature_schema": (config.training_root / "raw_feature_schema.json", "feature_schema.json"),
             "model_input_schema": (source / "model_input_schema.json", "model_input_schema.json"),
             "label_map": (config.training_root / "label_map.json", "label_map.json")}
    files = {}
    for role, (origin, name) in roles.items():
        target = directory / name
        shutil.copyfile(origin, target)
        if file_hash(target) != file_hash(origin):
            raise ValueError("비용 평가용 후보 묶음이 학습 원본과 다릅니다.")
        files[role] = {"path": str(target.resolve()), "sha256": file_hash(target), "bytes": target.stat().st_size}
    # 후보 기본 정책 0.5를 덮어쓰지 않고, 선정한 0.7 정책을 미승인 후보 묶음에 따로 저장한다.
    policy = directory / "decision_policy.json"
    write_json(policy, {"candidate_id": config.candidate_id, "threshold": config.threshold, "positive_class": 1,
                        "comparison": ">=", "input_transform_id": config.input_transform_id,
                        "selection": "validation_selected_pending_cost_and_test", "test_confirmed": False,
                        "deployment_ready": False, "automatic_blocking_allowed": False})
    files["decision_policy"] = {"path": str(policy.resolve()), "sha256": file_hash(policy), "bytes": policy.stat().st_size}
    descriptor = {"id": "selected_recovery_rf", "kind": "random_forest", "candidate_id": config.candidate_id,
                  "threshold": config.threshold, "input_transform_id": config.input_transform_id,
                  "requires_ordered_session_prefix": False, "files": files, "validation_metrics": chosen["metrics"]}
    runtime = FrozenRuntime(descriptor)
    expected_parameters = report["protocol"]["full_rf_parameters"]
    if (not isinstance(runtime.model, RandomForestClassifier) or runtime.model.get_params() != expected_parameters or
            runtime.scaler is not None or list(runtime.pipeline.named_steps) != ["classifier"] or
            list(runtime.model.classes_) != [0, 1] or list(runtime.model.feature_names_in_) != FEATURE_NAMES):
        raise ValueError("RF 학습 설정·입력 순서·클래스·무전처리 경로가 다릅니다.")
    probability = runtime.predict_raw(data.validation.X.to_numpy())
    saved = pd.read_csv(source / "validation_predictions.csv", float_precision="round_trip", dtype={"row_id": str})
    if (saved.row_id.tolist() != data.validation.metadata.row_id.tolist() or
            not np.array_equal(saved.label.to_numpy(), data.validation.y) or
            not np.array_equal(saved.anomaly_probability.to_numpy(), probability)):
        raise ValueError("저장 후보와 새 Validation 재예측의 행·정답·확률이 다릅니다.")
    replay = make_trial({"candidate_id": config.candidate_id, "input_transform_id": config.input_transform_id},
                        data.validation, probability, config.threshold, report["protocol"]["recovery_plan"]["approval_criteria"])
    if replay != chosen:
        raise ValueError("원본 0.7 선정의 전체·시나리오·첫 메시지 Validation 결과가 다릅니다.")
    return descriptor, {value["path"]: value["sha256"] for value in files.values()}


def validate_cost_runs(config: CostConfig, descriptor: dict, data, costs: dict) -> None:
    if set(costs) != {descriptor["id"]}:
        raise ValueError("선정한 RF 하나만 측정해야 합니다.")
    result = costs[descriptor["id"]]
    runs = result["process_records"]
    indices = np.random.default_rng(config.benchmark.sampling_seed).choice(len(data.validation.y),
        size=config.benchmark.single_request_repeats, replace=len(data.validation.y) < config.benchmark.single_request_repeats).tolist()
    if len(runs) != config.benchmark.process_repeats or len({run["pid"] for run in runs}) != len(runs):
        raise ValueError("서로 다른 프로세스의 고정 반복 수를 확인할 수 없습니다.")
    for run in runs:
        if (run["descriptor"] != descriptor or run["input_rows"] != len(data.validation.y) or
                run["sample_indices"] != indices or run["torch_imported"] is not False or
                run["model_training_performed"] is not False or run["test_files_read"] is not False or
                run["environment"]["cpu_threads"] != 1 or
                run["required_model_files_bytes"] != sum(item["bytes"] for item in descriptor["files"].values())):
            raise ValueError("비용 worker의 모델·입력·스레드·학습 금지 조건이 다릅니다.")
        if any(pool["num_threads"] != 1 for pool in run["environment"]["threadpools"]):
            raise ValueError("비용 측정 중 계산 라이브러리 스레드가 1개가 아닙니다.")
        if (any(len(run["single_request"][name]["samples_ms"]) != config.benchmark.single_request_repeats
                for name in ("features_to_decision", "model_only")) or
                len(run["batch"]["samples_ms"]) != config.benchmark.batch_repeats):
            raise ValueError("지연시간 원본 표본 수가 설정과 다릅니다.")
        for name in ("features_to_decision", "model_only"):
            if latency_summary(run["single_request"][name]["samples_ms"]) != run["single_request"][name]:
                raise ValueError("단일 요청 지연시간 요약이 원본 표본과 다릅니다.")
        batch = run["batch"]
        if ({key: value for key, value in batch.items() if key != "amortized_per_message_median_ms"} !=
                latency_summary(batch["samples_ms"]) or
                batch["amortized_per_message_median_ms"] != batch["median_ms"] / len(data.validation.y)):
            raise ValueError("배치 지연시간 요약·메시지당 처리량 계산이 다릅니다.")
    if summarize_cost(runs) != result["summary"]:
        raise ValueError("비용 요약이 원본 프로세스 결과와 다릅니다.")


def cost_rejections(cost: dict, criteria: dict) -> list[str]:
    reasons = []
    for key, limit, title in (
        ("single_request_worst_process_p95_ms", criteria["max_single_request_p95_ms"], "단일 요청 p95"),
        ("maximum_native_process_peak_rss_bytes", criteria["max_process_peak_rss_mib"] * MIB, "프로세스 peak RSS"),
        ("required_model_files_bytes", criteria["max_deployment_artifacts_mib"] * MIB, "필수 후보 파일 합계"),
    ):
        value = cost.get(key)
        if value is None or not np.isfinite(value) or value < 0 or ("rss" in key and value == 0):
            reasons.append(f"{title} 측정값 없음 또는 유효하지 않음")
        elif value > limit:
            reasons.append(f"{title} 사전 상한 초과")
    return reasons


def render_report(report: dict) -> str:
    summary = report["cost"]["summary"]
    peak = summary["maximum_native_process_peak_rss_bytes"]
    rss = f"{peak / MIB:.2f}" if peak is not None else "측정 불가"
    chosen = report["selected_trial"]
    lines = ["# 보정 RF 비용 측정과 Test 전 잠금", "", "## 작업 범위", "",
             "이미 학습·선정한 원본 7개 RF·threshold 0.7만 검증했다. 재학습·scaler fit·후보 교체·threshold 탐색을 하지 않았다.",
             f"Train {report['train_rows']}건의 학습 provenance를 확인하고 Validation {report['validation_rows']}건으로만 비용을 측정했다.",
             "실제 Test CSV·수집 레코드·종료 summary는 열지 않았다. Test 예상 해시는 기존 dataset manifest에서만 읽었다.", "",
             "## 비용 결과", "", "| 항목 | 측정값 |", "| --- | ---: |",
             f"| 전처리 포함 단일 요청 중앙값 | {summary['single_request_median_ms']:.4f} ms |",
             f"| 가장 높은 프로세스 단일 요청 p95 | {summary['single_request_worst_process_p95_ms']:.4f} ms |",
             f"| 모델 단독 중앙값 | {summary['model_only_median_ms']:.4f} ms |",
             f"| 프로세스 전체 네이티브 peak RSS | {rss} MiB |",
             f"| 필수 후보 파일 5개 합계 | {summary['required_model_files_bytes'] / 1024:.2f} KiB |",
             f"| 주요 모델 배열 합계 | {summary['model_and_scaler_array_bytes'] / 1024:.2f} KiB |",
             f"| 전체 Validation 배치 중앙값 | {summary['batch_median_ms']:.4f} ms |",
             f"| 배치 시간/메시지 수 | {summary['batch_amortized_per_message_median_ms']:.6f} ms |", "",
             "별도 프로세스 3개·동일 seed 42의 요청 300개·워밍업 20회·전체 배치 10회를 측정 전에 고정했다.",
             "계산 라이브러리 스레드 1개와 RF n_jobs=1을 유지했다. 운영체제의 프로세스 전체 스레드 수가 1개라는 뜻은 아니다.",
             "단일 요청/배치 확률 차이는 1e-5 이내이며 0.7 기준 판단은 정확히 같아야 한다. Torch는 측정 프로세스에서 로딩하지 않았다.",
             "전처리 포함 경로는 원본 배열을 열 순서가 있는 DataFrame으로 만들고 RF 예측·threshold 비교까지 수행한다. scaler나 추가 prefix는 없다.",
             "모델 단독 경로도 threshold 비교를 포함하되 DataFrame은 미리 만든다. 행 선택·파일 로딩·검증·JSON·HTTP·큐 대기는 시간에서 제외한다.",
             "RSS는 라이브러리·입력·측정 캐시를 포함하는 프로세스 전체이며 배열 크기나 파일 크기와 같지 않다.",
             "필수 5개 파일은 모델·원본 schema·모델 입력 schema·label map·0.7 정책이다. 측정 NPZ·보고서·잠금 파일은 배포 파일 예산에 합치지 않는다.",
             "배치 시간/메시지 수는 처리량 참고값이며 실제 요청 한 건의 응답시간이 아니다.", "", "## 판정과 잠금", "",
             f"상태: `{report['status']}`. 미충족 사유: {'; '.join(report['rejection_reasons']) or '없음'}.",
             "원래 승인 기준은 전체 FPR 10% 이하·정상 묶음 FPR 10% 이하·Recall 50% 이상·p95 25ms 이하·peak RSS 512MiB 이하·파일 50MiB 이하다.",
             f"고정 Validation: FPR {chosen['metrics']['false_positive_rate']:.2%}, 정상 묶음 FPR {chosen['by_scenario']['normal_batched_chat']['false_positive_rate']:.2%}, Recall {chosen['metrics']['recall']:.2%}.",
             "비용을 통과해도 독립 Test 성능이나 FastAPI 배포 승인을 뜻하지 않는다. 비용 실패 때 다른 후보로 바꾸지 않는다.",
             "후보 묶음은 미승인 평가 자료다. 원본 기본 정책 0.5와 학습 selection은 수정하지 않았다.", "", "## 한계", ""]
    lines += [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 다음 작업", "", "잠근 단일 정책으로 새 Test 5,407건의 전체·정상 묶음 FPR과 Recall을 확인한다. 접근 예약을 남긴 뒤 한 번 평가하며 실패하면 승인 보류한다.",
              "현재 FastAPI는 dummy이며 자동 차단·외부 운영 승인은 하지 않는다. 남은 큰 작업 묶음은 4개다.", ""]
    return "\n".join(lines)


def run(config: CostConfig, config_path: Path | None = None) -> dict:
    config = CostConfig.model_validate(config.model_dump())
    outputs = [config.report_prefix.with_suffix(extension) for extension in (".json", ".md")]
    sources = (config.dataset_dir, config.training_root, config.training_report, config.design_path)
    if config.artifact_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("완료·실패 비용 기록은 덮어쓰지 않습니다. 별도 출력 경로의 재측정도 이전 기록과 함께 해석하세요.")
    if (any(path.resolve().is_relative_to(root.resolve()) for path in [config.artifact_dir, *outputs] for root in sources) or
            any(path.resolve().is_relative_to(config.artifact_dir.resolve()) for path in outputs)):
        raise ValueError("원본 데이터·모델·보고서 경로와 비용 산출물은 분리해야 합니다.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    manifest = read_json(config.dataset_dir / "dataset_manifest.json")
    if (len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows or
            manifest["splits"]["test"]["rows"] != config.expected_test_rows):
        raise ValueError("고정 Train/Validation/Test 행 수가 manifest와 다릅니다.")
    report, selection, protected = training_evidence(config, data)
    raw_roots = [Path(name).resolve().parent for name in protected if Path(name).name == "assignment_manifest.json"]
    if any(path.resolve().is_relative_to(root) for path in [config.artifact_dir, *outputs] for root in raw_roots):
        raise ValueError("원본 수집 batch에는 비용 산출물을 저장하지 않습니다.")
    criteria = report["protocol"]["recovery_plan"]["approval_criteria"]
    source_files = [Path(__file__).resolve(), AI_ROOT / "evaluation" / "operational_cost_worker.py",
                    AI_ROOT / "evaluation" / "operational_final_models.py",
                    AI_ROOT / "evaluation" / "run_operational_final_evaluation.py",
                    AI_ROOT / "evaluation" / "operational_final_data.py"]
    if config_path is not None:
        source_files.append(config_path.resolve())
    protected.update({str(path): file_hash(path) for path in source_files})
    verify_without_holdout(protected)
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    try:
        descriptor, candidate_evidence = freeze_selected(config, data, report, selection, config.artifact_dir / "candidate_bundle")
        protected.update(candidate_evidence)
        protocol_path = config.artifact_dir / "cost_protocol_lock.json"
        write_json(protocol_path, {"status": "locked_before_cost_measurement", "locked_at_utc": datetime.now(UTC).isoformat(),
            "config": config.model_dump(mode="json"), "descriptor": descriptor, "criteria": criteria,
            "protected_sha256": protected, "validation_matrix_sha256": matrix_hash(data.validation.X),
            "expected_test_sha256": expected_test_hashes(config.dataset_dir), "test_files_read": False,
            "validation_prior_use": "candidate_and_threshold_selection_completed", "no_fallback_on_cost_failure": True})
        protected[str(protocol_path)] = file_hash(protocol_path)
        # 별도 worker 생성 전 계산 스레드 환경을 고정하고, 종료·실패 후에는 부모의 기존 환경을 복원한다.
        names = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
        previous = {name: os.environ.get(name) for name in names}
        try:
            os.environ.update({name: "1" for name in names})
            costs, cost_evidence = benchmark_models(config, {descriptor["id"]: descriptor}, data, None, config.artifact_dir / "cost")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        verify_without_holdout(protected)
        validate_cost_runs(config, descriptor, data, costs)
        protected.update(cost_evidence)
        verify_without_holdout(protected)
        cost = costs[descriptor["id"]]
        chosen = selection["selected_trial"]
        reasons = rejection_reasons(chosen, criteria) + cost_rejections(cost["summary"], criteria)
        gate_path, gate_hash = None, None
        if not reasons:
            # 이 잠금만으로 Test를 읽거나 배포하지 않는다. 접근 예약·독립 승인 확인은 다음 단계다.
            gate_path = config.artifact_dir / "candidate_lock.json"
            signature = digest({"descriptor": descriptor, "criteria": criteria, "test_rule": config.test_rule,
                                "expected_test_sha256": expected_test_hashes(config.dataset_dir)})
            write_json(gate_path, {"status": "candidate_locked_before_test", "locked_at_utc": datetime.now(UTC).isoformat(),
                "selected_model_id": descriptor["id"], "descriptor": descriptor, "criteria": criteria,
                "policy_signature": signature, "expected_test_sha256": expected_test_hashes(config.dataset_dir),
                "dataset_id": data.dataset_id, "dataset_dir": str(config.dataset_dir),
                "expected_test_rows": config.expected_test_rows, "input_sha256": data.input_sha256,
                "cost_summary": cost["summary"], "selected_trial": chosen, "test_rule": config.test_rule,
                "test_files_read": False, "pre_test_protected_sha256": protected,
                "local_integration_approved": False, "production_ready": False, "automatic_blocking_allowed": False})
            gate_hash = file_hash(gate_path)
            protected[str(gate_path)] = gate_hash
        result = {"report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(),
                  "status": "candidate_locked_pending_independent_test" if not reasons else "withheld_before_test_due_to_cost",
                  "experiment_id": config.experiment_id, "dataset_id": data.dataset_id,
                  "train_rows": len(data.train.y), "validation_rows": len(data.validation.y),
                  "selected_trial": chosen, "descriptor": descriptor, "benchmark": config.benchmark.model_dump(),
                  "criteria": criteria, "cost": cost, "rejection_reasons": reasons,
                  "cost_protocol_lock_sha256": file_hash(protocol_path), "candidate_lock_path": str(gate_path) if gate_path else None,
                  "candidate_lock_sha256": gate_hash, "protected_sha256": protected,
                  "test_files_read": False, "test_evaluated": False, "holdout_reserved": False,
                  "model_training_performed": False, "threshold_search_performed": False,
                  "cost_verified": not reasons, "test_confirmed": False, "deployment_ready": False,
                  "fastapi_still_dummy": True, "production_ready": False, "automatic_blocking_allowed": False,
                  "limitations": ["모델 내부의 로컬 비용이며 TCP→HTTP→FastAPI→응답의 종단 지연시간이 아니다.",
                      "단일 장비의 측정 결과이며 운영 동시성·큐 포화·외부 트래픽·다른 장비의 비용을 보장하지 않는다.",
                      "Validation은 이미 모델·threshold 선정에 사용했다. 이 비용 통과는 독립 성능 확인이 아니다.",
                      "선정 정책은 오탐을 줄이는 대신 FN 275건이 남으며 malformed 길이 36건 중 33건을 놓쳤다.",
                      "통제된 애플리케이션 메시지 실험이다. 헤더 거부는 메시지 모델의 관측 밖이며 실제 침입 탐지 일반화는 확인하지 않았다."]}
        outputs[0].parent.mkdir(parents=True, exist_ok=True)
        write_json(outputs[0], result)
        with outputs[1].open("x", encoding="utf-8") as stream:
            stream.write(render_report(result))
        verify_without_holdout(protected)
        inventory = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                     for path in sorted(config.artifact_dir.rglob("*")) if path.is_file()}
        write_json(config.artifact_dir / "cost_manifest.json", {"status": "complete", "result_status": result["status"],
            "artifacts": inventory, "reports": {str(path): file_hash(path) for path in outputs},
            "candidate_lock_sha256": gate_hash, "test_evaluated": False, "deployment_ready": False})
        return result
    except BaseException as error:
        write_json(config.artifact_dir / "cost_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}",
                    "test_evaluated": False, "deployment_ready": False})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="선정 RF·0.7을 Validation에서만 비용 측정하고 Test 전 정책을 잠급니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_recovery_cost.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    values = load_config(args.config).model_dump()
    for name in ("artifact_dir", "report_prefix"):
        if getattr(args, name) is not None:
            values[name] = getattr(args, name).resolve()
    report = run(CostConfig.model_validate(values), args.config)
    print(f"[보정 비용] {report['status']}")
    print("[보정 비용] Test 예측·모델 배포·FastAPI 교체는 아직 하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
