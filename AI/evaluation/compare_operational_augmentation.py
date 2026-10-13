from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from data_collection.augmentation import verify_hashes
from data_collection.quality import inside_path, read_json
from data_preparation.operational_dataset import file_hash
from evaluation.compare_operational_baselines import load_verified_model, prediction_transitions
from training.operational_data import OperationalData
from training.run_operational_validation_experiment import choose_trial, ExperimentConfig
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics


def prefix_diagnostics(data: OperationalData, probability: np.ndarray, threshold: float) -> dict:
    frame = data.validation.metadata.copy()
    frame["predicted"] = (probability >= threshold).astype("int64")
    frame["request_index"] = frame.request_index.astype("int64")
    frame["duration"] = data.validation.X.connection_duration_ms.to_numpy()
    burst = frame[frame.scenario_id == "controlled_burst_chat"]
    sessions = []
    for (run_id, session_id), rows in burst.groupby(["run_id", "session_id"], sort=False):
        rows = rows.sort_values("request_index")
        positives = rows[rows.predicted == 1]
        first = positives.iloc[0] if len(positives) else None
        sessions.append({"run_id": run_id, "session_id": session_id, "message_rows": len(rows),
                         "fn": int((rows.predicted == 0).sum()),
                         "first_positive_request_index": int(first.request_index) if first is not None else None,
                         "first_positive_elapsed_from_first_message_ms": float(first.duration - rows.iloc[0].duration)
                         if first is not None else None})
    return {"burst_sessions": sessions, "burst_by_request_index": [
        {"request_index": int(index), "observed_messages": len(rows), "fn": int((rows.predicted == 0).sum())}
        for index, rows in burst.groupby("request_index", sort=True)],
        "interpretation": "뒤쪽 prefix의 분모는 그 길이까지 관측된 세션뿐이다. 한 번 탐지했다고 이전 누락을 정답으로 바꾸지 않는다."}


