from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS_DIR = AI_ROOT / ".deps"

if LOCAL_DEPS_DIR.exists():
    sys.path.insert(0, str(LOCAL_DEPS_DIR))

import joblib
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score


PROCESSED_DIR = AI_ROOT / "data" / "processed" / "unsw_nb15"
RAW_DIR = AI_ROOT / "data" / "raw" / "unsw_nb15"
ARTIFACTS_DIR = AI_ROOT / "artifacts"
REPORTS_DIR = AI_ROOT / "reports"

X_TEST_FILE = PROCESSED_DIR / "X_test.csv"
Y_TEST_FILE = PROCESSED_DIR / "y_test.csv"
ATTACK_CATEGORY_FILE = PROCESSED_DIR / "attack_cat_test.csv"
RAW_TEST_FILE = RAW_DIR / "UNSW_NB15_testing-set.csv"
MODEL_FILE = ARTIFACTS_DIR / "baseline_logistic_regression.joblib"

JSON_REPORT_FILE = REPORTS_DIR / "logistic_error_analysis.json"
MARKDOWN_REPORT_FILE = REPORTS_DIR / "logistic_error_analysis.md"

DEFAULT_THRESHOLD = 0.5
THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
TOP_FEATURE_COUNT = 15
MIN_CATEGORY_SUPPORT = 20


def require_files(paths: list[Path]) -> None:
    missing_files = [path for path in paths if not path.exists()]
    if missing_files:
        formatted_paths = "\n".join(f"  - {path}" for path in missing_files)
        raise FileNotFoundError(f"분석에 필요한 파일을 찾을 수 없습니다.\n{formatted_paths}")


def normalize_category(values: pd.Series) -> pd.Series:
    normalized = values.astype("string").fillna("unknown").str.strip()
    return normalized.replace(
        {
            "": "unknown",
            "-": "unknown",
            "nan": "unknown",
            "None": "unknown",
            "null": "unknown",
        }
    )


def load_inputs() -> tuple[Any, pd.DataFrame, pd.Series, pd.Series, pd.DataFrame]:
    model = joblib.load(MODEL_FILE)
    x_test = pd.read_csv(X_TEST_FILE, dtype=np.float32)
    y_test_data = pd.read_csv(Y_TEST_FILE)
    attack_data = pd.read_csv(ATTACK_CATEGORY_FILE)
    raw_test = pd.read_csv(RAW_TEST_FILE)

    if "label" not in y_test_data.columns:
        raise ValueError(f"label column이 없습니다: {Y_TEST_FILE}")
    if "attack_cat" not in attack_data.columns:
        raise ValueError(f"attack_cat column이 없습니다: {ATTACK_CATEGORY_FILE}")

    y_test = y_test_data["label"].astype(np.int8)
    attack_categories = normalize_category(attack_data["attack_cat"])
    return model, x_test, y_test, attack_categories, raw_test


def validate_inputs(
    model: Any,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    attack_categories: pd.Series,
    raw_test: pd.DataFrame,
) -> None:
    lengths = {
        "X_test": len(x_test),
        "y_test": len(y_test),
        "attack_cat_test": len(attack_categories),
        "raw_test": len(raw_test),
    }
    if len(set(lengths.values())) != 1:
        raise ValueError(f"분석 입력 파일의 row 수가 다릅니다: {lengths}")
    if "label" not in raw_test.columns or "attack_cat" not in raw_test.columns:
        raise ValueError("원본 test CSV에 label 또는 attack_cat column이 없습니다.")
    if not np.array_equal(raw_test["label"].astype(np.int8).to_numpy(), y_test.to_numpy()):
        raise ValueError("원본 test CSV와 y_test.csv의 label 순서가 다릅니다.")

    raw_attack_categories = normalize_category(raw_test["attack_cat"])
    if not np.array_equal(raw_attack_categories.to_numpy(), attack_categories.to_numpy()):
        raise ValueError("원본 test CSV와 attack_cat_test.csv의 row 순서가 다릅니다.")
    if x_test.isna().any().any():
        raise ValueError("X_test에 결측치가 남아 있습니다.")
    if getattr(model, "n_features_in_", x_test.shape[1]) != x_test.shape[1]:
        raise ValueError("모델이 기대하는 feature 수와 X_test의 feature 수가 다릅니다.")
    if hasattr(model, "feature_names_in_") and not np.array_equal(
        model.feature_names_in_, x_test.columns.to_numpy()
    ):
        raise ValueError("모델 학습 당시 feature 이름 또는 순서와 X_test가 다릅니다.")


