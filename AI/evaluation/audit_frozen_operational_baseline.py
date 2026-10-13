from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from data_collection.augmentation import freeze_reference, verify_hashes
from data_collection.quality import inside_path, parse_json, read_json
from data_collection.traffic import CampaignPlan, planned_runs
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.audit_feature_alignment import assess_model_schema
from inference_api.payload_validation import OPERATIONAL_CONTRACT
from training.operational_data import OperationalData, OperationalSplit, load_operational_data
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


THRESHOLD = 0.7
DEFAULT_DATASET = AI_ROOT / "data" / "processed" / "operational_message" / "v2_campaign_holdout"
DEFAULT_EXPERIMENT = AI_ROOT / "artifacts" / "operational_message" / "validation_selection_v1"


def load_frozen_model(root: Path, data: OperationalData) -> tuple[Pipeline, dict, dict]:
    frozen = freeze_reference(root / "selection.json")
    selection = read_json(root / "selection.json")
    if (frozen["kind"] != "logistic_regression" or frozen["candidate_id"] != "lr_c_1" or
            frozen["threshold"] != THRESHOLD or selection.get("positive_class") != 1 or
            selection.get("comparison") != ">=" or selection.get("refit_with_validation") is not False or
            selection.get("feature_order") != FEATURE_NAMES or selection.get("label_map") != data.label_map):
        raise ValueError("이번 진단은 고정한 LR C=1·threshold 0.7·기존 입력/정답 정책만 사용합니다.")
    manifest = read_json(root / "experiment_manifest.json")
    evidence = dict(frozen["file_sha256"])

    def verified(relative: str) -> bytes:
        path = inside_path(root, relative)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if manifest["artifacts"][relative]["sha256"] != digest:
            raise ValueError(f"선정 산출물 SHA-256 불일치: {relative}")
        evidence[str(path)] = digest
        return content

    lock = parse_json(verified("protocol_lock.json").decode("utf-8"))
    protocol = lock["protocol"]
    digest = hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    candidates = [candidate for candidate in protocol["candidates"] if candidate["id"] == frozen["candidate_id"]]
    if (lock.get("status") != "locked_before_fit" or digest != lock["protocol_sha256"] or
            digest != selection["protocol_sha256"] or digest != manifest["protocol_sha256"] or
            lock["input_sha256"] != manifest["input_sha256"] or THRESHOLD not in protocol["thresholds"] or
            selection["rule"] != protocol["selection_rule"] or
            len(candidates) != 1 or candidates[0]["kind"] != "logistic_regression" or candidates[0]["C"] != 1.0):
        raise ValueError("기존 선정 protocol·모델 설정·threshold의 근거가 일치하지 않습니다.")
    prefix = f"candidates/{frozen['candidate_id']}"
    if (selection["model_path"] != f"{prefix}/pipeline.joblib" or
            selection["feature_schema_path"] != f"{prefix}/feature_schema.json" or
            selection["label_map_path"] != f"{prefix}/label_map.json"):
        raise ValueError("선정 후보와 모델·schema·정답 경로가 다릅니다.")
    training = parse_json(verified(f"{prefix}/training_manifest.json").decode("utf-8"))
    report_bytes = verified(f"candidate_reports/{frozen['candidate_id']}.json")
    training_report = parse_json(report_bytes.decode("utf-8"))
    model_bytes = verified(selection["model_path"])
    schema_bytes = verified(selection["feature_schema_path"])
    labels_bytes = verified(selection["label_map_path"])
    policy_bytes = verified(f"{prefix}/decision_policy.json")
    for name, content in (("pipeline.joblib", model_bytes), ("feature_schema.json", schema_bytes),
                          ("label_map.json", labels_bytes), ("decision_policy.json", policy_bytes)):
        if hashlib.sha256(content).hexdigest() != training["artifacts"][name]["sha256"]:
            raise ValueError("학습 manifest와 선정 산출물의 해시가 다릅니다.")
    if (training.get("status") != "complete" or training.get("converged") is not True or
            training["input_sha256"] != manifest["input_sha256"] or
            training_report["input_sha256"] != manifest["input_sha256"] or
            training["dataset_id"] != protocol["dataset_id"] or training_report["dataset_id"] != protocol["dataset_id"] or
            training_report["fit_scope"] != "train_only" or training_report["test_files_read"] is not False or
            training_report["test_evaluated"] is not False):
        raise ValueError("기존 Train 전용 학습·수렴·입력 provenance를 확인할 수 없습니다.")
    schema = parse_json(schema_bytes.decode("utf-8"))
    labels = parse_json(labels_bytes.decode("utf-8"))
    if (not assess_model_schema(OPERATIONAL_CONTRACT, "message", schema)["schema_compatible"] or
            schema["dataset_id"] != protocol["dataset_id"] or
            schema["feature_dtypes"] != data.schema["feature_dtypes"] or labels != data.label_map or
            schema["preprocessing"].get("fit_scope") != "train_only" or
            schema["preprocessing"].get("kind") != "StandardScaler"):
        raise ValueError("기존 모델과 새 데이터의 feature 의미·순서·단위·전처리 계약이 다릅니다.")
    policy = parse_json(policy_bytes.decode("utf-8"))
    if policy != {"positive_class": 1, "threshold": 0.5, "comparison": ">=",
                  "selection": "fixed_baseline_not_tuned", "deployment_ready": False}:
        raise ValueError("후보 모델의 초기 정책이 예상한 0.5 고정 baseline과 다릅니다.")
    # 직접 학습한 로컬 joblib만 로딩한다. 파일 해시 자체는 pickle의 안전성을 보증하지 않는다.
    pipeline = joblib.load(io.BytesIO(model_bytes))
    if (not isinstance(pipeline, Pipeline) or list(pipeline.named_steps) != ["scaler", "classifier"] or
            not isinstance(pipeline.named_steps["scaler"], StandardScaler) or
            not isinstance(pipeline.named_steps["classifier"], LogisticRegression) or
            list(pipeline.classes_) != [0, 1] or list(pipeline.feature_names_in_) != FEATURE_NAMES):
        raise ValueError("저장 모델의 종류·입력 순서·클래스 순서가 다릅니다.")
    scaler, classifier = pipeline.named_steps["scaler"], pipeline.named_steps["classifier"]
    learned = training_report["learned_parameters"]
    if (int(scaler.n_samples_seen_) != learned["scaler_samples_seen"] or
            int(scaler.n_samples_seen_) != training_report["dataset_splits"]["train"]["rows"] or
            not np.array_equal(scaler.mean_, [learned["scaler_mean"][name] for name in FEATURE_NAMES]) or
            not np.array_equal(scaler.var_, [learned["scaler_variance"][name] for name in FEATURE_NAMES]) or
            not np.array_equal(scaler.scale_, [learned["scaler_scale"][name] for name in FEATURE_NAMES]) or
            not np.array_equal(classifier.coef_[0], [learned["weights"][name] for name in FEATURE_NAMES]) or
            float(classifier.intercept_[0]) != learned["intercept"] or
            classifier.C != 1.0 or
            any(classifier.get_params()[name] != training_report["config"][name]
                for name in ("C", "solver", "max_iter", "tol", "random_state"))):
        raise ValueError("저장한 scaler·가중치·설정이 기존 학습 보고서와 다릅니다.")
    # 새 Train 통계와 비교하거나 다시 fit하지 않는다. 기존 학습 시점의 값이 유지돼야 한다.
    verify_hashes(evidence)
    return pipeline, selection, {"frozen_reference": frozen, "file_sha256": evidence,
                                 "training_dataset_id": protocol["dataset_id"], "scaler_samples_seen": int(scaler.n_samples_seen_)}