def compare(data: OperationalData, config: ExperimentConfig, experiment: dict,
            reference_probability: np.ndarray, reference_model_hash: str) -> tuple[dict, pd.DataFrame]:
    root = config.artifact_dir
    manifest = read_json(root / "experiment_manifest.json")
    if (manifest["status"] != "complete" or manifest["input_sha256"] != data.input_sha256 or
            experiment["input_sha256"] != data.input_sha256 or experiment["protocol"] != config.model_dump(mode="json")):
        raise ValueError("보강 실험의 완료 상태·입력·사전 protocol이 다릅니다.")
    evidence = {str(root / "experiment_manifest.json"): file_hash(root / "experiment_manifest.json")}
    for relative, description in manifest["artifacts"].items():
        evidence[str(inside_path(root, relative))] = description["sha256"]
    evidence.update(manifest["reports"])
    verify_hashes(evidence)
    probability_by_candidate = {}
    candidate_evidence = {}
    for candidate in config.candidates:
        kind = "lr" if candidate.kind == "logistic_regression" else "rf"
        pipeline, training_report, candidate_evidence[candidate.id], saved = load_verified_model(
            root / "candidates" / candidate.id, kind, data)
        probability = pipeline.predict_proba(data.validation.X)[:, 1]
        if (calculate_metrics(data.validation.y, probability, 0.5) != training_report["validation_metrics"] or
                list(saved.row_id) != list(data.validation.metadata.row_id) or
                not np.array_equal(saved.label.to_numpy(), data.validation.y) or
                not np.allclose(saved.anomaly_probability.to_numpy(), probability, rtol=0, atol=1e-14)):
            raise ValueError("보강 후보의 재예측·저장 지표·행 순서가 다릅니다.")
        probability_by_candidate[candidate.id] = probability
        for trial in [trial for trial in experiment["trials"] if trial["candidate_id"] == candidate.id]:
            if calculate_metrics(data.validation.y, probability, trial["threshold"]) != trial["metrics"]:
                raise ValueError("보강 후보의 threshold 지표가 재예측과 다릅니다.")
    selected = choose_trial(experiment["trials"], config.selection_rule)
    if selected != experiment["selection"]["selected_trial"]:
        raise ValueError("보강 선정 결과가 사전 규칙과 다릅니다.")
    best_by_kind = {kind: choose_trial([trial for trial in experiment["trials"] if trial["kind"] == kind], config.selection_rule)
                    for kind in ("logistic_regression", "random_forest")}
    arms = {
        "frozen_lr_0_7": {"candidate_id": "lr_c_1", "kind": "logistic_regression", "threshold": 0.7,
                           "probability": reference_probability, "training_scope": "legacy_v1_train",
                           "model_sha256": reference_model_hash},
        "augmented_lr_c_1_0_7": {"candidate_id": "lr_c_1", "kind": "logistic_regression", "threshold": 0.7,
                                  "probability": probability_by_candidate["lr_c_1"], "training_scope": "new_train_only"},
        "augmented_rf_leaf_1_0_7": {"candidate_id": "rf_leaf_1", "kind": "random_forest", "threshold": 0.7,
                                     "probability": probability_by_candidate["rf_leaf_1"], "training_scope": "new_train_only"},
    }
    for kind, label in (("logistic_regression", "best_lr_validation"), ("random_forest", "best_rf_validation")):
        trial = best_by_kind[kind]
        if trial:
            arms[label] = {"candidate_id": trial["candidate_id"], "kind": kind, "threshold": trial["threshold"],
                           "probability": probability_by_candidate[trial["candidate_id"]], "training_scope": "new_train_only"}
    predictions = data.validation.metadata.copy()
    predictions["label"] = data.validation.y
    old_prediction = (reference_probability >= 0.7).astype("int64")
    models, changed = {}, {}
    for name, arm in arms.items():
        probability, threshold = arm["probability"], arm["threshold"]
        predicted = (probability >= threshold).astype("int64")
        if "model_sha256" not in arm:
            arm["model_sha256"] = file_hash(root / "candidates" / arm["candidate_id"] / "pipeline.joblib")
        models[name] = {key: value for key, value in arm.items() if key != "probability"}
        models[name].update(validation_metrics=calculate_metrics(data.validation.y, probability, threshold),
                            validation_by_scenario=scenario_metrics(data.validation, predicted),
                            prefix_diagnostics=prefix_diagnostics(data, probability, threshold))
        predictions[f"{name}__probability"] = probability
        predictions[f"{name}__prediction"] = predicted
        predictions[f"{name}__threshold"] = threshold
        if name == "frozen_lr_0_7":
            continue
        models[name]["transitions_from_frozen"] = prediction_transitions(data.validation.y, old_prediction, predicted)
        changed[name] = []
        for index in np.flatnonzero(old_prediction != predicted):
            label = int(data.validation.y[index])
            change_type = ("corrected_" if predicted[index] == label else "new_") + ("fp" if label == 0 else "fn")
            changed[name].append({**data.validation.metadata.iloc[index].to_dict(), "label": label,
                                  "change_type": change_type, "frozen_prediction": int(old_prediction[index]),
                                  "augmented_prediction": int(predicted[index]), "frozen_probability": float(reference_probability[index]),
                                  "augmented_probability": float(probability[index]), "augmented_threshold": threshold})
    verify_hashes(evidence)
    selected_arm = ("best_lr_validation" if selected["kind"] == "logistic_regression" else "best_rf_validation") if selected else None
    return {"models": models, "best_by_kind": best_by_kind, "selected_arm": selected_arm,
            "changed_predictions": changed, "candidate_evidence": candidate_evidence, "verified_file_sha256": evidence}, predictions


