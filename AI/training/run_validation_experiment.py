from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold

from evaluation.analyze_logistic_errors import analyze_attack_categories, format_percent, markdown_table, normalize_category
from scripts.preprocess_unsw_nb15 import build_preprocessor, infer_feature_types, normalize_categorical_missing_values
from training.train_logistic_regression import calculate_metrics, label_distribution, relative_path
from training.train_random_forest import file_sha256


CONFIG_FILE = AI_ROOT / "configs" / "validation_experiment.json"
ARTIFACT_DIR = AI_ROOT / "artifacts" / "validation_experiment"
JSON_REPORT_FILE = AI_ROOT / "reports" / "validation_experiment.json"
MARKDOWN_REPORT_FILE = AI_ROOT / "reports" / "validation_experiment.md"


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    ids = [candidate["id"] for candidate in config["candidates"]]
    if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(r"[a-z0-9_]+", name) for name in ids):
        raise ValueError("후보 id는 중복 없는 소문자, 숫자, 밑줄이어야 합니다.")
    thresholds = config["thresholds"]
    if not thresholds or 0.5 not in thresholds or any(not 0 < value < 1 for value in thresholds):
        raise ValueError("threshold 목록은 0.5를 포함하며 각 값은 0보다 크고 1보다 작아야 합니다.")
    if len(thresholds) != len(set(thresholds)):
        raise ValueError("threshold 목록에 중복 값이 있습니다.")
    if not 0 <= config["selection"]["max_false_positive_rate"] <= 1:
        raise ValueError("허용 오탐률은 0~1 사이여야 합니다.")
    if config["selection"]["primary_metric"] != "recall" or config["selection"]["tie_breakers"] != [
        "f1_desc", "false_positive_rate_asc", "candidate_id_asc", "threshold_asc",
    ]:
        raise ValueError("이 실험의 선정 규칙은 Recall 최대, F1, FPR, 후보 id, threshold 순입니다.")
    split = config["split"]
    if split["n_splits"] < 2 or not 0 <= split["validation_fold"] < split["n_splits"]:
        raise ValueError("분할 수와 validation fold 설정이 올바르지 않습니다.")
    if split["stratify_column"] != "attack_cat" or split["grouping"] != "normalized_input_feature_fingerprint":
        raise ValueError("분리는 attack_cat 층화와 원본 입력 feature 그룹을 사용해야 합니다.")
    return config