def verify_campaign_assignment(dataset_dir: Path, data: OperationalData, frozen: dict) -> tuple[dict, dict]:
    manifest = read_json(dataset_dir / "dataset_manifest.json")
    config = manifest["config_snapshot"]
    if config["split_policy"] != "preassigned_campaign_holdout" or not config.get("campaign_registry"):
        raise ValueError("새 Validation 진단에는 사전에 캠페인 전체를 배정한 데이터셋이 필요합니다.")
    registry_path = Path(config["campaign_registry"]).resolve()
    registry_hash = file_hash(registry_path)
    if manifest["source_file_sha256"][str(registry_path)] != registry_hash:
        raise ValueError("송신 전 캠페인 역할 registry의 SHA-256이 데이터셋과 다릅니다.")
    registry = read_json(registry_path)
    if (registry.get("prepared_before_collection") is not True or
            registry["target_dataset_id"] != data.dataset_id or registry["reference_baseline"] != frozen):
        raise ValueError("수집 전에 고정한 기준 모델·threshold와 현재 평가 대상이 다릅니다.")
    assignments = read_json(dataset_dir / "split_manifest.json")
    roles = {entry["campaign_id"]: entry["split"] for entry in registry["campaigns"]}
    if len(roles) != len(registry["campaigns"]) or any(roles.get(run["campaign_id"]) != run["split"] for run in assignments["runs"]):
        raise ValueError("데이터셋 캠페인 역할이 사전 registry와 다릅니다.")
    validation_entries = [entry for entry in registry["campaigns"] if entry["split"] == "validation"]
    campaign_ids = {entry["campaign_id"] for entry in validation_entries}
    if not campaign_ids or set(data.validation.metadata["campaign_id"]) != campaign_ids:
        raise ValueError("실제 Validation 행과 사전 배정한 캠페인 집합이 다릅니다.")
    excluded = [run for run in manifest["excluded_sources"] if run["campaign_id"] in campaign_ids]
    if any(run.get("scenario_id") != "invalid_frame_size" for run in excluded):
        raise ValueError("관측 불가 헤더 거부 외의 실패 run이 Validation에서 제외되었습니다.")
    planned = [(entry, repeat, profile) for entry in validation_entries
               for repeat, profile, _ in planned_runs(CampaignPlan.model_validate(entry["plan_snapshot"]))]
    observed_runs = {f"{entry['campaign_id']}_r{repeat:02d}_{profile.scenario_id}"
                     for entry, repeat, profile in planned if profile.scenario_id != "invalid_frame_size"}
    header_runs = {f"{entry['campaign_id']}_r{repeat:02d}_{profile.scenario_id}"
                   for entry, repeat, profile in planned if profile.scenario_id == "invalid_frame_size"}
    if set(data.validation.metadata["run_id"]) != observed_runs or {run["run_id"] for run in excluded} != header_runs:
        raise ValueError("Validation에서 계획한 메시지 run 또는 헤더 거부 coverage가 다릅니다.")
    coverage = {"campaigns": len(campaign_ids), "planned_runs": len(planned),
                "message_observable_runs": len(observed_runs), "header_rejection_runs": len(header_runs),
                "planned_sessions": sum(profile.sessions for _, _, profile in planned),
                "message_observable_sessions": len(data.validation.metadata[["run_id", "session_id"]].drop_duplicates()),
                "header_rejection_sessions": sum(profile.sessions for _, _, profile in planned if profile.scenario_id == "invalid_frame_size"),
                "message_rows": len(data.validation.y), "excluded_sources": excluded}
    return coverage, {str(registry_path): registry_hash}


