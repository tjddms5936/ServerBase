from __future__ import annotations

import argparse
import hashlib
import io
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from data_collection.quality import parse_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.audit_feature_alignment import assess_model_schema
from inference_api.payload_validation import OPERATIONAL_CONTRACT
from training.operational_data import OperationalData, load_operational_data
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


ARTIFACT_FILES = ("pipeline.joblib", "feature_schema.json", "label_map.json",
                  "decision_policy.json", "validation_predictions.csv")


def load_verified_model(directory: Path, model_kind: str, data: OperationalData) -> tuple[Pipeline, dict, dict, pd.DataFrame]:
    manifest_path = directory / "training_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = parse_json(manifest_bytes.decode("utf-8"))
    if (manifest.get("status") != "complete" or manifest.get("dataset_id") != data.dataset_id or
            manifest.get("feature_contract_id") != data.schema["feature_contract_id"] or
            manifest.get("input_sha256") != data.input_sha256):
        raise ValueError("모델과 현재 데이터셋의 학습 입력·고정 분할이 일치하지 않습니다.")
    if model_kind == "lr" and manifest.get("converged") is not True:
        raise ValueError("LR의 수렴을 확인할 수 없습니다.")
    if set(manifest["artifacts"]) != set(ARTIFACT_FILES):
        raise ValueError("모델 산출물의 파일 구성이 일치하지 않습니다.")

    # manifest의 임의 파일명을 열지 않고 알려진 모델 산출물만 검증한다.
    contents = {}
    for name in ARTIFACT_FILES:
        content = (directory / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"모델 산출물 SHA-256 불일치: {name}")
        contents[name] = content
    schema = parse_json(contents["feature_schema.json"].decode("utf-8"))
    labels = parse_json(contents["label_map.json"].decode("utf-8"))
    decision = parse_json(contents["decision_policy.json"].decode("utf-8"))
    if (not assess_model_schema(OPERATIONAL_CONTRACT, "message", schema)["schema_compatible"] or
            schema.get("feature_dtypes") != data.schema["feature_dtypes"] or labels != data.label_map):
        raise ValueError("모델 schema 또는 정답 정의가 현재 입력과 일치하지 않습니다.")
    if decision != {"positive_class": 1, "threshold": 0.5, "comparison": ">=",
                    "selection": "fixed_baseline_not_tuned", "deployment_ready": False}:
        raise ValueError("비교에는 사전에 고정한 threshold=0.5가 필요합니다.")

    reports = [(Path(path), digest) for path, digest in manifest["reports"].items() if Path(path).suffix == ".json"]
    if len(reports) != 1:
        raise ValueError("학습 JSON 보고서를 정확히 하나 지정해야 합니다.")
    report_path, expected_hash = reports[0]
    report_content = report_path.read_bytes()
    if hashlib.sha256(report_content).hexdigest() != expected_hash:
        raise ValueError("학습 보고서의 SHA-256이 일치하지 않습니다.")
    report = parse_json(report_content.decode("utf-8"))
    expected_experiment = {"lr": "operational_logistic_regression_fixed_baseline",
                           "rf": "operational_random_forest_fixed_baseline"}[model_kind]
    if (report.get("experiment") != expected_experiment or report.get("dataset_id") != data.dataset_id or
            report.get("input_sha256") != data.input_sha256 or report.get("feature_order") != FEATURE_NAMES or
            report.get("fit_scope") != "train_only" or report.get("test_evaluated") is not False or
            report.get("test_files_read") is not False or report.get("config", {}).get("threshold") != 0.5):
        raise ValueError("비교 대상의 학습 조건 또는 test 미사용 기록이 일치하지 않습니다.")

    # 직접 만든 신뢰 가능한 joblib만 로드한다. SHA-256 일치가 파일 안전성을 보증하지는 않는다.
    pipeline = joblib.load(io.BytesIO(contents["pipeline.joblib"]))
    expected_type = LogisticRegression if model_kind == "lr" else RandomForestClassifier
    if (not isinstance(pipeline, Pipeline) or not isinstance(pipeline.named_steps.get("classifier"), expected_type) or
            list(pipeline.classes_) != [0, 1] or list(pipeline.feature_names_in_) != FEATURE_NAMES):
        raise ValueError("모델 종류·입력 순서·클래스 순서가 일치하지 않습니다.")
    classifier = pipeline.named_steps["classifier"]
    if model_kind == "lr":
        scaler = pipeline.named_steps.get("scaler")
        if (list(pipeline.named_steps) != ["scaler", "classifier"] or not isinstance(scaler, StandardScaler) or
                int(scaler.n_samples_seen_) != len(data.train.y) or
                not np.allclose(scaler.mean_, data.train.X.mean().to_numpy()) or
                not np.allclose(scaler.var_, data.train.X.var(ddof=0).to_numpy())):
            raise ValueError("LR scaler의 train 전용 fit을 확인할 수 없습니다.")
        parameters = {name: report["config"][name] for name in ("C", "max_iter", "tol", "solver", "random_state")}
    else:
        if list(pipeline.named_steps) != ["classifier"] or schema["preprocessing"]["kind"] != "identity":
            raise ValueError("RF의 원본 입력 전처리 계약이 일치하지 않습니다.")
        parameters = report["model_parameters"]
    if any(classifier.get_params().get(name) != value for name, value in parameters.items()):
        raise ValueError("저장 모델과 보고서의 학습 설정이 다릅니다.")
    evidence = {"directory": str(directory), "training_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "report_path": str(report_path), "report_sha256": expected_hash,
                "artifacts": manifest["artifacts"]}
    saved = pd.read_csv(io.BytesIO(contents["validation_predictions.csv"]), dtype={"row_id": str})
    return pipeline, report, evidence, saved


