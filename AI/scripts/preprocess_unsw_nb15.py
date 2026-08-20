from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS_DIR = AI_ROOT / ".deps"

if LOCAL_DEPS_DIR.exists():
    sys.path.insert(0, str(LOCAL_DEPS_DIR))

import joblib
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_object_dtype, is_string_dtype
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


RAW_DIR = AI_ROOT / "data" / "raw" / "unsw_nb15"
PROCESSED_DIR = AI_ROOT / "data" / "processed" / "unsw_nb15"
ARTIFACTS_DIR = AI_ROOT / "artifacts"
REPORTS_DIR = AI_ROOT / "reports"

TRAIN_FILE = RAW_DIR / "UNSW_NB15_training-set.csv"
TEST_FILE = RAW_DIR / "UNSW_NB15_testing-set.csv"

FEATURE_DESCRIPTION_CANDIDATES = [
    RAW_DIR / "UNSW_NB15_features.csv",
    RAW_DIR / "UNSW-NB15_features.csv",
    RAW_DIR / "NUSW-NB15_features.csv",
]

FEATURE_SCHEMA_FILE = ARTIFACTS_DIR / "feature_schema_unsw_nb15.json"
PREPROCESSOR_FILE = ARTIFACTS_DIR / "preprocessor_unsw_nb15.joblib"
LABEL_MAP_FILE = ARTIFACTS_DIR / "label_map_unsw_nb15.json"
SUMMARY_FILE = REPORTS_DIR / "preprocess_summary_unsw_nb15.json"

KNOWN_CATEGORICAL_COLUMNS = {"proto", "service", "state"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UNSW-NB15 데이터셋을 baseline 학습용 형태로 전처리합니다."
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        help="빠른 테스트용 샘플 row 수입니다. 0이면 전체 데이터를 사용합니다.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="샘플링 재현성을 위한 random seed입니다.",
    )
    return parser.parse_args()


def make_one_hot_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def find_feature_description_file() -> Path | None:
    for path in FEATURE_DESCRIPTION_CANDIDATES:
        if path.exists():
            return path
    return None


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"필수 CSV 파일을 찾을 수 없습니다: {path}")
    return pd.read_csv(path)


def normalize_categorical_missing_values(data: pd.DataFrame, categorical_columns: list[str]) -> None:
    missing_tokens = {"", "-", "?", "none", "None", "null", "NULL", "nan", "NaN"}
    for column in categorical_columns:
        data[column] = data[column].replace(list(missing_tokens), pd.NA)


