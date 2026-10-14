from __future__ import annotations

import numpy as np
import pandas as pd

from evaluation.compare_operational_augmentation import prefix_diagnostics
from evaluation.compare_operational_baselines import prediction_transitions
from training.operational_data import OperationalData
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics


def coverage_metrics(y: np.ndarray, prediction: np.ndarray, lengths: np.ndarray, width: int) -> dict:
    result = {}
    for name, mask in (("length_one", lengths == 1), ("partial_length_2_to_max_minus_1", (lengths > 1) & (lengths < width)),
                       ("full_window", lengths == width)):
        actual, predicted = y[mask], prediction[mask]
        normal, anomaly = int((actual == 0).sum()), int((actual == 1).sum())
        fp = int(((actual == 0) & (predicted == 1)).sum())
        fn = int(((actual == 1) & (predicted == 0)).sum())
        result[name] = {"rows": len(actual), "normal": normal, "controlled_anomaly": anomaly, "fp": fp, "fn": fn,
                        "false_positive_rate": fp / normal if normal else None,
                        "recall": (anomaly - fn) / anomaly if anomaly else None}
    return result


def compare(data: OperationalData, lengths: np.ndarray, width: int, rf_probability: np.ndarray,
            rf_threshold: float, candidate_probabilities: dict[str, np.ndarray], candidates: list[dict],
            selected: dict | None) -> tuple[dict, pd.DataFrame]:
    arms = {"reference_rf": {"candidate_id": "reference_rf", "threshold": rf_threshold, "probability": rf_probability,
                             "role": "previously_selected_rf"}}
    for candidate in candidates:
        name = candidate["id"]
        arms[f"{name}__default"] = {"candidate_id": name, "threshold": 0.5,
                                    "probability": candidate_probabilities[name], "role": "fixed_default_not_selected"}
        best = candidate["best_feasible_trial"]
        if best:
            arms[f"{name}__best_feasible"] = {"candidate_id": name, "threshold": best["threshold"],
                                              "probability": candidate_probabilities[name], "role": "candidate_best_validation"}
    if selected:
        arms["selected_lstm"] = {"candidate_id": selected["candidate_id"], "threshold": selected["threshold"],
                                 "probability": candidate_probabilities[selected["candidate_id"]], "role": "selected_lstm_validation"}
    frame = data.validation.metadata.copy()
    for feature in data.validation.X.columns:
        frame[feature] = data.validation.X[feature].to_numpy()
    frame["label"], frame["context_length"] = data.validation.y, lengths
    rf_prediction = (rf_probability >= rf_threshold).astype("int64")
    models, changes = {}, {}
    identity_columns = ["row_id", "campaign_id", "run_id", "session_id", "scenario_id", "request_index"]
    for name, arm in arms.items():
        probability, threshold = arm["probability"], arm["threshold"]
        prediction = (probability >= threshold).astype("int64")
        models[name] = {key: value for key, value in arm.items() if key != "probability"}
        models[name].update(validation_metrics=calculate_metrics(data.validation.y, probability, threshold),
                            validation_by_scenario=scenario_metrics(data.validation, prediction),
                            prefix_diagnostics=prefix_diagnostics(data, probability, threshold),
                            coverage=coverage_metrics(data.validation.y, prediction, lengths, width))
        frame[f"{name}__probability"] = probability
        frame[f"{name}__prediction"] = prediction
        frame[f"{name}__threshold"] = threshold
        if name == "reference_rf":
            continue
        models[name]["transitions_from_rf"] = prediction_transitions(data.validation.y, rf_prediction, prediction)
        changes[name] = [{**data.validation.metadata.iloc[index][identity_columns].to_dict(),
                          "label": int(data.validation.y[index]), "context_length": int(lengths[index]),
                          "rf_probability": float(rf_probability[index]), "rf_prediction": int(rf_prediction[index]),
                          "lstm_probability": float(probability[index]), "lstm_prediction": int(prediction[index]),
                          "threshold": threshold,
                          "change_type": ("corrected_" if prediction[index] == data.validation.y[index] else "new_") +
                                         ("fp" if data.validation.y[index] == 0 else "fn")}
                         for index in np.flatnonzero(rf_prediction != prediction)]
    return {"models": models, "changed_predictions": changes,
            "interpretation": "동일한 현재 메시지를 비교한다. RF는 현재 입력, LSTM은 같은 의미의 최근 과거·현재 창을 사용한다. Validation 개발 결과이며 최종 배포 선택은 아니다."}, frame