def load_raw_training(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    raw = pd.read_csv(path)
    if not {"label", "attack_cat"}.issubset(raw.columns):
        raise ValueError("원본 train에는 label과 attack_cat이 필요합니다.")
    if not raw["label"].isin([0, 1]).all():
        raise ValueError("label은 결측치 없는 0 또는 1이어야 합니다.")
    labels = raw["label"].astype(np.int8)
    categories = normalize_category(raw["attack_cat"])
    if not np.array_equal((categories == "Normal").to_numpy(), (labels == 0).to_numpy()):
        raise ValueError("Normal 공격 유형과 정상 label이 일치하지 않습니다.")
    features = raw.drop(columns=["id", "label", "attack_cat"], errors="ignore")
    return features, labels, categories


def grouped_split(
    features: pd.DataFrame, categories: pd.Series, options: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # 결측 표현을 통일한 입력만 해시하므로 id나 정답은 그룹 정의에 들어가지 않습니다.
    normalized = features.copy()
    _, categorical = infer_feature_types(normalized)
    normalize_categorical_missing_values(normalized, categorical)
    groups = pd.util.hash_pandas_object(normalized, index=False).to_numpy()
    splitter = StratifiedGroupKFold(
        n_splits=options["n_splits"], shuffle=True, random_state=options["random_state"],
    )
    splits = splitter.split(features, categories, groups)
    for fold, (train_indices, validation_indices) in enumerate(splits):
        if fold == options["validation_fold"]:
            if np.intersect1d(groups[train_indices], groups[validation_indices]).size:
                raise ValueError("학습과 validation에 동일한 feature 그룹이 있습니다.")
            return train_indices, validation_indices, groups
    raise ValueError("요청한 validation fold를 찾을 수 없습니다.")


def fit_training_preprocessor(
    training: pd.DataFrame, validation: pd.DataFrame,
) -> tuple[Any, pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    numeric, categorical = infer_feature_types(training)
    train_features = training.copy()
    validation_features = validation.copy()
    normalize_categorical_missing_values(train_features, categorical)
    normalize_categorical_missing_values(validation_features, categorical)
    preprocessor = build_preprocessor(numeric, categorical)
    # fit은 분리된 학습 부분에 한 번만 수행하고 validation에는 transform만 적용합니다.
    train_array = preprocessor.fit_transform(train_features).astype(np.float32)
    validation_array = preprocessor.transform(validation_features).astype(np.float32)
    if not np.isfinite(train_array).all() or not np.isfinite(validation_array).all():
        raise ValueError("전처리 결과에 결측치 또는 무한대가 있습니다.")
    names = preprocessor.get_feature_names_out()
    return (
        preprocessor,
        pd.DataFrame(train_array, columns=names),
        pd.DataFrame(validation_array, columns=names),
        numeric, categorical,
    )


def make_model(candidate: dict[str, Any], config: dict[str, Any]) -> Any:
    if candidate["kind"] == "logistic_regression":
        return LogisticRegression(**candidate["parameters"])
    if candidate["kind"] == "random_forest":
        parameters = {**config["random_forest_common_parameters"], **candidate["parameters"]}
        parameters["n_jobs"] = min(parameters["n_jobs"], os.cpu_count() or 1)
        return RandomForestClassifier(**parameters)
    raise ValueError(f"지원하지 않는 후보 종류입니다: {candidate['kind']}")


def evaluate_threshold(labels: pd.Series, probabilities: np.ndarray, threshold: float) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(np.int8)
    metrics = calculate_metrics(labels, predictions, probabilities)
    details = metrics["confusion_matrix_details"]
    metrics["false_positive_rate"] = details["false_positive"] / int((labels == 0).sum())
    metrics["false_negative_rate"] = details["false_negative"] / int((labels == 1).sum())
    return metrics


def choose_trial(trials: list[dict[str, Any]], maximum_fpr: float) -> dict[str, Any] | None:
    feasible = [
        trial for trial in trials
        if trial["converged"] and trial["metrics"]["false_positive_rate"] <= maximum_fpr
    ]
    if not feasible:
        return None
    return min(feasible, key=lambda trial: (
        -trial["metrics"]["recall"], -trial["metrics"]["f1"],
        trial["metrics"]["false_positive_rate"], trial["candidate_id"], trial["threshold"],
    ))


def render_report(result: dict[str, Any]) -> str:
    split = result["split"]
    selected = result["selection"]["selected_trial"]
    rows = []
    for candidate in result["candidates"]:
        for trial in candidate["threshold_trials"]:
            metrics = trial["metrics"]
            rows.append([
                candidate["id"], str(trial["threshold"]), format_percent(metrics["false_positive_rate"]),
                format_percent(metrics["recall"]), format_percent(metrics["precision"]),
                format_percent(metrics["f1"]), "충족" if trial["eligible"] else "미충족",
            ])
    lines = [
        "# Validation 분리와 설정·threshold 선정 실험", "",
        "## 고정 실험 규칙", "",
        "공식 train만 사용해 전처리, 모델 학습, 설정 및 threshold 선택을 수행했다. 공식 test CSV는 읽지 않았다.", "",
        "- 후보: LR C=1, RF min_samples_leaf=1/5/20; RF 트리 수 100개 등 나머지 조건은 고정",
        f"- threshold 후보: `{result['protocol']['thresholds']}`",
        f"- 목표: validation FPR <= {format_percent(result['protocol']['selection']['max_false_positive_rate'])} 중 Recall 최대",
        "- 동률 처리: F1 최대, FPR 최소, 후보 id, threshold 순으로 결정",
        "- 이 오탐률 목표는 실험용이며 실제 운영 환경의 오탐률을 보장하지 않는다.", "",
        "## 중복 그룹을 고려한 분리", "",
        f"원본 train {split['source_rows']:,}건에서 학습 {split['training_rows']:,}건, "
        f"validation {split['validation_rows']:,}건({format_percent(split['validation_fraction'])})으로 분리했다.", "",
        f"동일 입력 feature 중복 행은 {split['duplicate_feature_rows']:,}건이었다. id와 정답을 제외하고 "
        "결측 표현을 통일한 feature fingerprint를 그룹으로 사용했다. 단순 row 분리로 중복 행이 양쪽에 "
        "들어가는 문제를 피하도록 StratifiedGroupKFold의 사전 지정 fold를 사용했다.", "",
        f"- fold 수: {split['n_splits']}, validation fold: {split['validation_fold']}, seed: {split['random_state']}",
        "- 층화 기준: attack_cat; 학습 입력에는 attack_cat을 포함하지 않음",
        f"- 학습/validation 간 공유 feature 그룹: {split['shared_feature_groups']}",
        "- 그룹 크기가 달라 비율과 유형별 비중이 정확한 80:20은 아닐 수 있음", "",
        markdown_table(["공격 유형", "학습", "Validation"], [
            [category, f"{counts['training']:,}", f"{counts['validation']:,}"]
            for category, counts in split["category_counts"].items()
        ]), "",
        "## 전처리 학습 범위", "",
        f"전처리기는 학습 {split['training_rows']:,}건에만 fit했다. Validation에는 같은 전처리기의 "
        "transform만 적용했다. 기존 전체 train 기반 전처리 CSV와 전처리기는 사용하지 않았다.", "",
        f"숫자형 {result['preprocessing']['numeric_feature_count']}개, 범주형 "
        f"{result['preprocessing']['categorical_feature_count']}개를 처리해 "
        f"{result['preprocessing']['processed_feature_count']}개 입력을 생성했다. 중앙값 대체, "
        "표준화, unknown 처리와 one-hot encoding은 기존 실험과 같은 규칙이다.", "",
        "## 후보별 threshold 결과", "",
        markdown_table(["후보", "Threshold", "FPR", "Recall", "Precision", "F1", "제약"], rows), "",
        "제약 충족은 허용 FPR 조건과 학습 수렴 여부를 모두 확인한 결과다. "
        "한 번 학습한 모델의 validation 확률에 여러 threshold를 적용하며, threshold마다 재학습하지 않는다.", "",
        "## 선정 결과", "",
    ]
    if selected is None:
        lines.extend(["제약을 충족한 후보가 없어 모델을 선정하지 않았다. 목표를 몰래 완화하거나 test를 보고 후보를 바꾸지 않았다.", ""])
    else:
        metrics = selected["metrics"]
        lines.extend([
            f"선정 후보는 `{selected['candidate_id']}`, threshold는 `{selected['threshold']}`다. "
            f"Validation FPR {format_percent(metrics['false_positive_rate'])}, Recall "
            f"{format_percent(metrics['recall'])}, F1 {format_percent(metrics['f1'])}로 사전 규칙을 충족했다.", "",
            "선정 모델은 분리된 학습 부분으로 학습한 상태로 보관한다. Validation을 합쳐 다시 fit하지 않았으며, "
            "전처리기, 모델, feature schema, threshold 및 split 위치를 함께 저장했다.", "",
        ])

    lines.extend(["## 후보별 공격 유형 Recall", ""])
    for candidate in result["candidates"]:
        best = candidate["best_feasible_trial"]
        best_by_category = {
            row["attack_category"]: row for row in candidate["best_attack_category_performance"]
        }
        lines.extend([
            f"### {candidate['id']}", "",
            f"이 후보의 제약 충족 threshold: `{best['threshold'] if best else '없음'}`", "",
            markdown_table(["유형", "표본", "기본 0.5 Recall", "선정 threshold Recall", "선정 FN"], [
                [row["attack_category"], f"{row['total']:,}", format_percent(row["recall"]),
                 format_percent(best_by_category[row['attack_category']]["recall"]) if best else "N/A",
                 str(best_by_category[row['attack_category']]["false_negative"]) if best else "N/A"]
                for row in candidate["default_attack_category_performance"]
            ]), "",
        ])
    lines.extend([
        "## 해석 범위와 다음 단계", "",
        "- 이번 validation 결과를 과거 공식 test 결과와 직접 비교해 개선폭이라고 표현하면 안 된다. 평가 표본과 전처리 학습 범위가 다르다.",
        "- 정확히 같은 feature 그룹은 분리했지만 동일 세션이나 유사한 흐름 전체를 분리했다는 뜻은 아니다. 이 CSV에는 충분한 시간/세션 정보가 없다.",
        "- 단일 fold와 seed의 탐색 실험이므로 선택 후 수치는 독립적인 최종 성능 추정이 아니다.",
        "- 기존 공식 test도 이전 단계에서 탐색한 자료다. 추후 고정된 설정 평가와 새 데이터 검증의 한계를 구분해 기록해야 한다.",
        "- 저장한 후보와 threshold를 동결하고, 다음 단계에서 운영용 feature의 의미·단위·계산 시점을 C++ 수집 값과 대조한다.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 train에서 validation을 분리해 고정 후보와 threshold를 비교합니다.")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    args = parser.parse_args()
    config = load_config(args.config)
    source = AI_ROOT / config["dataset"]["training_file"]
    print("[Validation] 원본 train만 읽고 중복 feature 그룹으로 분리합니다.", flush=True)
    features, labels, categories = load_raw_training(source)
    train_indices, validation_indices, groups = grouped_split(features, categories, config["split"])
    y_train = labels.iloc[train_indices].reset_index(drop=True)
    y_validation = labels.iloc[validation_indices].reset_index(drop=True)
    if set(y_train.unique()) != {0, 1} or set(y_validation.unique()) != {0, 1}:
        raise ValueError("학습과 validation에 정상 및 공격 label이 모두 필요합니다.")
    validation_categories = categories.iloc[validation_indices].reset_index(drop=True)
    preprocessor, x_train, x_validation, numeric, categorical = fit_training_preprocessor(
        features.iloc[train_indices], features.iloc[validation_indices],
    )
    print(f"  - 학습 {len(x_train):,}건 / validation {len(x_validation):,}건 / 입력 {x_train.shape[1]}개", flush=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    preprocessor_path = ARTIFACT_DIR / "preprocessor.joblib"
    joblib.dump(preprocessor, preprocessor_path, compress=3)
    schema = {
        "raw_input_features": list(features.columns), "numeric_features": numeric,
        "categorical_features": categorical, "processed_features": list(x_train.columns),
        "normalization": "transform 전에 normalize_categorical_missing_values를 동일하게 적용합니다.",
        "label_map": {"normal": 0, "attack": 1}, "fit_rows": len(x_train),
    }
    save_json(ARTIFACT_DIR / "feature_schema.json", schema)
    save_json(ARTIFACT_DIR / "split_manifest.json", {
        "source": relative_path(source), "source_sha256": file_sha256(source),
        "index_base": 0, "indices_refer_to": "원본 train CSV의 헤더 제외 row 위치",
        "training_indices": train_indices.tolist(), "validation_indices": validation_indices.tolist(),
        "split": config["split"],
    })

    candidates = []
    all_trials = []
    probabilities_by_candidate = {}
    for candidate in config["candidates"]:
        model = make_model(candidate, config)
        fit_parameters = model.get_params()
        print(f"[Validation] {candidate['id']} 학습 중", flush=True)
        started = time.perf_counter()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            model.fit(x_train, y_train)
        training_seconds = time.perf_counter() - started
        convergence_messages = [str(item.message) for item in captured if issubclass(item.category, ConvergenceWarning)]
        other_messages = [str(item.message) for item in captured if not issubclass(item.category, ConvergenceWarning)]
        if candidate["kind"] == "random_forest":
            # 확률 합산 순서를 일정하게 유지해 threshold 경계의 재현성을 확보합니다.
            model.set_params(n_jobs=1)
        probabilities = model.predict_proba(x_validation)[:, 1]
        probabilities_by_candidate[candidate["id"]] = probabilities
        trials = []
        for threshold in config["thresholds"]:
            metrics = evaluate_threshold(y_validation, probabilities, threshold)
            trials.append({
                "candidate_id": candidate["id"], "threshold": threshold,
                "converged": not convergence_messages, "metrics": metrics,
                "eligible": not convergence_messages and metrics["false_positive_rate"] <= config["selection"]["max_false_positive_rate"],
            })
        best = choose_trial(trials, config["selection"]["max_false_positive_rate"])
        model_path = ARTIFACT_DIR / f"{candidate['id']}.joblib"
        joblib.dump(model, model_path, compress=3)
        candidates.append({
            "id": candidate["id"], "kind": candidate["kind"],
            "fit_parameters": fit_parameters, "prediction_parameters": model.get_params(),
            "training_seconds": training_seconds, "convergence_warnings": convergence_messages,
            "other_warnings": other_messages, "threshold_trials": trials,
            "best_feasible_trial": best,
            "default_attack_category_performance": analyze_attack_categories(
                y_validation, validation_categories, (probabilities >= 0.5).astype(np.int8), probabilities,
            ),
            "best_attack_category_performance": analyze_attack_categories(
                y_validation, validation_categories, (probabilities >= best["threshold"]).astype(np.int8), probabilities,
            ) if best else [],
            "model_path": relative_path(model_path), "model_sha256": file_sha256(model_path),
        })
        all_trials.extend(trials)
        print(f"  - 선정 가능한 threshold: {best['threshold'] if best else '없음'}", flush=True)

    selected = choose_trial(all_trials, config["selection"]["max_false_positive_rate"])
    selected_model_path = None
    if selected:
        selected_model_path = ARTIFACT_DIR / f"{selected['candidate_id']}.joblib"
        restored_model = joblib.load(selected_model_path)
        validation_raw = features.iloc[validation_indices].copy()
        normalize_categorical_missing_values(validation_raw, categorical)
        restored_preprocessor = joblib.load(preprocessor_path)
        restored_input = pd.DataFrame(
            restored_preprocessor.transform(validation_raw).astype(np.float32), columns=x_validation.columns,
        )
        restored_probabilities = restored_model.predict_proba(restored_input)[:, 1]
        expected = probabilities_by_candidate[selected["candidate_id"]]
        if not np.allclose(restored_probabilities, expected, rtol=1e-12, atol=1e-12):
            raise ValueError("선정 모델과 전처리기 재로딩 후 확률이 재현되지 않습니다.")
        if evaluate_threshold(y_validation, restored_probabilities, selected["threshold"]) != selected["metrics"]:
            raise ValueError("재로딩한 선정 모델의 지표가 일치하지 않습니다.")

    selection = {
        "status": "selected" if selected else "no_feasible_candidate", "selected_trial": selected,
        "model_path": relative_path(selected_model_path) if selected_model_path else None,
        "model_sha256": file_sha256(selected_model_path) if selected_model_path else None,
        "preprocessor_path": relative_path(preprocessor_path), "preprocessor_sha256": file_sha256(preprocessor_path),
        "feature_schema_path": relative_path(ARTIFACT_DIR / "feature_schema.json"),
        "fit_scope": "grouped training subset only", "full_train_refit_performed": False,
        "official_test_evaluated": False,
    }
    save_json(ARTIFACT_DIR / "selection.json", selection)
    split = {
        **config["split"], "source_rows": len(features), "training_rows": len(train_indices),
        "validation_rows": len(validation_indices), "validation_fraction": len(validation_indices) / len(features),
        "duplicate_feature_rows": len(features) - int(np.unique(groups).size), "shared_feature_groups": 0,
        "training_label_distribution": label_distribution(y_train),
        "validation_label_distribution": label_distribution(y_validation),
        "category_counts": {
            category: {
                "training": int((categories.iloc[train_indices] == category).sum()),
                "validation": int((validation_categories == category).sum()),
            } for category in sorted(categories.unique())
        },
    }
    result = {
        "schema_version": 1, "generated_at_utc": datetime.now(UTC).isoformat(),
        "protocol": config, "config_sha256": file_sha256(args.config),
        "source": {"path": relative_path(source), "sha256": file_sha256(source)},
        "split": split,
        "preprocessing": {
            "fit_scope": "grouped training subset only", "fit_rows": len(x_train),
            "numeric_feature_count": len(numeric), "categorical_feature_count": len(categorical),
            "processed_feature_count": x_train.shape[1],
        },
        "candidates": candidates, "selection": selection,
        "environment": {
            "python": platform.python_version(), "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__, "numpy": np.__version__, "platform": platform.platform(),
        },
        "official_test_evaluated": False,
    }
    save_json(JSON_REPORT_FILE, result)
    MARKDOWN_REPORT_FILE.write_text(render_report(result), encoding="utf-8")
    print("[Validation] 실험 및 재로딩 검증이 완료되었습니다.")
    if selected:
        print(f"  - 선정: {selected['candidate_id']}, threshold={selected['threshold']}")
        print(f"  - Recall={selected['metrics']['recall']:.6f}, FPR={selected['metrics']['false_positive_rate']:.6f}")
    else:
        print("  - 허용 오탐률 조건을 충족한 후보가 없습니다.")
    print(f"  - 보고서: {MARKDOWN_REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
