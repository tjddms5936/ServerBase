from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import pairwise_distances

from data_collection.quality import parse_json, read_json
from data_collection.traffic import SCENARIO_LABELS, chat_frame
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.compare_operational_baselines import load_verified_model
from training.operational_data import OperationalData, load_operational_data
from training.run_operational_validation_experiment import ExperimentConfig, choose_trial
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


DEFAULT_EXPERIMENT_DIR = AI_ROOT / "artifacts" / "operational_message" / "validation_selection_v1"
DEFAULT_PLAN = AI_ROOT / "configs" / "operational_augmentation_plan.json"


def inside_artifact(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("선정 산출물 경로가 실험 폴더 밖을 가리킵니다.")
    return path


def load_selection(root: Path, data: OperationalData) -> tuple[object, dict, dict]:
    manifest_bytes = (root / "experiment_manifest.json").read_bytes()
    manifest = parse_json(manifest_bytes.decode("utf-8"))
    if manifest.get("status") != "complete" or manifest.get("input_sha256") != data.input_sha256:
        raise ValueError("완료된 선정 실험과 현재 Train/Validation 입력 해시가 일치해야 합니다.")
    verified = {"experiment_manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}

    def read_verified(relative: str) -> bytes:
        content = inside_artifact(root, relative).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if manifest["artifacts"][relative]["sha256"] != digest:
            raise ValueError(f"선정 산출물 SHA-256 불일치: {relative}")
        verified[relative] = digest
        return content

    selection = parse_json(read_verified("selection.json").decode("utf-8"))
    lock = parse_json(read_verified("protocol_lock.json").decode("utf-8"))
    protocol = ExperimentConfig.model_validate(lock["protocol"])
    digest = hashlib.sha256(json.dumps(lock["protocol"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    if (digest != lock["protocol_sha256"] or digest != manifest["protocol_sha256"] or
            digest != selection["protocol_sha256"] or lock["input_sha256"] != data.input_sha256 or
            protocol.dataset_id != data.dataset_id or selection.get("feature_order") != FEATURE_NAMES or
            selection.get("feature_contract_id") != data.schema["feature_contract_id"]):
        raise ValueError("선정 protocol·데이터셋·feature 계약이 일치하지 않습니다.")
    if selection.get("status") != "selected" or selection.get("selected_trial") is None:
        raise ValueError("선정 모델이 없습니다. 미선정 실험에 대한 모델 오분류 분석은 실행하지 않습니다.")
    selected = selection["selected_trial"]
    candidate = next((item for item in protocol.candidates if item.id == selected["candidate_id"]), None)
    if (candidate is None or candidate.kind != selected["kind"] or selected["threshold"] not in protocol.thresholds or
            selection["threshold"] != selected["threshold"] or selection.get("positive_class") != 1 or
            selection.get("comparison") != ">=" or selection.get("refit_with_validation") is not False or
            selection.get("test_evaluated") is not False or selection.get("rule") != protocol.selection_rule.model_dump() or
            selection.get("label_map") != data.label_map):
        raise ValueError("선정 후보·threshold·학습 범위가 protocol과 다릅니다.")
    expected_model = f"candidates/{candidate.id}/pipeline.joblib"
    if (selection["model_path"] != expected_model or
            selection["feature_schema_path"] != f"candidates/{candidate.id}/feature_schema.json" or
            selection["label_map_path"] != f"candidates/{candidate.id}/label_map.json"):
        raise ValueError("선정 모델·schema·정답 경로와 후보 id가 다릅니다.")
    model_prefix = f"candidates/{candidate.id}"
    for name in ("pipeline.joblib", "training_manifest.json", "feature_schema.json", "label_map.json",
                 "decision_policy.json", "validation_predictions.csv"):
        read_verified(f"{model_prefix}/{name}")
    read_verified(f"candidate_reports/{candidate.id}.json")
    if verified[expected_model] != selection["model_sha256"]:
        raise ValueError("선정 모델의 기록 해시와 실제 파일이 다릅니다.")
    kind = "lr" if candidate.kind == "logistic_regression" else "rf"
    pipeline, _, model_evidence, _ = load_verified_model(root / model_prefix, kind, data)
    if model_evidence["training_manifest_sha256"] != verified[f"{model_prefix}/training_manifest.json"]:
        raise ValueError("검증 도중 후보 학습 manifest가 변경되었습니다.")
    report_paths = [(Path(path), value) for path, value in manifest["reports"].items() if Path(path).suffix == ".json"]
    if len(report_paths) != 1:
        raise ValueError("선정 실험 JSON 보고서가 정확히 하나 필요합니다.")
    report_path, report_digest = report_paths[0]
    report_content = report_path.read_bytes()
    if hashlib.sha256(report_content).hexdigest() != report_digest:
        raise ValueError("선정 실험 보고서 SHA-256이 일치하지 않습니다.")
    experiment = parse_json(report_content.decode("utf-8"))
    if (experiment["selection"] != selection or experiment["input_sha256"] != data.input_sha256 or
            experiment["protocol"] != lock["protocol"] or
            choose_trial(experiment["trials"], protocol.selection_rule) != selected):
        raise ValueError("선정 결과와 전체 조합·사전 규칙이 일치하지 않습니다.")
    probability = pipeline.predict_proba(data.validation.X)[:, 1]
    predicted = (probability >= selection["threshold"]).astype("int64")
    if calculate_metrics(data.validation.y, probability, selection["threshold"]) != selected["metrics"]:
        raise ValueError("선정 threshold 재예측 결과와 저장 지표가 다릅니다.")
    saved = pd.read_csv(io.BytesIO(read_verified("selected_validation_predictions.csv")), dtype={"row_id": str})
    expected_error = np.where(predicted == data.validation.y, "correct", np.where(predicted == 1, "false_positive", "false_negative"))
    if (list(saved["row_id"]) != list(data.validation.metadata["row_id"]) or
            not np.array_equal(saved["label"], data.validation.y) or not np.array_equal(saved["predicted_label"], predicted) or
            not np.allclose(saved["anomaly_probability"], probability, rtol=0, atol=1e-14) or
            not np.array_equal(saved["error_type"], expected_error)):
        raise ValueError("선정 메시지별 예측·정답·행 순서가 일치하지 않습니다.")
    return pipeline, selection, {"verified_artifacts": verified, "model": model_evidence,
                                  "experiment_report_path": str(report_path), "experiment_report_sha256": report_digest}


def numeric_summary(values) -> dict:
    array = np.asarray(values, dtype="float64")
    if not len(array):
        return {"rows": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    return {"rows": len(array), "minimum": float(array.min()), "median": float(np.median(array)),
            "mean": float(array.mean()), "maximum": float(array.max())}


def feature_diagnostics(data: OperationalData) -> dict:
    result = {}
    for scenario in sorted(data.validation.metadata["scenario_id"].unique()):
        train = data.train.X.loc[data.train.metadata["scenario_id"] == scenario]
        validation = data.validation.X.loc[data.validation.metadata["scenario_id"] == scenario]
        features = {}
        for name in FEATURE_NAMES:
            low, high = train[name].min(), train[name].max()
            outside = int(((validation[name] < low) | (validation[name] > high)).sum()) if len(train) else None
            features[name] = {"train": numeric_summary(train[name]), "validation": numeric_summary(validation[name]),
                              "validation_outside_same_scenario_train_range": outside}
        result[scenario] = {"train_rows": len(train), "validation_rows": len(validation), "features": features}
    observed = pd.concat([data.train.metadata[["feature_hash"]].assign(label=data.train.y),
                          data.validation.metadata[["feature_hash"]].assign(label=data.validation.y)], ignore_index=True)
    groups = observed.groupby("feature_hash")["label"].nunique()
    return {"by_scenario": result, "observed_exact_feature_conflicting_label_groups": int((groups > 1).sum()),
            "constant_train_features": [name for name in FEATURE_NAMES if data.train.X[name].nunique() == 1],
            "packet_payload_difference_always_8": bool(((data.train.X.packet_size - data.train.X.payload_size) == 8).all() and
                                                        ((data.validation.X.packet_size - data.validation.X.payload_size) == 8).all())}


def representation_probe(pipeline, threshold: float) -> dict:
    # 실제 네트워크 실험이 아니라 같은 관측값에 다른 본문을 넣을 수 있음을 보이는 구성 반례다.
    normal = chat_frame(b"representation_probe", 17)
    malformed = normal[:8] + struct.pack("!H", 60000) + normal[10:]
    payload_size = len(normal) - 8
    features = {"packet_size": len(normal), "payload_size": payload_size, "connection_duration_ms": 1.0,
                "message_interval_ms": 0.0, "bytes_received_total": len(normal), "bytes_sent_total": 0, "error_count_total": 0}
    X = pd.DataFrame([features, features], columns=FEATURE_NAMES, dtype="float64")
    probability = pipeline.predict_proba(X)[:, 1]
    return {"probe_kind": "constructed_representation_counterexample_not_captured_traffic",
            "wire_size_equal": len(normal) == len(malformed), "wire_bytes_equal": normal == malformed,
            "normal_wire_sha256": hashlib.sha256(normal).hexdigest(), "malformed_wire_sha256": hashlib.sha256(malformed).hexdigest(),
            "normal_declared_text_length": struct.unpack("!H", normal[8:10])[0],
            "malformed_declared_text_length": struct.unpack("!H", malformed[8:10])[0],
            "available_text_bytes_after_fixed_fields": payload_size - 6, "identical_feature_vector": features,
            "anomaly_probabilities": probability.tolist(), "predicted_labels": (probability >= threshold).astype("int64").tolist(),
            "same_model_output": bool(probability[0] == probability[1]),
            "interpretation": "일곱 입력을 같게 고정하면 본문 길이 필드의 차이는 전달되지 않는다. 실제 수집된 두 행이 완전히 같았다는 증거는 아니다."}


def error_cases(data: OperationalData, pipeline, threshold: float, probability: np.ndarray) -> tuple[pd.DataFrame, list[dict]]:
    predicted = (probability >= threshold).astype("int64")
    wrong = np.flatnonzero(predicted != data.validation.y)
    mean, scale = data.train.X.mean().to_numpy(), data.train.X.std(ddof=0).to_numpy()
    scale = np.where(scale == 0, 1.0, scale)
    normalized_train = (data.train.X.to_numpy() - mean) / scale
    distances = (pairwise_distances((data.validation.X.iloc[wrong].to_numpy() - mean) / scale, normalized_train)
                 if len(wrong) else np.empty((0, len(data.train.y))))
    linear = isinstance(pipeline.named_steps["classifier"], LogisticRegression)
    contributions = None
    scores, cutoff = None, None
    if linear:
        classifier = pipeline.named_steps["classifier"]
        contributions = pipeline.named_steps["scaler"].transform(data.validation.X) * classifier.coef_[0]
        scores = contributions.sum(axis=1) + classifier.intercept_[0]
        if not np.allclose(scores, pipeline.decision_function(data.validation.X)):
            raise ValueError("가중치 기여도 합과 LR 점수가 일치하지 않습니다.")
        cutoff = math.log(threshold / (1 - threshold))
    cases = []
    for position, index in enumerate(wrong):
        metadata = data.validation.metadata.iloc[index]
        opposite = np.flatnonzero(data.train.y != data.validation.y[index])
        nearest = int(opposite[np.argmin(distances[position, opposite])])
        case = {**metadata.to_dict(), "label": int(data.validation.y[index]), "predicted_label": int(predicted[index]),
                "error_type": "false_positive" if predicted[index] else "false_negative",
                "anomaly_probability": float(probability[index]), "threshold": threshold,
                "probability_minus_threshold": float(probability[index] - threshold),
                "features": data.validation.X.iloc[index].to_dict(),
                "minimum_chat_payload_violation": bool(data.validation.X.iloc[index].payload_size < 6),
                "minimum_payload_scope": "현재 캠페인의 CS_CHAT만 해당하며 정답이나 모델 입력을 바꾸는 규칙이 아니다.",
                "nearest_opposite_label_train_example": {
                    "row_id": data.train.metadata.iloc[nearest].row_id,
                    "scenario_id": data.train.metadata.iloc[nearest].scenario_id, "label": int(data.train.y[nearest]),
                    "standardized_euclidean_distance": float(distances[position, nearest]),
                    "features": data.train.X.iloc[nearest].to_dict(),
                },
                "lr_intercept": float(pipeline.named_steps["classifier"].intercept_[0]) if linear else None,
                "lr_score": float(scores[index]) if linear else None, "threshold_logit": cutoff,
                "feature_contributions": dict(zip(FEATURE_NAMES, contributions[index].tolist())) if linear else None,
            }
        cases.append(case)
    csv = data.validation.metadata.iloc[wrong].reset_index(drop=True).copy()
    csv = pd.concat([csv, data.validation.X.iloc[wrong].reset_index(drop=True)], axis=1)
    csv["label"] = data.validation.y[wrong]
    csv["predicted_label"] = predicted[wrong]
    csv["error_type"] = [case["error_type"] for case in cases]
    csv["anomaly_probability"] = probability[wrong]
    csv["threshold"] = threshold
    csv["probability_minus_threshold"] = probability[wrong] - threshold
    csv["minimum_chat_payload_violation"] = [case["minimum_chat_payload_violation"] for case in cases]
    return csv, cases


def session_diagnostics(data: OperationalData, probability: np.ndarray, threshold: float) -> dict:
    frame = data.validation.metadata.copy()
    frame["label"] = data.validation.y
    frame["predicted"] = (probability >= threshold).astype("int64")
    frame["request_index"] = frame.request_index.astype("int64")
    frame["wrong"] = frame.label != frame.predicted
    sessions = []
    for (run_id, session_id), rows in frame.groupby(["run_id", "session_id"], sort=False):
        rows = rows.sort_values("request_index")
        positives = rows.loc[rows.predicted == 1, "request_index"].tolist()
        sessions.append({"run_id": run_id, "session_id": session_id, "scenario_id": rows.iloc[0].scenario_id,
                         "message_rows": len(rows), "error_rows": int(rows.wrong.sum()),
                         "misclassified_request_indices": rows.loc[rows.wrong, "request_index"].tolist(),
                         "first_positive_request_index": int(positives[0]) if positives else None})
    burst = frame[frame.scenario_id == "controlled_burst_chat"]
    prefix = [{"request_index": int(index), "rows": len(rows), "fn": int(((rows.label == 1) & (rows.predicted == 0)).sum())}
              for index, rows in burst.groupby("request_index", sort=True)]
    return {"sessions": sessions, "burst_by_request_index": prefix,
            "interpretation": "세션·prefix는 진단용 묶음이다. 메시지 정답·평가 지표를 세션 단위로 바꾸거나 미래 정보를 입력에 합치지 않았다."}


def augmentation_design(path: Path, selection: dict) -> dict:
    plan = read_json(path)
    slots = plan["campaign_slots"]
    ids, seeds = [slot["slot_id"] for slot in slots], [slot["seed"] for slot in slots]
    limits = plan["safety_limits"]
    if (plan.get("status") != "design_only" or len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)) or
            any(type(seed) is not int or not 0 <= seed <= 2**31 - 1 for seed in seeds) or
            {slot["split"] for slot in slots} != {"train", "validation", "test"} or
            plan["feature_contract_id"] != selection["feature_contract_id"] or limits["auto_collect_now"] is not False or
            limits["external_traffic_allowed"] is not False or limits["destination"] != "127.0.0.1:4000" or
            plan["campaign_split_policy"] != "preassigned_campaign_holdout" or
            plan["freeze_reference_before_collection"] is not True or
            len(plan["scenario_ids"]) != len(set(plan["scenario_ids"])) or set(plan["scenario_ids"]) != set(SCENARIO_LABELS) or
            type(plan["repeats_per_campaign"]) is not int or not 1 <= plan["repeats_per_campaign"] <= 10 or
            type(plan["sessions_per_run"]) is not int or not 1 <= plan["sessions_per_run"] <= 4):
        raise ValueError("보강 계획은 고유 캠페인·seed·사전 분할·미실행 상태를 유지해야 합니다.")
    runs = len(slots) * plan["repeats_per_campaign"] * len(plan["scenario_ids"])
    no_message_runs = len(slots) * plan["repeats_per_campaign"] * int("invalid_frame_size" in plan["scenario_ids"])
    return {"status": "design_only", "plan_path": str(path), "plan_sha256": file_hash(path), "plan": plan,
            "planned_campaigns": len(slots), "planned_runs": runs,
            "planned_sessions_including_header_rejection": runs * plan["sessions_per_run"],
            "planned_message_observable_sessions": (runs - no_message_runs) * plan["sessions_per_run"],
            "planned_header_rejection_runs": no_message_runs,
            "actual_new_campaigns_collected": 0,
            "frozen_reference": {"candidate_id": selection["selected_trial"]["candidate_id"],
                                 "model_sha256": selection["model_sha256"], "threshold": selection["threshold"]}}


def render_report(report: dict) -> str:
    metrics = report["validation_metrics"]
    matrix = metrics["confusion_matrix"]
    design = report["augmentation_design"]
    lines = ["# 선정 운영 모델 오분류·관측 한계 분석", "", "## 분석 범위", "",
             f"선정 후보 `{report['candidate_id']}`, threshold={report['threshold']}의 Validation {metrics['rows']}건을 재예측했다.",
             f"TN={matrix['tn']}, FP={matrix['fp']}, FN={matrix['fn']}, TP={matrix['tp']}. 모델·threshold를 변경하거나 재학습하지 않았다.",
             "Test는 읽지 않았다. 원본 JSONL이나 종료 summary를 모델 입력에 합치지 않고 보존한 row_id·run/session·원본 행 번호로 추적한다.", "",
             "## 시나리오별 오분류", "", "| 시나리오 | 메시지 | FP | FN |", "| --- | ---: | ---: | ---: |"]
    for name, values in report["validation_by_scenario"].items():
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} |")
    lines += ["", "## burst prefix 진단", "", "| 메시지 번호 | 관측 수 | FN |", "| --- | ---: | ---: |"]
    for values in report["session_diagnostics"]["burst_by_request_index"]:
        lines.append(f"| {values['request_index']} | {values['rows']} | {values['fn']} |")
    lines += ["", "## 모든 오분류 추적", "",
              "| 종류 | 시나리오 | 세션 | 메시지 | 이상 확률 | payload 바이트 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for case in report["error_cases"]:
        lines.append(f"| {case['error_type']} | {case['scenario_id']} | {case['session_id']} | {case['request_index']} | {case['anomaly_probability']:.6f} | {case['features']['payload_size']:.0f} |")
    lines += ["", "CSV에는 전체 식별자·원본 행 번호·입력 7개·정답·예측·확률·threshold를 저장한다. JSON은 LR 가중치 기여도와 반대 정답의 가까운 Train 예시도 보관한다. 기여도와 거리는 인과관계나 프로토콜 동등성 증거가 아니다.", "",
              "## 관측 한계", ""] + [f"- {finding}" for finding in report["observability_findings"]]
    lines += ["", "## 본문 표현의 구성 반례", "",
              "정상 프레임의 문자열 길이 필드만 60000으로 바꿨다. 전체 프레임 크기는 같고, 시각·카운터를 같게 고정하면 입력 7개와 모델 출력도 같다.",
              "이것은 모델에 전달되지 않는 정보의 구성 반례다. 실제 두 수집 행이 완전히 같거나 모델이 모든 malformed 메시지를 탐지할 수 없다는 뜻은 아니다.", "",
              "## 독립 데이터 보강 설계", "",
              f"계획은 캠페인 {design['planned_campaigns']}개, run {design['planned_runs']}개, 세션 {design['planned_sessions_including_header_rejection']}개(헤더 거부 포함)다. 새로 수집한 캠페인은 0개다.",
              "캠페인 전체를 사전에 Train 3개·Validation 1개·Test 2개로 배치한다. 새 seed·긴 정상 세션·타이밍·묶음/분할 크기·동시 연결 수준을 교차 배치한다.",
              "현재 생성기와 데이터셋 분리기에 새 설정·캠페인 holdout 구현이 필요하다. 이 JSON을 기존 수집기에 바로 전달해 실행하는 계획이 아니다.",
              "프로토콜 유효성은 기존 C++ 검증 경로로 다루고 AI의 행동 이상 판단과 구분한다. 새 관측 필드가 필요하면 의미 버전·판단 시점·재수집을 별도 설계한다.", "",
              "## 해석 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 다음 작업", "",
              "설계한 생성기 설정과 캠페인 단위 분리를 구현하고 새 캠페인을 수집·검증한다. 이후 새 Validation에서 고정 baseline을 진단하고 새 Train 전용 보강 실험을 진행한다. Test는 최종 평가까지 보류한다.", ""]
    return "\n".join(lines)


def analyze(dataset_dir: Path, dataset_id: str, experiment_dir: Path, plan_path: Path, report_prefix: Path) -> dict:
    outputs = [report_prefix.with_suffix(extension) for extension in (".json", ".md", ".csv")]
    if any(path.exists() for path in outputs):
        raise FileExistsError("기존 오분류 보고서는 덮어쓰지 않습니다. 새 report-prefix를 지정하세요.")
    if any(path.resolve().is_relative_to(root.resolve()) for path in outputs for root in (dataset_dir, experiment_dir)):
        raise ValueError("고정 데이터셋·선정 실험 내부에 분석 결과를 저장할 수 없습니다.")
    data = load_operational_data(dataset_dir, dataset_id)
    pipeline, selection, evidence = load_selection(experiment_dir, data)
    probability = pipeline.predict_proba(data.validation.X)[:, 1]
    threshold = selection["threshold"]
    csv, cases = error_cases(data, pipeline, threshold, probability)
    diagnostics = feature_diagnostics(data)
    design = augmentation_design(plan_path, selection)
    source_paths = [AI_ROOT / "configs" / "operational_feature_schema.json", AI_ROOT / "data_collection" / "traffic.py",
                    AI_ROOT.parent / "Server" / "ServerBase" / "Session.cpp",
                    AI_ROOT.parent / "Server" / "Server" / "ServerSession.cpp",
                    AI_ROOT.parent / "Server" / "FeatureExtraction" / "FeatureCollector.cpp",
                    AI_ROOT.parent / "Server" / "ServerBase" / "Protocol.h",
                    AI_ROOT.parent / "Server" / "ServerBase" / "MemoryStream.h"]
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(), "dataset_id": dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "candidate_id": selection["selected_trial"]["candidate_id"],
        "threshold": threshold, "analysis_split": "validation", "model_refit_performed": False,
        "threshold_changed": False, "test_files_read": False, "test_evaluated": False,
        "raw_jsonl_read": False, "summary_used_as_input_or_label": False,
        "input_sha256": data.input_sha256, "selection_evidence": evidence,
        "validation_metrics": calculate_metrics(data.validation.y, probability, threshold),
        "validation_by_scenario": scenario_metrics(data.validation, (probability >= threshold).astype("int64")),
        "error_cases": cases, "session_diagnostics": session_diagnostics(data, probability, threshold),
        "feature_diagnostics": diagnostics, "representation_probe": representation_probe(pipeline, threshold),
        "augmentation_design": design,
        "source_sha256": {str(path): file_hash(path) for path in source_paths},
        "code_sha256": {str(path): file_hash(path) for path in (
            Path(__file__), AI_ROOT / "training" / "operational_data.py",
            AI_ROOT / "evaluation" / "compare_operational_baselines.py",
            AI_ROOT / "training" / "train_operational_logistic_regression.py",
            AI_ROOT / "training" / "run_operational_validation_experiment.py")},
        "environment": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
        "observability_findings": [
            "malformed_chat_payload의 payload 1바이트는 CS_CHAT의 최소 고정 필드 6바이트보다 짧다. 크기 단서는 현재 입력에 있지만 모델의 누락과 프로토콜 검증 실패는 별개다.",
            "malformed_length_chat은 같은 크기의 정상 본문에서 문자열 길이 필드만 바꿀 수 있다. 그 필드·본문 유효성은 현재 입력 7개에 없다.",
            "PacketParsed와 FeatureCollector의 snapshot은 ServerSession의 본문 역직렬화 전에 생성된다. 이후 오류나 종료 summary를 현재 error_count_total에 가져오면 판단 시점 누출이다.",
            "첫 메시지 간격은 0이고 짧은 이상 세션은 메시지 하나뿐이다. 시계열 모델도 없는 과거 정보나 본문 필드를 복원할 수 없다.",
            "묶음 전송의 파싱 간격은 실제 TCP 패킷 도착 간격과 다르다. 정상 묶음도 짧은 간격·동일 수신 누적값을 보일 수 있다.",
            "같은 시나리오의 Train/Validation 시간 범위를 별도로 비교한다. 전체 Train 범위 안에 있어도 시나리오별 분포 차이는 남을 수 있다.",
            "invalid_frame_size는 메시지 snapshot이 없으므로 메시지 모델 평가 대상 밖이다. 이미 적용한 헤더 거부 경로와 별도의 coverage로 구분한다.",
        ],
        "limitations": [
            "이미 선정에 사용한 Validation의 사후 진단이다. 독립적인 최종 성능 또는 원인 확정으로 해석하지 않는다.",
            "시나리오별 표본과 독립 세션 수가 작다. 요약 범위 차이는 통계적 분포 이동 검정이 아니다.",
            "LR 기여도는 표준화 입력·현재 가중치의 계산 분해이며 중복 입력과 상관관계를 포함한다. 기여도가 실제 원인이라는 뜻은 아니다.",
            "같은 feature의 실제 다른 정답 관측 여부와 구성 반례를 구분한다. 실제 feature 충돌이 0이어도 충분한 관측 정보를 보장하지 않는다.",
            "보강 계획은 아직 실행하지 않았다. 새 캠페인·seed 분리만으로 실제 공격 도메인이나 생성기 편향이 해결되지는 않는다.",
            "프로토콜 검증 규칙으로 본문 오류를 검출하는 것을 AI 모델 개선 성과로 대신 주장하지 않는다.",
        ],
    }
    outputs[0].parent.mkdir(parents=True, exist_ok=True)
    csv.to_csv(outputs[2], index=False, encoding="utf-8", mode="x")
    report["error_csv"] = {"path": str(outputs[2]), "rows": len(csv), "sha256": file_hash(outputs[2])}
    write_json(outputs[0], report)
    with outputs[1].open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="선정 모델 Validation 오분류·관측 한계 분석 및 독립 수집 설계. 재학습·Test 평가는 하지 않습니다.")
    parser.add_argument("--dataset-dir", type=Path, default=AI_ROOT / "data" / "processed" / "operational_message" / "v1")
    parser.add_argument("--dataset-id", default="operational_message_v1")
    parser.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT_DIR)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_error_analysis")
    args = parser.parse_args()
    try:
        report = analyze(args.dataset_dir.resolve(), args.dataset_id, args.experiment_dir.resolve(), args.plan.resolve(), args.report_prefix.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"[운영 분석] 실패: {error}", file=sys.stderr)
        return 2
    matrix = report["validation_metrics"]["confusion_matrix"]
    print(f"[운영 분석] FP={matrix['fp']}, FN={matrix['fn']} / 오분류 {len(report['error_cases'])}건 추적")
    print(f"[운영 분석] 보강 설계 {report['augmentation_design']['planned_campaigns']}개 캠페인 / 실제 새 수집 0개 / Test 미사용")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