def confusion_details(y_true: pd.Series, predictions: np.ndarray) -> dict[str, Any]:
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    true_negative, false_positive, false_negative, true_positive = (
        int(value) for value in matrix.ravel()
    )
    normal_count = true_negative + false_positive
    attack_count = true_positive + false_negative

    return {
        "matrix": matrix.astype(int).tolist(),
        "labels": ["normal(0)", "attack(1)"],
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_positive": true_positive,
        "false_positive_rate": false_positive / normal_count,
        "false_negative_rate": false_negative / attack_count,
        "specificity": true_negative / normal_count,
        "recall": true_positive / attack_count,
    }


def probability_summary(values: np.ndarray) -> dict[str, float]:
    quantiles = np.quantile(values, [0.0, 0.25, 0.5, 0.75, 0.9, 1.0])
    return {
        "min": float(quantiles[0]),
        "q1": float(quantiles[1]),
        "median": float(quantiles[2]),
        "q3": float(quantiles[3]),
        "p90": float(quantiles[4]),
        "max": float(quantiles[5]),
        "mean": float(np.mean(values)),
    }


def analyze_attack_categories(
    y_test: pd.Series,
    attack_categories: pd.Series,
    predictions: np.ndarray,
    probabilities: np.ndarray,
) -> list[dict[str, Any]]:
    analysis_data = pd.DataFrame(
        {
            "actual": y_test.to_numpy(),
            "attack_category": attack_categories.to_numpy(),
            "prediction": predictions,
            "attack_probability": probabilities,
        }
    )
    attack_data = analysis_data[analysis_data["actual"] == 1]
    results: list[dict[str, Any]] = []

    for category, group in attack_data.groupby("attack_category", sort=True):
        total = int(len(group))
        detected = int((group["prediction"] == 1).sum())
        false_negative = total - detected
        results.append(
            {
                "attack_category": str(category),
                "total": total,
                "detected": detected,
                "false_negative": false_negative,
                "recall": detected / total,
                "false_negative_rate": false_negative / total,
                "mean_attack_probability": float(group["attack_probability"].mean()),
                "median_attack_probability": float(group["attack_probability"].median()),
            }
        )

    return sorted(results, key=lambda item: (item["recall"], -item["total"]))


