from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd

from training.run_validation_experiment import (
    choose_trial, evaluate_threshold, fit_training_preprocessor, grouped_split, load_raw_training,
)


class ValidationExperimentTests(unittest.TestCase):
    def test_groups_are_disjoint_reproducible_and_cover_all_rows(self) -> None:
        features = pd.DataFrame({
            "dur": np.repeat(np.arange(30), 3),
            "proto": ["tcp"] * 90,
            "service": ["-", None, "-"] * 30,
        })
        categories = pd.Series(np.repeat(["Normal", "Exploits"] * 15, 3))
        options = {"n_splits": 5, "validation_fold": 0, "random_state": 42}
        train, validation, groups = grouped_split(features, categories, options)
        repeated_train, repeated_validation, _ = grouped_split(features, categories, options)
        np.testing.assert_array_equal(train, repeated_train)
        np.testing.assert_array_equal(validation, repeated_validation)
        self.assertFalse(set(groups[train]) & set(groups[validation]))
        self.assertFalse(set(train) & set(validation))
        self.assertEqual(set(train) | set(validation), set(range(len(features))))
        self.assertEqual(len(np.unique(groups)), 30)

    def test_scaler_imputer_and_encoder_fit_training_only(self) -> None:
        training = pd.DataFrame({"dur": [10.0, 20.0, 30.0, np.nan], "proto": ["tcp"] * 4})
        validation = pd.DataFrame({"dur": [1000.0, np.nan], "proto": ["validation_only"] * 2})
        preprocessor, train_data, validation_data, _, _ = fit_training_preprocessor(training, validation)
        numeric = preprocessor.named_transformers_["numeric"]
        self.assertEqual(numeric.named_steps["imputer"].statistics_[0], 20.0)
        self.assertEqual(numeric.named_steps["scaler"].mean_[0], 20.0)
        encoder = preprocessor.named_transformers_["categorical"].named_steps["one_hot"]
        self.assertEqual(encoder.categories_[0].tolist(), ["tcp"])
        self.assertNotIn("categorical__proto_validation_only", train_data.columns)
        self.assertTrue((validation_data["categorical__proto_tcp"] == 0).all())
        self.assertEqual(validation_data.loc[1, "numeric__dur"], 0.0)
        self.assertTrue(np.isfinite(validation_data.to_numpy()).all())

    def test_selection_maximizes_recall_under_constraint(self) -> None:
        trials = [
            self.trial("high_f1", 0.5, 0.90, 0.94, 0.04),
            self.trial("high_recall", 0.3, 0.95, 0.91, 0.09),
            self.trial("over_limit", 0.3, 0.99, 0.98, 0.11),
            self.trial("not_converged", 0.3, 0.99, 0.99, 0.01, converged=False),
        ]
        self.assertEqual(choose_trial(trials, 0.10)["candidate_id"], "high_recall")

    def test_no_feasible_candidate_does_not_relax_constraint(self) -> None:
        self.assertIsNone(choose_trial([self.trial("candidate", 0.5, 0.99, 0.99, 0.11)], 0.10))
        self.assertIsNone(choose_trial([], 0.10))

    def test_tie_break_is_deterministic_and_limit_is_inclusive(self) -> None:
        trials = [
            self.trial("b", 0.3, 0.95, 0.95, 0.10),
            self.trial("a", 0.7, 0.95, 0.95, 0.10),
            self.trial("a", 0.5, 0.95, 0.95, 0.10),
        ]
        for order in [trials, list(reversed(trials))]:
            selected = choose_trial(order, 0.10)
            self.assertEqual((selected["candidate_id"], selected["threshold"]), ("a", 0.5))

    def test_threshold_boundary_and_error_tradeoff(self) -> None:
        actual = pd.Series([0, 0, 1, 1])
        probabilities = np.array([0.2, 0.5, 0.5, 0.8])
        low = evaluate_threshold(actual, probabilities, 0.5)
        high = evaluate_threshold(actual, probabilities, 0.7)
        self.assertEqual(low["confusion_matrix"], [[1, 1], [0, 2]])
        self.assertEqual(high["confusion_matrix"], [[2, 0], [1, 1]])

    def test_invalid_label_is_rejected_before_integer_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.csv"
            pd.DataFrame({"dur": [1, 2], "label": [0, 257], "attack_cat": ["Normal", "Exploits"]}).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "label"):
                load_raw_training(path)

    @staticmethod
    def trial(name: str, threshold: float, recall: float, f1: float, fpr: float, converged: bool = True) -> dict:
        return {
            "candidate_id": name, "threshold": threshold, "converged": converged,
            "metrics": {"recall": recall, "f1": f1, "false_positive_rate": fpr},
        }


if __name__ == "__main__":
    unittest.main()