def session_diagnostics(split: OperationalSplit, probability: np.ndarray) -> dict:
    frame = split.metadata.copy()
    frame["label"] = split.y
    frame["predicted"] = (probability >= THRESHOLD).astype("int64")
    frame["probability"] = probability
    frame["connection_duration_ms"] = split.X["connection_duration_ms"].to_numpy()
    frame["request_index"] = frame["request_index"].astype("int64")
    frame["wrong"] = frame.label != frame.predicted
    sessions = []
    for (run_id, session_id), rows in frame.groupby(["run_id", "session_id"], sort=False):
        rows = rows.sort_values("request_index")
        if rows.label.nunique() != 1 or rows.scenario_id.nunique() != 1:
            raise ValueError("같은 세션 안에 정답·시나리오가 혼입되었습니다.")
        positives = rows[rows.predicted == 1]
        first = positives.iloc[0] if len(positives) else None
        sessions.append({"run_id": run_id, "session_id": session_id, "scenario_id": rows.iloc[0].scenario_id,
                         "label": int(rows.iloc[0].label), "message_rows": len(rows), "error_rows": int(rows.wrong.sum()),
                         "misclassified_request_indices": rows.loc[rows.wrong, "request_index"].tolist(),
                         "first_positive_request_index": int(first.request_index) if first is not None else None,
                         "first_positive_connection_duration_ms": float(first.connection_duration_ms) if first is not None else None,
                         "first_positive_elapsed_from_first_message_ms": float(first.connection_duration_ms - rows.iloc[0].connection_duration_ms)
                         if first is not None else None})
    by_scenario = {}
    for session in sessions:
        counts = by_scenario.setdefault(session["scenario_id"], {"sessions": 0, "sessions_with_any_positive": 0,
                                                                "sessions_without_positive": 0, "sessions_with_errors": 0})
        counts["sessions"] += 1
        counts["sessions_with_any_positive" if session["first_positive_request_index"] is not None else "sessions_without_positive"] += 1
        counts["sessions_with_errors"] += int(session["error_rows"] > 0)
    burst = frame[frame.scenario_id == "controlled_burst_chat"]
    prefix = [{"request_index": int(index), "observed_messages": len(rows), "tp": int((rows.predicted == 1).sum()),
               "fn": int((rows.predicted == 0).sum()), "recall": float((rows.predicted == 1).mean())}
              for index, rows in burst.groupby("request_index", sort=True)]
    return {"sessions": sessions, "by_scenario": by_scenario, "burst_by_request_index": prefix,
            "interpretation": "세션·prefix는 메시지 예측의 사후 진단이다. 세션 전체 정답률로 대체하지 않으며 미래 메시지·종료 요약은 입력에 사용하지 않았다. 뒤쪽 prefix는 그 길이까지 관측된 세션만 포함한다."}


