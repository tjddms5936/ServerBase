from __future__ import annotations

import math

import numpy as np
import pandas as pd

from evaluation.compare_operational_baselines import prediction_transitions
from evaluation.compare_operational_augmentation import prefix_diagnostics
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics


def rejection_reasons(trial: dict, criteria: dict) -> list[str]:
    metrics = trial["metrics"]
    batch = trial["by_scenario"].get("normal_batched_chat", {})
    reasons = []
    for name, value, limit, upper in (
        ("전체 정상 FPR", metrics.get("false_positive_rate"), criteria["max_false_positive_rate"], True),
        ("정상 묶음 FPR", batch.get("false_positive_rate"), criteria["max_normal_batched_false_positive_rate"], True),
        ("이상 Recall", metrics.get("recall"), criteria["min_recall"], False),
    ):
        if value is None or not math.isfinite(value):
            reasons.append(f"{name}을 검증할 수 없음")
        elif (upper and value > limit) or (not upper and value < limit):
            reasons.append(f"{name} 기준 미달")
    return reasons


def choose_trial(trials: list[dict], criteria: dict) -> dict | None:
    feasible = [trial for trial in trials if not rejection_reasons(trial, criteria)]
    if not feasible:
        return None
    # 수집 전에 고정한 순서를 그대로 적용한다. 비용과 Test 승인은 별도 단계다.
    return min(feasible, key=lambda trial: (-trial["metrics"]["recall"], -trial["metrics"]["f1"],
                                           trial["metrics"]["false_positive_rate"], trial["candidate_id"], trial["threshold"]))


def message_position_counts(source, prediction: np.ndarray) -> dict:
    first = source.metadata.request_index.astype("int64").to_numpy() == 1
    result = {}
    for name, selected in (("first_message", first), ("later_messages", ~first)):
        y, predicted = source.y[selected], prediction[selected]
        normal, anomaly = int((y == 0).sum()), int((y == 1).sum())
        fp, fn = int(((y == 0) & (predicted == 1)).sum()), int(((y == 1) & (predicted == 0)).sum())
        result[name] = {"rows": len(y), "normal": normal, "anomaly": anomaly, "fp": fp, "fn": fn,
                        "fpr": fp / normal if normal else None, "recall": (anomaly - fn) / anomaly if anomaly else None}
    return result


def make_trial(candidate: dict, source, probability: np.ndarray, threshold: float, criteria: dict) -> dict:
    prediction = (probability >= threshold).astype("int64")
    trial = {**candidate, "threshold": threshold, "metrics": calculate_metrics(source.y, probability, threshold),
             "by_scenario": scenario_metrics(source, prediction), "by_message_position": message_position_counts(source, prediction)}
    trial["rejection_reasons"] = rejection_reasons(trial, criteria)
    trial["validation_feasible"] = not trial["rejection_reasons"]
    return trial


