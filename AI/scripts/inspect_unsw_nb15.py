from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = AI_ROOT / "data" / "raw" / "unsw_nb15"
REPORT_DIR = AI_ROOT / "reports"
REPORT_PATH = REPORT_DIR / "dataset_profile_unsw_nb15.json"

EXPECTED_FILES = {
    "training": ["UNSW_NB15_training-set.csv"],
    "testing": ["UNSW_NB15_testing-set.csv"],
    "features": [
        "UNSW_NB15_features.csv",
        "UNSW-NB15_features.csv",
        "NUSW-NB15_features.csv",
    ],
}

ENCODING_CANDIDATES = ("utf-8-sig", "cp1252", "cp949", "latin1")
MISSING_TOKENS = {"", "-", "?", "none", "null", "nan"}


def is_missing(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() in MISSING_TOKENS


def can_parse_float(value: str | None) -> bool:
    if is_missing(value):
        return True

    try:
        float(str(value).strip())
        return True
    except ValueError:
        return False


def find_existing_file(candidates: list[str]) -> Path | None:
    for filename in candidates:
        path = RAW_DIR / filename
        if path.exists():
            return path
    return None


def check_expected_files() -> tuple[dict[str, Path], dict[str, list[str]]]:
    paths: dict[str, Path] = {}
    missing: dict[str, list[str]] = {}

    for role, candidates in EXPECTED_FILES.items():
        path = find_existing_file(candidates)
        if path is None:
            missing[role] = candidates
        else:
            paths[role] = path

    return paths, missing


def inspect_csv(path: Path, sample_limit: int) -> dict[str, Any]:
    print(f"\n[EDA] 파일 분석: {path.name}")

    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODING_CANDIDATES:
        try:
            result = inspect_csv_with_encoding(path, sample_limit, encoding)
            result["encoding"] = encoding
            print(f"  - 사용 인코딩: {encoding}")
            return result
        except UnicodeDecodeError as error:
            last_error = error

    raise UnicodeDecodeError(
        last_error.encoding if last_error else "unknown",
        last_error.object if last_error else b"",
        last_error.start if last_error else 0,
        last_error.end if last_error else 0,
        f"지원하는 인코딩으로 파일을 읽지 못했습니다: {path}",
    )


def inspect_csv_with_encoding(path: Path, sample_limit: int, encoding: str) -> dict[str, Any]:
    row_count = 0
    label_counts: Counter[str] = Counter()
    attack_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    numeric_candidates: dict[str, bool] = {}
    columns: list[str] = []

    with path.open("r", encoding=encoding, newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError(f"CSV header를 읽을 수 없습니다: {path}")

        columns = list(reader.fieldnames)
        numeric_candidates = {column: True for column in columns}

        for row in reader:
            row_count += 1

            if "label" in row:
                label = row.get("label", "").strip() or "UNKNOWN"
                label_counts[label] += 1

            if "attack_cat" in row:
                attack_cat = row.get("attack_cat", "").strip() or "Normal/Unknown"
                attack_counts[attack_cat] += 1

            for column in columns:
                value = row.get(column)
                if is_missing(value):
                    missing_counts[column] += 1

                if row_count <= sample_limit and numeric_candidates[column]:
                    numeric_candidates[column] = can_parse_float(value)

    numeric_columns = [column for column, is_numeric in numeric_candidates.items() if is_numeric]
    categorical_columns = [column for column in columns if column not in numeric_columns]
    columns_with_missing = {
        column: count
        for column, count in missing_counts.items()
        if count > 0
    }

    print(f"  - row 수: {row_count}")
    print(f"  - column 수: {len(columns)}")
    print(f"  - 숫자형 후보 feature 수: {len(numeric_columns)}")
    print(f"  - 범주형 후보 feature 수: {len(categorical_columns)}")

    if label_counts:
        print("  - label 분포:")
        for label, count in label_counts.most_common():
            print(f"    {label}: {count}")

    if attack_counts:
        print("  - attack_cat 상위 분포:")
        for attack_cat, count in attack_counts.most_common(10):
            print(f"    {attack_cat}: {count}")

    if columns_with_missing:
        print("  - 결측치가 있는 column:")
        for column, count in sorted(columns_with_missing.items(), key=lambda item: item[1], reverse=True)[:10]:
            print(f"    {column}: {count}")
    else:
        print("  - 결측치가 있는 column: 없음")

    return {
        "file": path.name,
        "rows": row_count,
        "columns": columns,
        "column_count": len(columns),
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "label_counts": dict(label_counts),
        "attack_category_counts": dict(attack_counts),
        "missing_counts": columns_with_missing,
    }


def inspect_feature_description(path: Path) -> dict[str, Any]:
    print(f"\n[EDA] feature 설명 파일 확인: {path.name}")

    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODING_CANDIDATES:
        try:
            result = inspect_feature_description_with_encoding(path, encoding)
            result["encoding"] = encoding
            print(f"  - 사용 인코딩: {encoding}")
            return result
        except UnicodeDecodeError as error:
            last_error = error

    raise UnicodeDecodeError(
        last_error.encoding if last_error else "unknown",
        last_error.object if last_error else b"",
        last_error.start if last_error else 0,
        last_error.end if last_error else 0,
        f"지원하는 인코딩으로 파일을 읽지 못했습니다: {path}",
    )


def inspect_feature_description_with_encoding(path: Path, encoding: str) -> dict[str, Any]:
    with path.open("r", encoding=encoding, newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    print(f"  - 설명 row 수: {len(rows)}")
    print(f"  - column: {fieldnames}")

    return {
        "file": path.name,
        "rows": len(rows),
        "columns": fieldnames,
    }


def print_missing_file_help(missing: dict[str, list[str]]) -> None:
    print("[EDA] UNSW-NB15 원본 파일이 아직 준비되지 않았습니다.")
    print("\n아래 파일을 이 폴더에 배치해 주세요.")
    print(f"\n  {RAW_DIR}")

    for role, candidates in missing.items():
        print(f"\n  [{role}] 다음 파일명 중 하나가 필요합니다.")
        for filename in candidates:
            print(f"  - {filename}")

    print("\n공식 데이터셋 페이지:")
    print("  https://research.unsw.edu.au/projects/unsw-nb15-dataset")
    print("\n파일을 배치한 뒤 다시 실행:")
    print("  python .\\scripts\\inspect_unsw_nb15.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="UNSW-NB15 데이터셋 기본 EDA 스크립트")
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=5000,
        help="숫자형/범주형 feature 추정을 위해 검사할 최대 row 수",
    )
    args = parser.parse_args()

    paths, missing = check_expected_files()
    if missing:
        print_missing_file_help(missing)
        return 1

    profile = {
        "dataset": "UNSW-NB15",
        "raw_dir": str(RAW_DIR),
        "sample_limit": args.sample_limit,
        "files": {},
    }

    profile["files"]["training"] = inspect_csv(paths["training"], args.sample_limit)
    profile["files"]["testing"] = inspect_csv(paths["testing"], args.sample_limit)
    profile["files"]["features"] = inspect_feature_description(paths["features"])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n[EDA] 분석 결과 저장 완료: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