def prediction_transitions(y: np.ndarray, lr: np.ndarray, rf: np.ndarray) -> dict:
    if not (y.shape == lr.shape == rf.shape and y.ndim == 1) or any(not np.isin(values, [0, 1]).all() for values in (y, lr, rf)):
        raise ValueError("오분류 비교에는 같은 길이의 이진 정답·예측이 필요합니다.")
    masks = {
        "both_correct": (lr == y) & (rf == y), "both_wrong": (lr != y) & (rf != y),
        "corrected_fp": (y == 0) & (lr == 1) & (rf == 0),
        "corrected_fn": (y == 1) & (lr == 0) & (rf == 1),
        "new_fp": (y == 0) & (lr == 0) & (rf == 1),
        "new_fn": (y == 1) & (lr == 1) & (rf == 0),
    }
    return {name: int(mask.sum()) for name, mask in masks.items()}


def render_report(report: dict) -> str:
    lr, rf = report["models"]["lr"], report["models"]["rf"]
    changes = report["prediction_transitions"]
    lines = ["# 운영 LR / RF 초기 기준 모델 비교", "", "## 비교 조건", "",
             "학습 데이터·정답·metadata·고정 분할의 파일 해시를 두 모델과 현재 데이터셋에서 대조했다. 저장 모델을 다시 로드해 동일한 validation을 예측했다.",
             "LR은 train 전용 StandardScaler, RF는 원본 입력을 사용한다. 입력 7개와 메시지·정답은 같고 모델별 전처리만 다르다.",
             "두 모델 모두 threshold=0.5다. test는 읽지 않았고 설정·threshold 선정, 최종 모델 선택, 실제 모델 배포는 하지 않았다.", "",
             "## validation 지표", "", "| 지표 | LR | RF | RF - LR |", "| --- | ---: | ---: | ---: |"]
    for name, delta in report["metric_deltas_rf_minus_lr"].items():
        lines.append(f"| {name} | {lr['validation_metrics'][name]:.6f} | {rf['validation_metrics'][name]:.6f} | {delta:+.6f} |")
    for name, model in (("LR", lr), ("RF", rf)):
        matrix = model["validation_metrics"]["confusion_matrix"]
        lines.append(f"\n{name}: TN={matrix['tn']}, FP={matrix['fp']}, FN={matrix['fn']}, TP={matrix['tp']}.")
    lines += ["", "## 같은 메시지의 오분류 변화", "",
              f"- LR 오탐을 RF가 수정: {changes['corrected_fp']}건",
              f"- LR 누락을 RF가 수정: {changes['corrected_fn']}건",
              f"- RF에서 새로 발생한 오탐: {changes['new_fp']}건",
              f"- RF에서 새로 발생한 누락: {changes['new_fn']}건",
              f"- 두 모델 모두 정답: {changes['both_correct']}건 / 두 모델 모두 오분류: {changes['both_wrong']}건", "",
              "## 시나리오별 오분류", "", "| 시나리오 | 메시지 | LR FP | RF FP | LR FN | RF FN |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, values in report["scenario_comparison"].items():
        lines.append(f"| {name} | {values['lr']['rows']} | {values['lr']['fp']} | {values['rf']['fp']} | {values['lr']['fn']} | {values['rf']['fn']} |")
    lines += ["", "## 해석과 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "JSON에 시나리오별 Recall/FPR, 예측이 달라진 모든 row_id·정답·확률·예측과 모델/보고서 해시를 보관한다. 이 보고서는 지연시간·메모리 benchmark가 아니다.",
              "", "## 다음 작업", "", "제한된 모델 설정·threshold 후보와 선택 규칙을 사전에 고정하고 validation에서 비교·선정한다. test는 최종 비교까지 읽지 않는다.", ""]
    return "\n".join(lines)


def compare_models(dataset_dir: Path, dataset_id: str, lr_dir: Path, rf_dir: Path, report_prefix: Path) -> dict:
    report_json, report_md = report_prefix.with_suffix(".json"), report_prefix.with_suffix(".md")
    if report_json.exists() or report_md.exists():
        raise FileExistsError("기존 비교 보고서는 덮어쓰지 않습니다. 새 report-prefix를 지정하세요.")
    data = load_operational_data(dataset_dir, dataset_id)
    models, probabilities, predictions, evidence = {}, {}, {}, {}
    for name, directory in (("lr", lr_dir), ("rf", rf_dir)):
        pipeline, training_report, evidence[name], saved = load_verified_model(directory, name, data)
        probability = pipeline.predict_proba(data.validation.X)[:, 1]
        metrics = calculate_metrics(data.validation.y, probability, 0.5)
        if metrics != training_report["validation_metrics"]:
            raise ValueError(f"저장 모델의 재예측과 학습 보고서의 validation 지표가 다릅니다: {name}")
        # 개별 예측 CSV도 현재 row 순서·정답·확률과 맞는지 검사한다.
        if (list(saved["row_id"]) != list(data.validation.metadata["row_id"]) or
                not np.array_equal(saved["label"].to_numpy(), data.validation.y) or
                not np.allclose(saved["anomaly_probability"].to_numpy(), probability, rtol=0, atol=1e-14) or
                not np.array_equal(saved["predicted_label"].to_numpy(), (probability >= 0.5).astype("int64"))):
            raise ValueError(f"validation 메시지별 예측 기록이 다릅니다: {name}")
        probabilities[name] = probability
        predictions[name] = (probability >= 0.5).astype("int64")
        models[name] = {"validation_metrics": metrics,
                        "validation_by_scenario": scenario_metrics(data.validation, predictions[name]),
                        "train_metrics_reference": training_report["train_metrics"],
                        "preprocessing": "train_only_StandardScaler" if name == "lr" else "identity_no_scaler"}
    changed_rows = []
    for index in np.flatnonzero(predictions["lr"] != predictions["rf"]):
        metadata = data.validation.metadata.iloc[index]
        label = int(data.validation.y[index])
        category = ("corrected_" if predictions["rf"][index] == label else "new_") + ("fp" if label == 0 else "fn")
        changed_rows.append({**{name: metadata[name] for name in ("row_id", "run_id", "session_id", "request_index", "scenario_id")},
                             "label": label, "change_type": category,
                             "lr_probability": float(probabilities["lr"][index]), "rf_probability": float(probabilities["rf"][index]),
                             "lr_prediction": int(predictions["lr"][index]), "rf_prediction": int(predictions["rf"][index])})
    metric_names = ("accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "log_loss",
                    "false_positive_rate", "false_negative_rate")
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(), "dataset_id": dataset_id,
        "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "threshold": 0.5, "threshold_selection": "fixed_baseline_not_tuned", "evaluated_split": "validation",
        "test_files_read": False, "test_evaluated": False, "final_model_selected": False, "deployment_ready": False,
        "validation_rows": len(data.validation.y), "input_sha256": data.input_sha256,
        "models": models, "model_evidence": evidence,
        "metric_deltas_rf_minus_lr": {name: models["rf"]["validation_metrics"][name] - models["lr"]["validation_metrics"][name]
                                     for name in metric_names},
        "prediction_transitions": prediction_transitions(data.validation.y, predictions["lr"], predictions["rf"]),
        "changed_predictions": changed_rows,
        "scenario_comparison": {scenario: {name: model["validation_by_scenario"][scenario] for name, model in models.items()}
                                for scenario in models["lr"]["validation_by_scenario"]},
        "code_sha256": {"compare_operational_baselines.py": file_hash(Path(__file__)),
                        "operational_data.py": file_hash(AI_ROOT / "training" / "operational_data.py"),
                        "train_operational_logistic_regression.py": file_hash(AI_ROOT / "training" / "train_operational_logistic_regression.py")},
        "limitations": [
            "통제된 애플리케이션 메시지의 초기 결과이며 실제 침입 탐지 성능이나 배포 성능이 아니다.",
            "동일 입력·분할·threshold를 맞췄지만 같은 생성기·단일 캠페인의 작은 독립 run 수 한계는 해결하지 못했다.",
            "전체 지표와 시나리오별 FP/FN을 함께 해석한다. 확률 0.5가 두 모델에서 같은 보정 수준이라는 뜻은 아니다.",
            "validation에서 확인한 차이가 별도 캠페인이나 최종 test에도 유지된다고 보장하지 않는다.",
            "RF가 개선되더라도 선형 모델의 한계가 유일한 원인이라고 확정하지 않는다. 본문 관측 불가·생성기 특성도 남는다.",
            "설정·threshold 선정, 지연시간·메모리 측정, 최종 test 비교와 모델 연결은 별도 단계로 남아 있다.",
        ],
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    with report_md.open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 LR/RF의 동일 validation 초기 비교. test는 읽지 않습니다.")
    parser.add_argument("--dataset-dir", type=Path, default=AI_ROOT / "data" / "processed" / "operational_message" / "v1")
    parser.add_argument("--dataset-id", default="operational_message_v1")
    parser.add_argument("--lr-dir", type=Path, default=AI_ROOT / "artifacts" / "operational_message" / "logistic_regression_v1")
    parser.add_argument("--rf-dir", type=Path, default=AI_ROOT / "artifacts" / "operational_message" / "random_forest_v1")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_baseline_comparison")
    args = parser.parse_args()
    try:
        report = compare_models(args.dataset_dir.resolve(), args.dataset_id, args.lr_dir.resolve(), args.rf_dir.resolve(), args.report_prefix.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"[운영 비교] 실패: {error}", file=sys.stderr)
        return 2
    for name, model in report["models"].items():
        metrics = model["validation_metrics"]
        matrix = metrics["confusion_matrix"]
        print(f"[운영 비교] {name.upper()}: F1={metrics['f1']:.6f}, FP={matrix['fp']}, FN={matrix['fn']}")
    print(f"[운영 비교] 같은 validation {report['validation_rows']}건 / test 미사용 / 최종 모델 미선정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