def compare(data, candidates: list[dict], probabilities: dict[str, np.ndarray], reference_probability: np.ndarray,
            trials: list[dict], criteria: dict, prefix: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    selected = choose_trial(trials, criteria)
    best_by_candidate = {item["id"]: choose_trial([trial for trial in trials if trial["candidate_id"] == item["id"]], criteria)
                         for item in candidates}
    arms = {
        "frozen_reference_0_3": {"candidate_id": "rf_leaf_5", "threshold": 0.3, "input_transform_id": "raw7_v1",
                                  "probability": reference_probability, "training_scope": "previous_train_only"},
        "new_raw7_0_3": {"candidate_id": "rf_raw7", "threshold": 0.3, "input_transform_id": "raw7_v1",
                           "probability": probabilities["rf_raw7"], "training_scope": "new_train_only"},
    }
    for item in candidates:
        arms[f"{item['id']}__default_0_5"] = {"candidate_id": item["id"], "threshold": 0.5,
            "input_transform_id": item["input_transform_id"], "probability": probabilities[item["id"]],
            "training_scope": "new_train_only"}
        best = best_by_candidate[item["id"]]
        if best is not None:
            arms[f"{item['id']}__best_feasible"] = {"candidate_id": item["id"], "threshold": best["threshold"],
                "input_transform_id": item["input_transform_id"], "probability": probabilities[item["id"]],
                "training_scope": "new_train_only"}
    if selected is not None:
        arms["selected_validation_policy"] = {"candidate_id": selected["candidate_id"], "threshold": selected["threshold"],
            "input_transform_id": selected["input_transform_id"], "probability": probabilities[selected["candidate_id"]],
            "training_scope": "new_train_only"}
    frame = pd.concat([data.validation.metadata.copy(), data.validation.X.copy(),
                       prefix[["prefix_mean_interval_ms", "parsed_bytes_prefix_total"]]], axis=1)
    frame["label"] = data.validation.y
    reference_prediction = (reference_probability >= 0.3).astype("int64")
    models, changes = {}, {}
    for name, arm in arms.items():
        probability, threshold = arm["probability"], arm["threshold"]
        prediction = (probability >= threshold).astype("int64")
        model = {key: value for key, value in arm.items() if key != "probability"}
        model.update(metrics=calculate_metrics(data.validation.y, probability, threshold),
                     by_scenario=scenario_metrics(data.validation, prediction),
                     by_message_position=message_position_counts(data.validation, prediction),
                     burst_prefix=prefix_diagnostics(data, probability, threshold))
        if name != "frozen_reference_0_3":
            model["transitions_from_reference"] = prediction_transitions(data.validation.y, reference_prediction, prediction)
            changes[name] = []
            for index in np.flatnonzero(reference_prediction != prediction):
                label = int(data.validation.y[index])
                changes[name].append({"row_id": data.validation.metadata.row_id.iloc[index], "label": label,
                    "change_type": ("corrected_" if prediction[index] == label else "new_") + ("fp" if label == 0 else "fn"),
                    "reference_prediction": int(reference_prediction[index]), "prediction": int(prediction[index]),
                    "reference_probability": float(reference_probability[index]), "probability": float(probability[index])})
        models[name] = model
        frame[f"{name}__probability"] = probability
        frame[f"{name}__prediction"] = prediction
        frame[f"{name}__threshold"] = threshold
    return {"models": models, "changed_predictions": changes, "best_by_candidate": best_by_candidate,
            "selected_trial": selected, "cost_measured": False, "independent_test_confirmed": False}, frame


def render_report(report: dict, comparison_only: bool = False) -> str:
    lines = ["# 보정 RF의 새 Validation 비교" if comparison_only else "# 보정 RF 직접 학습과 Validation 선정", "",
             "## 고정 실험 조건", "",
             f"새 Train {report['train_rows']}건으로 후보 {report['candidate_count']}개를 각각 한 번 학습하고 새 Validation {report['validation_rows']}건에서 {report['trial_count']}개 조합을 비교했다.",
             "트리 100개·최소 leaf 5·seed 42·n_jobs=1, 입력 표현 7/5/5/6개, threshold 0.3/0.4/0.5/0.6/0.7을 수집 전에 고정했다. RF는 scaler 없이 사용하고 Validation을 Train에 합치지 않았다.",
             "", "전체 FPR 10% 이하·정상 묶음 FPR 10% 이하·Recall 50% 이상을 모두 요구한다. 통과 조합은 Recall 최대, F1 최대, FPR 최소, 후보 id·threshold 오름차순으로 고른다. 비용과 새 Test 승인은 아직 하지 않았다.",
             "", "## 모든 후보와 threshold", "", "| 후보 | threshold | FP | FN | FPR | 묶음 FPR | Recall | F1 | 제약 충족 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for trial in report["trials"]:
        metrics, matrix = trial["metrics"], trial["metrics"]["confusion_matrix"]
        batch = trial["by_scenario"]["normal_batched_chat"]["false_positive_rate"]
        lines.append(f"| {trial['candidate_id']} | {trial['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {batch:.2%} | {metrics['recall']:.2%} | {metrics['f1']:.2%} | {'통과' if trial['validation_feasible'] else '미달'} |")
    selected = report["selection"]["selected_trial"]
    lines += ["", "## Validation 선정", ""]
    if selected is None:
        lines.append("사전 제약을 모두 만족한 조합이 없어 미선정으로 완료했다. 후보 추가·threshold 추가·기준 완화를 하지 않았다.")
    else:
        metrics = selected["metrics"]
        lines += [f"개발 선정은 `{selected['candidate_id']}`·threshold `{selected['threshold']}`다.",
                  f"Validation FPR {metrics['false_positive_rate']:.2%}·Recall {metrics['recall']:.2%}·F1 {metrics['f1']:.2%}이며 독립 성능이나 배포 승인 결과는 아니다."]
    lines += ["", "## 같은 메시지의 비교 역할", "", "| 역할 | threshold | FP | FN | FPR | Recall | F1 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, model in report["comparison"]["models"].items():
        metrics, matrix = model["metrics"], model["metrics"]["confusion_matrix"]
        lines.append(f"| {name} | {model['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {metrics['recall']:.2%} | {metrics['f1']:.2%} |")
    lines += ["", "기존 RF 0.3과 새 원본 RF 0.3은 재학습 변화의 대조다. 기본 0.5 비교와 후보별 제약 통과 최적 threshold 비교를 분리한다. 같은 threshold라도 서로 다른 모델의 확률 보정 수준이 같다는 뜻은 아니다.",
              "", "## 첫 메시지와 시나리오", ""]
    displayed = report["comparison"]["models"].get("selected_validation_policy",
                                                report["comparison"]["models"]["new_raw7_0_3"])
    lines += ["미선정이면 아래 표는 새 원본 RF 0.3의 진단이며 선정 결과로 해석하지 않는다." if selected is None else "아래 표는 선정 Validation 정책의 진단이다.",
              "", "| 시나리오 | 메시지 | FP | FN | 정상 FPR | 이상 Recall |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in displayed["by_scenario"].items():
        fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.2%}"
        recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.2%}"
        lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {fpr} | {recall} |")
    lines += ["", "첫 메시지·후속 메시지 FP/FN, 같은 행의 수정/신규 오분류·burst 첫 탐지 위치와 prefix 분모를 JSON에 기록했다. 첫 메시지를 버리거나 이전 누락 정답을 바꾸지 않았다.",
              "", "## 저장과 재현", "", f"산출물: `{report['artifact_dir']}`",
              "모델 입력 변환 명세는 원본 v2 JSON schema와 별도로 저장했다. 후보별 모델을 재로딩해 Train/Validation 확률을 정확히 대조하고, 완료 manifest에 원본/모델/코드/보고서 해시를 보관한다.",
              "", "## 한계", ""] + [f"- {message}" for message in report["limitations"]]
    lines += ["", "## 다음 작업과 남은 단계", "",
              "Validation 선정 후보가 있으면 온라인 prefix 상태와 누락 감지를 구현·검증하고 상태 갱신을 포함한 비용을 측정한다. 비용까지 통과한 후보를 잠근 뒤 새 Test로 승인 확인한다. 미선정이면 보정 실패를 기록하고 새 제한 실험을 별도 설계한다.",
              "", "보정 묶음의 비용·독립 승인이 끝나지 않았으므로 FastAPI·TCP 통합·최종 문서화를 포함한 큰 작업 묶음은 아직 4개 남아 있다.", ""]
    return "\n".join(lines)