def render_report(report: dict) -> str:
    selected = report["selection"]["selected_trial"]
    lines = ["# 운영 LSTM 직접 학습과 Validation 비교", "", "## 범위", "",
             f"원본 Train {report['train_rows']}행으로만 scaler를 fit하고 같은 Train 메시지의 시계열 창으로 LSTM 두 후보를 직접 학습했다.",
             f"Validation {report['validation_rows']}건의 현재 메시지에서 기존 선정 RF와 비교했다. Test·원본 JSONL·종료 summary는 읽지 않았다.",
             "Validation은 이미 LR/RF 선정에 사용했다. checkpoint·threshold 선택에 다시 사용한 개발 결과이며 독립 최종 성능이 아니다.", "",
             "## 실제 학습 기록", "", "| 후보 | 사용 epoch | 최적 epoch | 종료 | 최저 Validation 손실 | 파라미터 수 |",
             "| --- | ---: | ---: | --- | ---: | ---: |"]
    for candidate in report["candidates"]:
        training = candidate["training"]
        lines.append(f"| {candidate['id']} | {training['epochs_used']}/{training['max_epochs']} | {training['best_epoch']} | {training['termination']} | {training['best_validation_loss']:.6f} | {training['parameter_count']} |")
    lines += ["", "padding은 유효 길이로 제외하고 마지막 유효 hidden 출력을 사용했다. 창마다 상태를 초기화하며 최적 checkpoint 저장·재로딩 예측도 일치했다.",
              "", "## 모든 threshold 조합", "", "| 후보 | threshold | FP | FN | FPR | Recall | F1 | 제약 충족 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for trial in report["trials"]:
        metrics, matrix = trial["metrics"], trial["metrics"]["confusion_matrix"]
        lines.append(f"| {trial['candidate_id']} | {trial['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {metrics['recall']:.2%} | {metrics['f1']:.2%} | {'충족' if trial['eligible'] else '미충족'} |")
    lines += ["", "## LSTM 후보 선정", "", "FPR 10% 이하·Recall 50% 이상 중 Recall 최대, 동률은 F1 최대·FPR 최소·후보 id·threshold 오름차순이다."]
    if selected:
        lines += [f"선정 LSTM: `{selected['candidate_id']}`·threshold `{selected['threshold']}`. 이는 LSTM 후보 안의 개발 선정이며 기존 RF를 자동 교체하지 않는다."]
    else:
        lines += ["기준 충족 LSTM이 없어 미선정으로 기록했다. 조건을 완화하거나 추가 후보를 자동 학습하지 않았다."]
    lines += ["", "## 같은 Validation의 RF/LSTM 비교", "", "| 비교 역할 | threshold | FP | FN | FPR | Recall | F1 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, model in report["comparison"]["models"].items():
        metrics, matrix = model["validation_metrics"], model["validation_metrics"]["confusion_matrix"]
        lines.append(f"| {name} | {model['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {metrics['recall']:.2%} | {metrics['f1']:.2%} |")
    lines += ["", "## 시나리오별 결과", "", "| 비교 역할 | 시나리오 | 메시지 | FP | FN | Recall | FPR |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    focus = ["reference_rf", "selected_lstm"] if selected else list(report["comparison"]["models"])
    for name in focus:
        for scenario, values in report["comparison"]["models"][name]["validation_by_scenario"].items():
            recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
            fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
            lines.append(f"| {name} | {scenario} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## 길이별 관측 coverage", "", "| 비교 역할 | 구간 | 메시지 | FP | FN |",
              "| --- | --- | ---: | ---: | ---: |"]
    for name in focus:
        for length, values in report["comparison"]["models"][name]["coverage"].items():
            lines.append(f"| {name} | {length} | {values['rows']} | {values['fp']} | {values['fn']} |")
    lines += ["", "모든 원본 메시지를 유지했다. 길이 1인 최초·malformed 메시지에 과거 정보를 만들어 넣지 않았다.",
              "변경 row_id·확률·수정/신규 FP/FN, burst 첫 양성과 request_index별 누락을 JSON과 비교 CSV에 보관한다.",
              "", "## 한계", ""] + [f"- {message}" for message in report["limitations"]]
    lines += ["", "## 다음 작업", "", "후보·threshold를 고정한 뒤 최종 Test와 동일 조건 추론 지연시간·메모리·산출물 비용을 비교해 최종 모델을 선택한다. FastAPI는 아직 dummy 상태다.", ""]
    return "\n".join(lines)