def split_features_and_targets(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    if "label" not in data.columns:
        raise ValueError("UNSW-NB15 CSV에 label column이 없습니다.")
    if "attack_cat" not in data.columns:
        raise ValueError("UNSW-NB15 CSV에 attack_cat column이 없습니다.")

    y_binary = data["label"].astype(int)
    y_attack_category = data["attack_cat"].astype(str)
    x = data.drop(columns=["id", "label", "attack_cat"], errors="ignore")
    return x, y_binary, y_attack_category


def sample_if_requested(
    data: pd.DataFrame,
    sample_size: int,
    random_state: int,
) -> pd.DataFrame:
    if sample_size <= 0 or sample_size >= len(data):
        return data
    return data.sample(n=sample_size, random_state=random_state).reset_index(drop=True)


def build_preprocessor(
    numeric_columns: list[str],
    categorical_columns: list[str],
) -> ColumnTransformer:
    numeric_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
            ("one_hot", make_one_hot_encoder()),
        ]
    )

    return ColumnTransformer(
        transformers=[
            ("numeric", numeric_pipeline, numeric_columns),
            ("categorical", categorical_pipeline, categorical_columns),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )


def infer_feature_types(data: pd.DataFrame) -> tuple[list[str], list[str]]:
    categorical_columns: list[str] = []
    numeric_columns: list[str] = []

    for column in data.columns:
        if column in KNOWN_CATEGORICAL_COLUMNS:
            categorical_columns.append(column)
        elif is_object_dtype(data[column]) or is_string_dtype(data[column]):
            categorical_columns.append(column)
        elif is_numeric_dtype(data[column]):
            numeric_columns.append(column)
        else:
            categorical_columns.append(column)

    return numeric_columns, categorical_columns


def get_feature_names(preprocessor: ColumnTransformer) -> list[str]:
    return [str(name) for name in preprocessor.get_feature_names_out()]


def save_processed_csv(
    x_train_processed: pd.DataFrame,
    x_test_processed: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    attack_train: pd.Series,
    attack_test: pd.Series,
) -> dict[str, str]:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    paths = {
        "x_train": PROCESSED_DIR / "X_train.csv",
        "x_test": PROCESSED_DIR / "X_test.csv",
        "y_train": PROCESSED_DIR / "y_train.csv",
        "y_test": PROCESSED_DIR / "y_test.csv",
        "attack_train": PROCESSED_DIR / "attack_cat_train.csv",
        "attack_test": PROCESSED_DIR / "attack_cat_test.csv",
    }

    x_train_processed.to_csv(paths["x_train"], index=False)
    x_test_processed.to_csv(paths["x_test"], index=False)
    y_train.rename("label").to_csv(paths["y_train"], index=False)
    y_test.rename("label").to_csv(paths["y_test"], index=False)
    attack_train.rename("attack_cat").to_csv(paths["attack_train"], index=False)
    attack_test.rename("attack_cat").to_csv(paths["attack_test"], index=False)

    return {name: str(path) for name, path in paths.items()}


def count_missing_values(data: pd.DataFrame) -> dict[str, int]:
    counts = data.isna().sum()
    return {column: int(count) for column, count in counts.items() if int(count) > 0}


def build_summary(
    train_raw: pd.DataFrame,
    test_raw: pd.DataFrame,
    x_train: pd.DataFrame,
    x_test: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    numeric_columns: list[str],
    categorical_columns: list[str],
    processed_feature_names: list[str],
    processed_paths: dict[str, str],
    sample_size: int,
) -> dict[str, Any]:
    return {
        "dataset": "UNSW-NB15",
        "sample_size": sample_size,
        "raw": {
            "train_rows": int(len(train_raw)),
            "test_rows": int(len(test_raw)),
            "train_columns": int(len(train_raw.columns)),
            "test_columns": int(len(test_raw.columns)),
        },
        "model_input": {
            "train_rows": int(len(x_train)),
            "test_rows": int(len(x_test)),
            "raw_feature_count": int(len(x_train.columns)),
            "processed_feature_count": int(len(processed_feature_names)),
            "numeric_feature_count": int(len(numeric_columns)),
            "categorical_feature_count": int(len(categorical_columns)),
        },
        "features": {
            "numeric": numeric_columns,
            "categorical": categorical_columns,
            "dropped": ["id", "label", "attack_cat"],
            "processed": processed_feature_names,
        },
        "targets": {
            "binary_label_distribution_train": {
                str(label): int(count) for label, count in y_train.value_counts().sort_index().items()
            },
            "binary_label_distribution_test": {
                str(label): int(count) for label, count in y_test.value_counts().sort_index().items()
            },
        },
        "missing_values_after_token_normalization": {
            "train": count_missing_values(x_train),
            "test": count_missing_values(x_test),
        },
        "outputs": {
            "processed_csv": processed_paths,
            "preprocessor": str(PREPROCESSOR_FILE),
            "feature_schema": str(FEATURE_SCHEMA_FILE),
            "label_map": str(LABEL_MAP_FILE),
            "summary": str(SUMMARY_FILE),
        },
    }


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()

    train_raw = read_csv(TRAIN_FILE)
    test_raw = read_csv(TEST_FILE)

    train_raw = sample_if_requested(train_raw, args.sample_size, args.random_state)
    test_raw = sample_if_requested(test_raw, args.sample_size, args.random_state)

    x_train, y_train, attack_train = split_features_and_targets(train_raw)
    x_test, y_test, attack_test = split_features_and_targets(test_raw)

    numeric_columns, categorical_columns = infer_feature_types(x_train)

    normalize_categorical_missing_values(x_train, categorical_columns)
    normalize_categorical_missing_values(x_test, categorical_columns)

    preprocessor = build_preprocessor(numeric_columns, categorical_columns)
    train_array = preprocessor.fit_transform(x_train)
    test_array = preprocessor.transform(x_test)
    processed_feature_names = get_feature_names(preprocessor)

    x_train_processed = pd.DataFrame(train_array, columns=processed_feature_names)
    x_test_processed = pd.DataFrame(test_array, columns=processed_feature_names)

    processed_paths = save_processed_csv(
        x_train_processed,
        x_test_processed,
        y_train,
        y_test,
        attack_train,
        attack_test,
    )

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(preprocessor, PREPROCESSOR_FILE)

    feature_description_file = find_feature_description_file()
    feature_schema = {
        "dataset": "UNSW-NB15",
        "target": "label",
        "secondary_target": "attack_cat",
        "raw_input_features": list(x_train.columns),
        "numeric_features": numeric_columns,
        "categorical_features": categorical_columns,
        "processed_features": processed_feature_names,
        "dropped_columns": ["id", "label", "attack_cat"],
        "feature_description_file": str(feature_description_file) if feature_description_file else None,
    }
    label_map = {"normal": 0, "attack": 1}

    save_json(FEATURE_SCHEMA_FILE, feature_schema)
    save_json(LABEL_MAP_FILE, label_map)

    summary = build_summary(
        train_raw=train_raw,
        test_raw=test_raw,
        x_train=x_train,
        x_test=x_test,
        y_train=y_train,
        y_test=y_test,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        processed_feature_names=processed_feature_names,
        processed_paths=processed_paths,
        sample_size=args.sample_size,
    )
    save_json(SUMMARY_FILE, summary)

    print("[Preprocess] UNSW-NB15 전처리 완료")
    print(f"  - train rows: {len(x_train_processed)}")
    print(f"  - test rows: {len(x_test_processed)}")
    print(f"  - raw feature count: {len(x_train.columns)}")
    print(f"  - processed feature count: {len(processed_feature_names)}")
    print(f"  - processed dir: {PROCESSED_DIR}")
    print(f"  - preprocessor: {PREPROCESSOR_FILE}")
    print(f"  - summary: {SUMMARY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