def analyze_probability_bins(false_positive_probabilities: np.ndarray) -> list[dict[str, Any]]:
    bins = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0000001]
    labels = ["0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]
    categories = pd.cut(
        false_positive_probabilities,
        bins=bins,
        labels=labels,
        right=False,
        include_lowest=True,
    )
    counts = pd.Series(categories).value_counts(sort=False)
    total = len(false_positive_probabilities)
    return [
        {
            "probability_range": str(label),
            "count": int(counts.get(label, 0)),
            "ratio": int(counts.get(label, 0)) / total if total else 0.0,
        }
        for label in labels
    ]


def analyze_thresholds(
    y_test: pd.Series,
    probabilities: np.ndarray,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        predictions = (probabilities >= threshold).astype(np.int8)
        details = confusion_details(y_test, predictions)
        results.append(
            {
                "threshold": threshold,
                "precision": float(precision_score(y_test, predictions, zero_division=0)),
                "recall": float(recall_score(y_test, predictions, zero_division=0)),
                "f1": float(f1_score(y_test, predictions, zero_division=0)),
                "false_positive_rate": details["false_positive_rate"],
                "false_positive": details["false_positive"],
                "false_negative": details["false_negative"],
            }
        )
    return results


def finite_float(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def analyze_numeric_false_positives(
    raw_test: pd.DataFrame,
    false_positive_mask: np.ndarray,
    true_negative_mask: np.ndarray,
) -> list[dict[str, Any]]:
    excluded_columns = {"id", "label", "attack_cat"}
    numeric_columns = [
        column
        for column in raw_test.columns
        if column not in excluded_columns and is_numeric_dtype(raw_test[column])
    ]
    results: list[dict[str, Any]] = []

    for column in numeric_columns:
        false_positive_values = pd.to_numeric(
            raw_test.loc[false_positive_mask, column], errors="coerce"
        ).dropna()
        true_negative_values = pd.to_numeric(
            raw_test.loc[true_negative_mask, column], errors="coerce"
        ).dropna()
        if false_positive_values.empty or true_negative_values.empty:
            continue

        fp_mean = float(false_positive_values.mean())
        tn_mean = float(true_negative_values.mean())
        fp_variance = float(false_positive_values.var(ddof=1))
        tn_variance = float(true_negative_values.var(ddof=1))
        pooled_standard_deviation = math.sqrt(max((fp_variance + tn_variance) / 2.0, 0.0))
        mean_difference = fp_mean - tn_mean
        standardized_difference = (
            mean_difference / pooled_standard_deviation
            if pooled_standard_deviation > 0.0
            else 0.0
        )

        results.append(
            {
                "feature": column,
                "false_positive_mean": finite_float(fp_mean),
                "true_negative_mean": finite_float(tn_mean),
                "false_positive_median": finite_float(false_positive_values.median()),
                "true_negative_median": finite_float(true_negative_values.median()),
                "standardized_mean_difference": finite_float(standardized_difference),
                "absolute_standardized_mean_difference": abs(standardized_difference),
            }
        )

    results.sort(key=lambda item: item["absolute_standardized_mean_difference"], reverse=True)
    return results[:TOP_FEATURE_COUNT]


def category_group_rows(
    raw_values: pd.Series,
    false_positive_mask: np.ndarray,
    normal_mask: np.ndarray,
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(
        {
            "category": normalize_category(raw_values[normal_mask]).to_numpy(),
            "is_false_positive": false_positive_mask[normal_mask].astype(np.int8),
        }
    )
    rows: list[dict[str, Any]] = []
    for category, group in frame.groupby("category", sort=True):
        total = int(len(group))
        false_positive = int(group["is_false_positive"].sum())
        rows.append(
            {
                "value": str(category),
                "normal_count": total,
                "false_positive": false_positive,
                "false_positive_rate": false_positive / total,
            }
        )
    return rows


def analyze_categorical_false_positives(
    raw_test: pd.DataFrame,
    false_positive_mask: np.ndarray,
    normal_mask: np.ndarray,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for column in ["proto", "service", "state"]:
        if column not in raw_test.columns:
            continue
        rows = category_group_rows(raw_test[column], false_positive_mask, normal_mask)
        by_count = sorted(rows, key=lambda item: (-item["false_positive"], -item["normal_count"]))
        supported = [row for row in rows if row["normal_count"] >= MIN_CATEGORY_SUPPORT]
        by_rate = sorted(
            supported,
            key=lambda item: (-item["false_positive_rate"], -item["normal_count"]),
        )
        results[column] = {
            "unique_value_count": len(rows),
            "minimum_support_for_rate_ranking": MIN_CATEGORY_SUPPORT,
            "top_by_false_positive_count": by_count[:TOP_FEATURE_COUNT],
            "top_by_false_positive_rate": by_rate[:TOP_FEATURE_COUNT],
        }
    return results


def analyze_coefficients(model: Any, feature_names: list[str]) -> dict[str, Any]:
    coefficients = np.asarray(model.coef_).reshape(-1)
    if len(coefficients) != len(feature_names):
        raise ValueError("모델 계수 개수와 feature 이름 개수가 다릅니다.")

    rows = [
        {"feature": feature, "coefficient": float(coefficient)}
        for feature, coefficient in zip(feature_names, coefficients, strict=True)
    ]
    return {
        "positive_attack_direction": sorted(
            rows, key=lambda item: item["coefficient"], reverse=True
        )[:TOP_FEATURE_COUNT],
        "negative_normal_direction": sorted(rows, key=lambda item: item["coefficient"])[
            :TOP_FEATURE_COUNT
        ],
        "interpretation_caution": (
            "양수 계수는 attack 방향, 음수 계수는 normal 방향에 기여한다. "
            "상관관계가 있는 feature와 one-hot feature가 있으므로 계수를 인과관계로 해석하면 안 된다."
        ),
    }


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    absolute = abs(value)
    if absolute >= 1_000_000 or (0 < absolute < 0.001):
        return f"{value:.3e}"
    return f"{value:.3f}"


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def build_markdown_report(result: dict[str, Any]) -> str:
    overall = result["overall"]
    attack_rows = result["attack_category_performance"]
    false_positive = result["false_positive_analysis"]
    false_negative = result["false_negative_analysis"]
    thresholds = result["threshold_sensitivity_descriptive_only"]
    coefficients = result["model_coefficients"]

    weakest_category = attack_rows[0]
    high_confidence_fp = false_positive["high_confidence_false_positive"]

    sections = [
        "# Logistic Regression 오분류 분석",
        "",
        "## 분석 목적",
        "",
        (
            "UNSW-NB15 공식 test split에서 Logistic Regression baseline이 어떤 공격 유형을 "
            "놓치고, 어떤 정상 트래픽을 공격으로 오판하는지 분석한다. 이 결과는 다음 "
            "RandomForest 실험의 가설과 비교 기준으로 사용한다."
        ),
        "",
        "## 실험 조건",
        "",
        f"- 모델: `{result['model']['name']}`",
        f"- test row 수: `{result['data']['test_rows']:,}`",
        f"- feature 수: `{result['data']['feature_count']}`",
        f"- 분류 threshold: `{result['model']['default_threshold']}`",
        "- 정상 label: `0`, 공격 label: `1`",
        "",
        "## 전체 오분류 현황",
        "",
        markdown_table(
            ["항목", "값"],
            [
                ["True Negative", f"{overall['true_negative']:,}"],
                ["False Positive", f"{overall['false_positive']:,}"],
                ["False Negative", f"{overall['false_negative']:,}"],
                ["True Positive", f"{overall['true_positive']:,}"],
                ["False Positive Rate", format_percent(overall["false_positive_rate"])],
                ["False Negative Rate", format_percent(overall["false_negative_rate"])],
                ["Specificity", format_percent(overall["specificity"])],
                ["Recall", format_percent(overall["recall"])],
            ],
        ),
        "",
        (
            f"공격 Recall은 `{format_percent(overall['recall'])}`로 높지만, 정상 "
            f"{overall['true_negative'] + overall['false_positive']:,}건 중 "
            f"{overall['false_positive']:,}건을 공격으로 오판했다. 따라서 현재 모델의 "
            "핵심 한계는 공격 누락보다 정상 트래픽 오탐에 있다."
        ),
        "",
        "## 공격 유형별 Recall",
        "",
        markdown_table(
            ["공격 유형", "전체", "탐지", "FN", "Recall", "평균 공격 확률"],
            [
                [
                    row["attack_category"],
                    f"{row['total']:,}",
                    f"{row['detected']:,}",
                    f"{row['false_negative']:,}",
                    format_percent(row["recall"]),
                    f"{row['mean_attack_probability']:.4f}",
                ]
                for row in attack_rows
            ],
        ),
        "",
        (
            f"가장 낮은 Recall은 `{weakest_category['attack_category']}`의 "
            f"`{format_percent(weakest_category['recall'])}`이다. 다만 이 유형의 표본은 "
            f"`{weakest_category['total']:,}`건이므로, 표본 수가 적은 공격 유형의 수치는 "
            "변동성이 클 수 있다."
        ),
        "",
        "## False Negative 확률 분포",
        "",
        markdown_table(
            ["통계", "공격 확률"],
            [
                ["최솟값", f"{false_negative['probability_summary']['min']:.4f}"],
                ["1사분위수", f"{false_negative['probability_summary']['q1']:.4f}"],
                ["중앙값", f"{false_negative['probability_summary']['median']:.4f}"],
                ["3사분위수", f"{false_negative['probability_summary']['q3']:.4f}"],
                ["최댓값", f"{false_negative['probability_summary']['max']:.4f}"],
            ],
        ),
        "",
        "## False Positive 신뢰도",
        "",
        markdown_table(
            ["공격 확률 구간", "FP 수", "FP 내 비율"],
            [
                [
                    row["probability_range"],
                    f"{row['count']:,}",
                    format_percent(row["ratio"]),
                ]
                for row in false_positive["probability_bins"]
            ],
        ),
        "",
        (
            f"FP 중 공격 확률이 0.9 이상인 고신뢰 오탐은 "
            f"`{high_confidence_fp['count']:,}`건, "
            f"`{format_percent(high_confidence_fp['ratio'])}`이다. 확률이 0.5 부근인 "
            "오탐은 threshold 조정 가능성을 시사하지만, 고신뢰 오탐은 단순 threshold "
            "조정만으로 해결하기 어려울 수 있다."
        ),
        "",
        "## Threshold 변화 참고",
        "",
        markdown_table(
            ["Threshold", "Precision", "Recall", "F1", "FPR", "FP", "FN"],
            [
                [
                    f"{row['threshold']:.1f}",
                    format_percent(row["precision"]),
                    format_percent(row["recall"]),
                    format_percent(row["f1"]),
                    format_percent(row["false_positive_rate"]),
                    f"{row['false_positive']:,}",
                    f"{row['false_negative']:,}",
                ]
                for row in thresholds
            ],
        ),
        "",
        (
            "이 표는 test 데이터에서 threshold 변화의 경향을 설명하기 위한 분석이다. "
            "test 결과를 보고 최종 threshold를 선택하면 data leakage가 발생하므로, 실제 "
            "threshold 선택은 train 내부 validation split에서 수행해야 한다."
        ),
        "",
        "## FP와 TN의 주요 숫자형 feature 차이",
        "",
        markdown_table(
            ["Feature", "FP 평균", "TN 평균", "FP 중앙값", "TN 중앙값", "표준화 평균 차이"],
            [
                [
                    row["feature"],
                    format_number(row["false_positive_mean"]),
                    format_number(row["true_negative_mean"]),
                    format_number(row["false_positive_median"]),
                    format_number(row["true_negative_median"]),
                    format_number(row["standardized_mean_difference"]),
                ]
                for row in false_positive["top_numeric_feature_differences"][:10]
            ],
        ),
        "",
        (
            "표준화 평균 차이의 절댓값이 클수록 FP와 TN 사이의 분포 차이가 크다. "
            "이는 연관성을 보여주는 탐색 지표이며 해당 feature가 오탐의 직접 원인이라는 "
            "뜻은 아니다."
        ),
        "",
        "## 범주형 feature별 오탐 집중 구간",
        "",
    ]

    for column, category_result in false_positive["categorical_feature_analysis"].items():
        sections.extend(
            [
                f"### {column}",
                "",
                markdown_table(
                    ["값", "정상 데이터", "FP", "FPR"],
                    [
                        [
                            row["value"],
                            f"{row['normal_count']:,}",
                            f"{row['false_positive']:,}",
                            format_percent(row["false_positive_rate"]),
                        ]
                        for row in category_result["top_by_false_positive_count"][:8]
                    ],
                ),
                "",
            ]
        )

    sections.extend(
        [
            "## Logistic Regression 계수",
            "",
            "### Attack 방향 계수 상위",
            "",
            markdown_table(
                ["Feature", "계수"],
                [
                    [row["feature"], f"{row['coefficient']:.6f}"]
                    for row in coefficients["positive_attack_direction"][:10]
                ],
            ),
            "",
            "### Normal 방향 계수 상위",
            "",
            markdown_table(
                ["Feature", "계수"],
                [
                    [row["feature"], f"{row['coefficient']:.6f}"]
                    for row in coefficients["negative_normal_direction"][:10]
                ],
            ),
            "",
            coefficients["interpretation_caution"],
            "",
            "## 한계와 RandomForest 실험 근거",
            "",
            "1. Logistic Regression은 높은 공격 Recall을 달성했지만 정상 트래픽 FPR이 높다.",
            "2. 공격 유형별 Recall 차이가 있어 전체 평균만으로는 취약 유형을 확인하기 어렵다.",
            "3. 원본 feature와 범주 조합에 따라 오탐이 집중된다면 선형 결정 경계의 표현력 한계일 가능성이 있다.",
            "4. RandomForest는 feature 간 비선형 관계와 조건 조합을 표현할 수 있으므로 FP 감소 여부를 검증할 가치가 있다.",
            "5. 다음 모델도 동일한 공식 train/test split과 지표를 사용해 공정하게 비교해야 한다.",
            "",
            "이 분석은 RandomForest가 더 우수하다는 결론이 아니라, 다음 실험에서 검증할 가설을 만든 결과이다.",
            "",
        ]
    )
    return "\n".join(sections)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    required_files = [
        X_TEST_FILE,
        Y_TEST_FILE,
        ATTACK_CATEGORY_FILE,
        RAW_TEST_FILE,
        MODEL_FILE,
    ]
    require_files(required_files)

    print("[Error Analysis] 모델과 test 데이터를 불러옵니다.")
    model, x_test, y_test, attack_categories, raw_test = load_inputs()
    validate_inputs(model, x_test, y_test, attack_categories, raw_test)

    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = (probabilities >= DEFAULT_THRESHOLD).astype(np.int8)
    overall = confusion_details(y_test, predictions)

    y_values = y_test.to_numpy()
    normal_mask = y_values == 0
    attack_mask = y_values == 1
    false_positive_mask = normal_mask & (predictions == 1)
    true_negative_mask = normal_mask & (predictions == 0)
    false_negative_mask = attack_mask & (predictions == 0)

    false_positive_probabilities = probabilities[false_positive_mask]
    false_negative_probabilities = probabilities[false_negative_mask]
    attack_category_results = analyze_attack_categories(
        y_test,
        attack_categories,
        predictions,
        probabilities,
    )
    high_confidence_fp_count = int((false_positive_probabilities >= 0.9).sum())

    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis": "Logistic Regression misclassification analysis",
        "model": {
            "name": type(model).__name__,
            "path": MODEL_FILE.relative_to(AI_ROOT).as_posix(),
            "default_threshold": DEFAULT_THRESHOLD,
        },
        "data": {
            "dataset": "UNSW-NB15",
            "split": "official testing split",
            "test_rows": int(len(x_test)),
            "feature_count": int(x_test.shape[1]),
            "normal_rows": int(normal_mask.sum()),
            "attack_rows": int(attack_mask.sum()),
        },
        "overall": overall,
        "attack_category_performance": attack_category_results,
        "false_negative_analysis": {
            "count": int(false_negative_mask.sum()),
            "probability_summary": probability_summary(false_negative_probabilities),
        },
        "false_positive_analysis": {
            "count": int(false_positive_mask.sum()),
            "rate_among_normal": overall["false_positive_rate"],
            "probability_summary": probability_summary(false_positive_probabilities),
            "probability_bins": analyze_probability_bins(false_positive_probabilities),
            "high_confidence_false_positive": {
                "threshold": 0.9,
                "count": high_confidence_fp_count,
                "ratio": high_confidence_fp_count / len(false_positive_probabilities),
            },
            "top_numeric_feature_differences": analyze_numeric_false_positives(
                raw_test,
                false_positive_mask,
                true_negative_mask,
            ),
            "categorical_feature_analysis": analyze_categorical_false_positives(
                raw_test,
                false_positive_mask,
                normal_mask,
            ),
        },
        "threshold_sensitivity_descriptive_only": analyze_thresholds(y_test, probabilities),
        "threshold_selection_warning": (
            "이 결과는 test 데이터의 설명용 분석이다. 최종 threshold는 train 내부 validation "
            "split에서 선택하고 test에는 한 번만 적용해야 한다."
        ),
        "model_coefficients": analyze_coefficients(model, list(x_test.columns)),
        "next_experiment_hypotheses": [
            "RandomForest가 비선형 feature 관계를 학습해 정상 트래픽 False Positive를 줄일 수 있는지 검증한다.",
            "공격 유형별 Recall을 비교해 희소 공격 유형의 탐지 성능이 개선되는지 검증한다.",
            "성능 개선과 함께 추론 지연시간 및 모델 크기 증가를 측정한다.",
        ],
        "outputs": {
            "json_report": JSON_REPORT_FILE.relative_to(AI_ROOT).as_posix(),
            "markdown_report": MARKDOWN_REPORT_FILE.relative_to(AI_ROOT).as_posix(),
        },
    }

    save_json(JSON_REPORT_FILE, result)
    MARKDOWN_REPORT_FILE.write_text(build_markdown_report(result), encoding="utf-8")

    weakest = attack_category_results[0]
    print("[Error Analysis] 분석이 완료되었습니다.")
    print(f"  - False Positive: {overall['false_positive']}")
    print(f"  - False Positive Rate: {overall['false_positive_rate']:.6f}")
    print(f"  - False Negative: {overall['false_negative']}")
    print(
        f"  - 최저 Recall 공격 유형: {weakest['attack_category']} "
        f"({weakest['recall']:.6f})"
    )
    print(f"  - JSON report: {JSON_REPORT_FILE}")
    print(f"  - Markdown report: {MARKDOWN_REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