def render_report(report: dict) -> str:
    old, new = report["reference_validation_metrics"], report["validation_metrics"]
    coverage = report["validation_coverage"]
    matrix = new["confusion_matrix"]
    lines = ["# 고정 LR의 새 캠페인 Validation 진단", "", "## 평가 조건", "",
             f"기존 LR C=1·threshold={THRESHOLD}·기존 scaler를 그대로 사용해 새 Validation {new['rows']}건만 예측했다.",
             f"기존 scaler는 v1 Train {report['model_evidence']['scaler_samples_seen']}건에서 학습한 값이다. 새 Train이나 Validation으로 다시 fit하지 않았다.",
             "Train CSV는 공통 로더의 형식·행·해시 검사에만 읽었고 모델 예측에는 넣지 않았다. 기존 v1 CSV, Test CSV, 원본 JSONL·종료 요약은 읽지 않았다.",
             "후보 파일의 초기 decision_policy는 0.5지만, 이번 판단은 수집 전에 고정한 selection.json의 0.7을 사용했다.", "",
             "## 기존 선정 Validation과 새 Validation", "", "| 지표 | 기존 v1 | 새 캠페인 |", "| --- | ---: | ---: |"]
    for name in ("rows", "accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "false_positive_rate", "log_loss"):
        lines.append(f"| {name} | {old[name]:.6f} | {new[name]:.6f} |")
    lines += ["", f"새 결과: TN={matrix['tn']}, FP={matrix['fp']}, FN={matrix['fn']}, TP={matrix['tp']}.",
              "FP는 정상 오탐, FN은 통제된 이상 누락이다. 기존 수치는 저장된 선정 보고서 참조이며 이번에 기존 Validation을 재예측하지 않았다.",
              "서로 다른 표본 수·시나리오 비율·설정의 데이터다. 건수 증감이나 점수 차이를 보강 모델의 개선 효과로 해석하지 않는다.", "",
              "## 시나리오별 결과", "", "| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["validation_by_scenario"].items():
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.6f}"
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.6f}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## run별 오분류", "", "| run | 메시지 | FP | FN |", "| --- | ---: | ---: | ---: |"]
    for run_id, values in report["validation_by_run"].items():
        lines.append(f"| {run_id} | {values['rows']} | {values['fp']} | {values['fn']} |")
    lines += ["", "## 세션 진단", "", "| 시나리오 | 세션 | 한 번 이상 양성 | 양성 없음 | 오분류 포함 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for scenario, values in report["session_diagnostics"]["by_scenario"].items():
        lines.append(f"| {scenario} | {values['sessions']} | {values['sessions_with_any_positive']} | {values['sessions_without_positive']} | {values['sessions_with_errors']} |")
    lines += ["", "정상 세션의 양성은 오탐이다. 이상 세션에서 한 번 잡았어도 이전 메시지 누락이 사라지지 않는다.", "",
              "## burst prefix", "", "| 메시지 번호 | 관측 수 | TP | FN | Recall |", "| --- | ---: | ---: | ---: | ---: |"]
    for values in report["session_diagnostics"]["burst_by_request_index"]:
        lines.append(f"| {values['request_index']} | {values['observed_messages']} | {values['tp']} | {values['fn']} | {values['recall']:.6f} |")
    lines += ["", "burst 세션별 첫 양성 메시지·시각은 JSON에 저장했다. 기존 입력만 사용하는 단일 메시지 모델이며 실제 시계열 창을 학습한 결과는 아니다.", "",
              "## 관측 coverage", "",
              f"Validation 캠페인 {coverage['campaigns']}개, 계획 run {coverage['planned_runs']}개·세션 {coverage['planned_sessions']}개 중 메시지 관측 run {coverage['message_observable_runs']}개·세션 {coverage['message_observable_sessions']}개를 평가했다.",
              f"헤더 거부 run {coverage['header_rejection_runs']}개·세션 {coverage['header_rejection_sessions']}개는 메시지 관측이 없어 메시지 성능에서 제외한다. 이를 AI가 탐지했다고 세지 않는다.", "",
              "## 해석", ""] + [f"- {note}" for note in report["findings"]]
    lines += ["", "## 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 추적·재현", "",
              "전체 예측 CSV에는 metadata·입력 7개·정답·확률·예측·오류 종류를 저장한다. errors CSV는 그중 오분류만 보관한다. 입력·모델·사전 역할·코드 SHA-256과 기존 가중치/scaler 보존 검증을 JSON에 남겼다.",
              "", "## 다음 작업", "",
              "새 Train 전용 보강 LR/RF 학습의 제한된 후보·threshold·선택 규칙을 먼저 고정한다. 새 Validation으로 비교하고 Test는 최종 평가까지 보류한다.", ""]
    return "\n".join(lines)


def audit(dataset_dir: Path, dataset_id: str, experiment_dir: Path, report_prefix: Path,
          expected_rows: int = 513) -> dict:
    outputs = {"report": report_prefix.with_suffix(".json"), "markdown": report_prefix.with_suffix(".md"),
               "predictions": report_prefix.with_suffix(".predictions.csv"), "errors": report_prefix.with_suffix(".errors.csv")}
    if any(path.exists() for path in outputs.values()):
        raise FileExistsError("기존 진단 결과를 덮어쓰지 않습니다. 새 --report-prefix를 지정하세요.")
    if any(path.resolve().is_relative_to(root.resolve()) for path in outputs.values() for root in (dataset_dir, experiment_dir)):
        raise ValueError("고정 데이터셋·모델 폴더 안에 평가 결과를 저장하지 않습니다.")
    data = load_operational_data(dataset_dir, dataset_id)
    if len(data.validation.y) != expected_rows:
        raise ValueError(f"새 Validation 행 수가 예상 {expected_rows}건과 다릅니다.")
    pipeline, selection, evidence = load_frozen_model(experiment_dir, data)
    coverage, registry_evidence = verify_campaign_assignment(dataset_dir, data, evidence["frozen_reference"])
    protected = {str(dataset_dir / name): digest for name, digest in data.input_sha256.items()}
    protected.update(evidence["file_sha256"])
    protected.update(registry_evidence)
    parameters_before = joblib.hash(pipeline)
    # 유일한 예측 호출이다. threshold 탐색·새 데이터 fit·Train/Test 예측을 하지 않는다.
    probabilities = pipeline.predict_proba(data.validation.X)
    if (probabilities.shape != (expected_rows, 2) or not np.isfinite(probabilities).all() or
            np.any((probabilities < 0) | (probabilities > 1)) or not np.allclose(probabilities.sum(axis=1), 1)):
        raise ValueError("모델 확률의 형태·범위·유한성 검사가 실패했습니다.")
    probability = probabilities[:, 1]
    predicted = (probability >= THRESHOLD).astype("int64")
    metrics = calculate_metrics(data.validation.y, probability, THRESHOLD)
    by_scenario = scenario_metrics(data.validation, predicted)
    by_run = {}
    for run_id in data.validation.metadata.run_id.unique():
        mask = (data.validation.metadata.run_id == run_id).to_numpy()
        by_run[run_id] = {"rows": int(mask.sum()),
                          "fp": int((mask & (data.validation.y == 0) & (predicted == 1)).sum()),
                          "fn": int((mask & (data.validation.y == 1) & (predicted == 0)).sum())}
    predictions = pd.concat([data.validation.metadata.reset_index(drop=True), data.validation.X.reset_index(drop=True)], axis=1)
    predictions["label"] = data.validation.y
    predictions["anomaly_probability"] = probability
    predictions["predicted_label"] = predicted
    predictions["threshold"] = THRESHOLD
    predictions["error_type"] = np.where(predicted == data.validation.y, "correct",
                                          np.where(predicted == 1, "false_positive", "false_negative"))
    errors = predictions[predictions.error_type != "correct"]
    if len(errors) != metrics["confusion_matrix"]["fp"] + metrics["confusion_matrix"]["fn"]:
        raise ValueError("오분류 CSV 행 수와 confusion matrix가 다릅니다.")
    parameters_unchanged = parameters_before == joblib.hash(pipeline)
    if not parameters_unchanged:
        raise ValueError("진단 도중 저장 모델의 메모리 상태가 변경되었습니다.")
    verify_hashes(protected)
    old_metrics = selection["selected_trial"]["metrics"]
    report = {
        "report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment": "frozen_lr_new_campaign_validation_audit", "dataset_id": data.dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "candidate_id": "lr_c_1", "threshold": THRESHOLD, "threshold_source": "precollection_frozen_selection",
        "evaluated_split": "validation", "model_prediction_calls": 1, "model_prediction_rows": expected_rows,
        "loaded_csv_splits": ["train", "validation"], "train_use": "format_and_integrity_validation_only",
        "train_predicted": False, "model_refit_performed": False, "scaler_refit_performed": False,
        "threshold_changed": False, "threshold_search_performed": False,
        "test_files_read": False, "test_evaluated": False, "legacy_dataset_csv_read": False,
        "raw_jsonl_read": False, "summary_used_as_input_or_label": False,
        "final_model_selected": False, "deployment_ready": False, "model_state_unchanged": parameters_unchanged,
        "input_sha256": data.input_sha256, "model_evidence": evidence, "registry_file_sha256": registry_evidence,
        "validation_metrics": metrics, "validation_by_scenario": by_scenario, "validation_by_run": by_run,
        "reference_validation_metrics": old_metrics, "reference_metrics_source": "stored_selection_not_repredicted",
        "metric_deltas_new_minus_reference": {name: metrics[name] - old_metrics[name]
                                              for name in ("accuracy", "precision", "recall", "f1", "roc_auc", "false_positive_rate")},
        "original_rule_check_diagnostic_only": {
            "max_false_positive_rate": selection["rule"]["max_false_positive_rate"],
            "min_recall": selection["rule"]["min_recall"],
            "meets_fpr_bound": metrics["false_positive_rate"] <= selection["rule"]["max_false_positive_rate"],
            "meets_recall_bound": metrics["recall"] >= selection["rule"]["min_recall"],
            "candidate_reselection_performed": False,
        },
        "validation_coverage": coverage, "session_diagnostics": session_diagnostics(data.validation, probability),
        "findings": [f"새 Validation의 정상 FPR은 {metrics['false_positive_rate']:.2%}, 통제된 이상 Recall은 {metrics['recall']:.2%}다. 모델·threshold는 그대로다."] +
                    [f"{name}: 메시지 {values['rows']}건, FP {values['fp']}건, FN {values['fn']}건."
                     for name, values in by_scenario.items()],
        "limitations": [
            "새 캠페인은 기존 선정 데이터와 다른 실행·seed지만 같은 생성기·Windows 장비·C++ 프로토콜이다. 외부 실제 침입으로의 일반화는 검증하지 않았다.",
            "새 Validation은 캠페인 1개다. 같은 세션의 메시지는 독립 표본이 아니며 513건을 513개 독립 실험으로 해석하지 않는다.",
            "이 결과는 현재 모델의 조건 변화 진단이지 새 데이터로 학습한 모델의 개선 성과가 아니다. 점수 차이만으로 원인을 확정하지 않는다.",
            "메시지 정답은 사전 시나리오 의도다. burst 초기 메시지를 사후 정상으로 바꾸지 않았고, 프로토콜 오류 검출과 AI 판단은 구분한다.",
            "본문 길이 필드는 현재 입력에 없다. LSTM이나 보강 학습이 없는 본문 관측 정보를 자동 복원한다고 가정하지 않는다.",
            "헤더 거부는 메시지 관측 불가로 별도 coverage를 보고한다. 관측 가능한 메시지 성능을 전체 연결의 탐지 성능으로 확대하지 않는다.",
            "threshold 재선정·보강 학습·시계열 모델·최종 Test·지연시간/메모리 비교·FastAPI 실모델 연결은 별도 작업이다.",
        ],
        "code_sha256": {str(path): file_hash(path) for path in (Path(__file__),
                            AI_ROOT / "training" / "operational_data.py", AI_ROOT / "data_collection" / "augmentation.py",
                            AI_ROOT / "training" / "train_operational_logistic_regression.py")},
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                        "scikit_learn": sklearn.__version__, "joblib": joblib.__version__},
    }
    outputs["report"].parent.mkdir(parents=True, exist_ok=True)
    for name, frame in (("predictions", predictions), ("errors", errors)):
        frame.to_csv(outputs[name], index=False, encoding="utf-8", mode="x")
    report["csv_outputs"] = {name: {"path": str(outputs[name]), "rows": len(frame), "sha256": file_hash(outputs[name])}
                             for name, frame in (("predictions", predictions), ("errors", errors))}
    with outputs["markdown"].open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    verify_hashes(protected)
    # 예측 CSV·설명 저장과 입력 보존 확인 후에만 완료 JSON을 기록한다.
    write_json(outputs["report"], report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="기존 LR·threshold 0.7의 새 Validation 진단. 재학습·Test 접근·threshold 탐색은 하지 않습니다.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--dataset-id", default="operational_message_v2_campaign_holdout")
    parser.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_frozen_baseline_audit")
    parser.add_argument("--expected-validation-rows", type=int, default=513)
    args = parser.parse_args()
    try:
        report = audit(args.dataset_dir.resolve(), args.dataset_id, args.experiment_dir.resolve(),
                       args.report_prefix.resolve(), args.expected_validation_rows)
    except (OSError, ValueError, KeyError) as error:
        print(f"[고정 LR 진단] 실패: {error}", file=sys.stderr)
        return 2
    metrics = report["validation_metrics"]
    matrix = metrics["confusion_matrix"]
    print(f"[고정 LR 진단] Validation {metrics['rows']}건 / threshold 0.7 / FP={matrix['fp']}, FN={matrix['fn']}")
    print(f"[고정 LR 진단] F1={metrics['f1']:.6f}, Recall={metrics['recall']:.6f}, FPR={metrics['false_positive_rate']:.6f}")
    print(f"[고정 LR 진단] 재학습·Train 예측·Test 접근 없음 / 보고서: {args.report_prefix.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
