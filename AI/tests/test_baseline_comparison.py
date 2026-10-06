from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from evaluation import compare_baselines as comparison
from evaluation.analyze_logistic_errors import analyze_attack_categories


class BaselineComparisonTests(unittest.TestCase):
    def test_error_changes_match_confusion_matrix_deltas(self) -> None:
        actual = pd.Series([0, 0, 0, 1, 1, 1, 1])
        lr = np.array([1, 1, 0, 0, 1, 1, 1])
        rf = np.array([0, 0, 1, 1, 0, 0, 1])
        changes = comparison.compare_error_changes(actual, lr, rf)
        self.assertEqual(changes, {
            "false_positives_fixed": 2, "new_false_positives": 1,
            "false_negatives_fixed": 1, "new_false_negatives": 2,
        })
        self.assertEqual(changes["new_false_positives"] - changes["false_positives_fixed"], -1)
        self.assertEqual(changes["new_false_negatives"] - changes["false_negatives_fixed"], 1)

    def test_category_comparison_joins_by_name_not_row_position(self) -> None:
        actual = pd.Series([0, 1, 1, 1])
        categories = pd.Series(["Normal", "Analysis", "Analysis", "Worms"])
        lr = analyze_attack_categories(actual, categories, np.array([0, 1, 0, 1]), np.array([0.1, 0.8, 0.2, 0.9]))
        rf = analyze_attack_categories(actual, categories, np.array([0, 1, 1, 0]), np.array([0.1, 0.8, 0.9, 0.2]))
        rows = comparison.compare_attack_categories(lr, rf)
        self.assertEqual(rows[0]["attack_category"], "Analysis")
        self.assertEqual(rows[0]["recall_delta_percentage_points"], 50.0)
        self.assertEqual(rows[1]["recall_delta_percentage_points"], -100.0)
        self.assertEqual(sum(row["random_forest_false_negative"] for row in rows), 1)

    def test_category_comparison_rejects_different_support(self) -> None:
        lr = [{"attack_category": "Analysis", "total": 2}]
        rf = [{"attack_category": "Analysis", "total": 3}]
        with self.assertRaisesRegex(ValueError, "표본 수"):
            comparison.compare_attack_categories(lr, rf)

    def test_raw_alignment_catches_reordering_inside_same_label_group(self) -> None:
        raw = pd.DataFrame({"dur": [1.0, 2.0, 3.0], "proto": ["tcp", "udp", "tcp"]})
        preprocessor = ColumnTransformer([
            ("numeric", StandardScaler(), ["dur"]),
            ("categorical", OneHotEncoder(sparse_output=False), ["proto"]),
        ])
        transformed = preprocessor.fit_transform(raw).astype(np.float32)
        saved = pd.DataFrame(transformed, columns=preprocessor.get_feature_names_out())
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "schema.json"
            schema_path.write_text(json.dumps({
                "raw_input_features": ["dur", "proto"], "categorical_features": ["proto"],
            }), encoding="utf-8")
            with patch.object(comparison, "FEATURE_SCHEMA_FILE", schema_path), patch.object(
                comparison.joblib, "load", return_value=preprocessor
            ):
                comparison.validate_raw_feature_alignment(saved, raw)
                with self.assertRaisesRegex(ValueError, "row 순서"):
                    comparison.validate_raw_feature_alignment(saved.iloc[::-1].reset_index(drop=True), raw)


if __name__ == "__main__":
    unittest.main()