def render_report(report: dict) -> str:
    rule = report["protocol"]["experiment"]["selection_rule"]
    lines = ["# 보강 LR/RF의 동일 Validation 비교", "", "## 학습 전에 고정한 규칙", "",
             f"Train {report['train_rows']}건, Validation {report['validation_rows']}건, 후보 {report['candidate_count']}개, threshold 조합 {report['trial_count']}개.",
             "LR C=0.1/1/10, RF 최소 leaf 표본 수=1/5/10, threshold=0.3~0.9를 고정했다. LR scaler와 모든 모델은 새 Train으로만 학습했다.",
             f"정상 FPR <= {rule['max_false_positive_rate']:.0%}, 이상 Recall >= {rule['min_recall']:.0%} 중 Recall 최대; 동률은 F1 최대·FPR 최소·후보 id·threshold 오름차순이다.",
             "Validation은 이미 고정 기존 모델의 진단에 사용했다. 이번 학습 전에 protocol을 고정한 것이지 Validation을 보기 전 사전 등록 실험은 아니다.",
             "기존 모델은 그대로 유지하고 Train에 Validation을 합치지 않았다. Test CSV는 해시 검사에도 읽지 않았다.", "",
             "## 같은 Validation의 모델별 결과", "",
             "| 비교 역할 | 후보 | threshold | FP | FN | FPR | Recall | F1 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, model in report["comparison"]["models"].items():
        metrics = model["validation_metrics"]
        matrix = metrics["confusion_matrix"]
        lines.append(f"| {name} | {model['candidate_id']} | {model['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {metrics['recall']:.2%} | {metrics['f1']:.2%} |")
    lines += ["", "고정 0.7 비교는 같은 LR 설정의 재학습 변화와 RF를 구분한다. Validation 최적 조합은 설정·threshold 선택까지 포함하므로 데이터 보강만의 효과로 해석하지 않는다. 같은 threshold가 모델 확률의 같은 보정 수준을 뜻하지는 않는다.",
              "", "## 선정 결과", ""]
    selected = report["selection"]["selected_trial"]
    if selected:
        metrics = selected["metrics"]
        lines += [f"선정 후보 `{selected['candidate_id']}`, threshold `{selected['threshold']}`.",
                  f"Validation FPR {metrics['false_positive_rate']:.2%}, Recall {metrics['recall']:.2%}, F1 {metrics['f1']:.2%}.",
                  "선정은 이번 제한된 후보 집합의 운영 baseline이다. 최종 Test·LSTM·비용 비교 전이므로 배포 모델 확정은 아니다."]
    else:
        lines += ["기준 충족 조합이 없어 미선정으로 완료했다. 기준을 완화하거나 후보를 자동 추가하지 않았다."]
    lines += ["", "## 선정 조합의 시나리오별 결과", "",
              "| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for scenario, values in (selected["validation_by_scenario"].items() if selected else []):
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
        lines.append(f"| {scenario} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## 기존 모델의 오분류 변화", "", "| 비교 역할 | 수정 FP | 수정 FN | 신규 FP | 신규 FN |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, model in report["comparison"]["models"].items():
        if "transitions_from_frozen" not in model:
            continue
        values = model["transitions_from_frozen"]
        lines.append(f"| {name} | {values['corrected_fp']} | {values['corrected_fn']} | {values['new_fp']} | {values['new_fn']} |")
    lines += ["", "변경된 모든 row_id·run/session·정답·확률·판단과 burst prefix를 JSON에 보관한다. 같은 Validation 행의 수정과 새로운 오분류를 분리해 보고한다.",
              "", "## 한계", ""] + [f"- {message}" for message in report["limitations"]]
    lines += ["", "## 저장 및 재현", "",
              f"보강 산출물: `{report['run_dir']}`", "전체 42개 조합과 학습 기록은 선정 보고서에, 동일 행 비교는 비교 CSV와 JSON에 보관한다. 두 단계의 사전 lock과 전체 완료 manifest를 남겼다.",
              "", "## 다음 작업", "", "분리된 각 split의 동일 run/session 내부에서 시계열 창을 구성하고 LSTM 개선 실험을 설계한다. Test는 최종 비교까지 모델에 넣지 않는다.", ""]
    return "\n".join(lines)
