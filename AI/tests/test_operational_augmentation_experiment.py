from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

import test_frozen_operational_baseline as fixtures
from data_collection.quality import read_json
from data_preparation.operational_dataset import file_hash
from training.operational_data import load_operational_data
from training.run_operational_validation_experiment import ExperimentConfig
import training.run_operational_augmentation_experiment as augmentation


class AugmentationExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        # 기존 임시 원본·기준 모델 fixture를 재사용한다. 사용자 데이터나 모델은 건드리지 않는다.
        self.fixture = fixtures.FrozenBaselineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.directory
        self.fixture.audit()
        run_dir = self.directory / "augmentation"
        self.config = augmentation.AugmentationConfig(
            run_dir=run_dir, reference_experiment_dir=self.fixture.reference,
            reference_audit_report=self.fixture.prefix.with_suffix(".json"),
            comparison_report_prefix=self.directory / "comparison", expected_train_rows=6, expected_validation_rows=6,
            experiment=ExperimentConfig(
                experiment_id="fixture_augmentation", dataset_id="fixture_v2", dataset_dir=self.fixture.dataset,
                artifact_dir=run_dir / "validation_selection", report_prefix=self.directory / "selection_report",
                thresholds=[0.5, 0.7], selection_rule={"max_false_positive_rate": 1.0, "min_recall": 0.0},
                common_parameters={"rf_n_estimators": 7}, candidates=[
                    {"id": "lr_c_1", "kind": "logistic_regression", "C": 1.0},
                    {"id": "rf_leaf_1", "kind": "random_forest", "min_samples_leaf": 1},
                ],
            ),
        )

    def config_with(self, **overrides):
        values = self.config.model_dump()
        values.update(overrides)
        return augmentation.AugmentationConfig.model_validate(values)

    def execute(self, config=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return augmentation.run(config or self.config)

    def test_default_protocol_has_limited_six_candidates_and_42_trials(self) -> None:
        config = augmentation.load_config(AI_ROOT / "configs" / "operational_augmentation_experiment.json")
        self.assertEqual(len(config.experiment.candidates), 6)
        self.assertEqual(len(config.experiment.thresholds), 7)
        self.assertEqual(config.experiment.selection_rule.max_false_positive_rate, 0.1)
        self.assertEqual(config.experiment.selection_rule.min_recall, 0.5)
        self.assertEqual(config.expected_train_rows, 1538)
        self.assertEqual(config.expected_validation_rows, 513)

    def test_full_protocol_is_locked_before_fit_and_only_new_train_is_fitted(self) -> None:
        data = load_operational_data(self.fixture.dataset, "fixture_v2")
        original = Pipeline.fit
        calls = []

        def fit(pipeline, X, y, **kwargs):
            outer = read_json(self.config.run_dir / "augmentation_protocol_lock.json")
            inner = read_json(self.config.experiment.artifact_dir / "protocol_lock.json")
            self.assertEqual(outer["status"], "locked_before_fit")
            self.assertEqual(inner["status"], "locked_before_fit")
            self.assertEqual(outer["protocol"]["experiment"], inner["protocol"])
            self.assertEqual(outer["validation_prior_use"], "frozen_baseline_audit_completed")
            self.assertEqual(outer["input_sha256"], data.input_sha256)
            pd.testing.assert_frame_equal(X, data.train.X)
            np.testing.assert_array_equal(y, data.train.y)
            calls.append(len(y))
            return original(pipeline, X, y, **kwargs)

        with patch.object(Pipeline, "fit", fit):
            report = self.execute()
        self.assertEqual(calls, [6, 6])
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(report["trial_count"], 4)
        self.assertFalse(report["refit_with_validation"])
        pipeline = joblib.load(self.config.experiment.artifact_dir / "candidates/lr_c_1/pipeline.joblib")
        self.assertEqual(int(pipeline.named_steps["scaler"].n_samples_seen_), 6)
        np.testing.assert_allclose(pipeline.named_steps["scaler"].mean_, data.train.X.mean().to_numpy())
        np.testing.assert_allclose(pipeline.named_steps["scaler"].var_, data.train.X.var(ddof=0).to_numpy())

    def test_test_csv_legacy_csv_jsonl_summary_and_traffic_are_not_used(self) -> None:
        original = Path.open

        def guarded(path, *args, **kwargs):
            if (path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "collection_summary.json"} or
                    path.suffix == ".jsonl" or ("legacy_dataset" in path.parts and path.suffix == ".csv")):
                raise AssertionError("Test·기존 CSV·JSONL·종료 summary 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded), patch("socket.create_connection", side_effect=AssertionError("트래픽 금지")):
            report = self.execute()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["test_evaluated"])
        self.assertFalse(report["deployment_ready"])

    def test_old_model_and_audit_are_not_overwritten(self) -> None:
        paths = [path for path in self.fixture.reference.rglob("*") if path.is_file()]
        paths += [self.fixture.prefix.with_suffix(suffix) for suffix in (".json", ".md", ".predictions.csv", ".errors.csv")]
        before = {path: file_hash(path) for path in paths}
        self.execute()
        self.assertEqual(before, {path: file_hash(path) for path in paths})

    def test_reference_prediction_hash_and_policy_are_verified_before_fit(self) -> None:
        path = self.fixture.prefix.with_suffix(".predictions.csv")
        with path.open("a", encoding="utf-8") as stream:
            stream.write("changed\n")
        with patch.object(Pipeline, "fit", side_effect=AssertionError("잘못된 기준으로 학습 금지")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.execute()
        self.assertFalse(self.config.run_dir.exists())

    def test_same_row_comparison_has_fixed_policy_arms_and_candidate_leaders(self) -> None:
        report = self.execute()
        models = report["comparison"]["models"]
        self.assertEqual(models["frozen_lr_0_7"]["threshold"], 0.7)
        self.assertEqual(models["augmented_lr_c_1_0_7"]["threshold"], 0.7)
        self.assertEqual(models["augmented_rf_leaf_1_0_7"]["threshold"], 0.7)
        self.assertIn("best_lr_validation", models)
        self.assertIn("best_rf_validation", models)
        for model in models.values():
            self.assertEqual(model["validation_metrics"]["rows"], 6)
            if "transitions_from_frozen" in model:
                transitions = model["transitions_from_frozen"]
                self.assertEqual(transitions["both_correct"] + transitions["both_wrong"] +
                                 sum(transitions[name] for name in ("corrected_fp", "corrected_fn", "new_fp", "new_fn")), 6)
        predictions = pd.read_csv(self.config.run_dir / "comparison_predictions.csv")
        self.assertEqual(len(predictions), 6)
        self.assertEqual(set(predictions.split), {"validation"})
        for arm in models:
            np.testing.assert_array_equal(predictions[f"{arm}__prediction"],
                                          (predictions[f"{arm}__probability"] >= predictions[f"{arm}__threshold"]).astype("int64"))

    def test_no_feasible_candidate_is_complete_without_relaxing_rule(self) -> None:
        core = self.config.experiment.model_dump()
        core["selection_rule"] = {"max_false_positive_rate": 0.0, "min_recall": 1.0}
        config = self.config_with(experiment=core)
        report = self.execute(config)
        self.assertEqual(report["selection"]["status"], "no_feasible_candidate")
        self.assertIsNone(report["comparison"]["selected_arm"])
        self.assertTrue(all(value is None for value in report["comparison"]["best_by_kind"].values()))
        self.assertEqual(read_json(config.run_dir / "augmentation_manifest.json")["status"], "complete")

    def test_existing_and_immutable_output_locations_are_rejected(self) -> None:
        self.execute()
        before = file_hash(self.config.run_dir / "augmentation_manifest.json")
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(before, file_hash(self.config.run_dir / "augmentation_manifest.json"))

    def test_invalid_control_candidates_and_duplicate_report_paths_are_rejected(self) -> None:
        core = self.config.experiment.model_dump()
        core["candidates"] = [core["candidates"][0]]
        with self.assertRaises(ValueError):
            self.config_with(experiment=core)
        config = self.config_with(comparison_report_prefix=self.config.experiment.report_prefix)
        with self.assertRaisesRegex(ValueError, "서로 다른 경로"):
            self.execute(config)
        self.assertFalse(self.config.run_dir.exists())

    def test_incorrect_row_counts_abort_before_outputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "행 수"):
            self.execute(self.config_with(expected_train_rows=1538))
        self.assertFalse(self.config.run_dir.exists())

    def test_outer_lock_change_blocks_full_completion(self) -> None:
        original = Pipeline.fit

        def fit(pipeline, *args, **kwargs):
            result = original(pipeline, *args, **kwargs)
            path = self.config.run_dir / "augmentation_protocol_lock.json"
            path.write_text("{}", encoding="utf-8")
            return result

        with patch.object(Pipeline, "fit", fit), self.assertRaisesRegex(ValueError, "고정 파일"):
            self.execute()
        self.assertFalse((self.config.run_dir / "augmentation_manifest.json").exists())
        self.assertFalse(self.config.comparison_report_prefix.with_suffix(".json").exists())

    def test_full_manifest_inventory_matches_saved_files(self) -> None:
        self.execute()
        manifest = read_json(self.config.run_dir / "augmentation_manifest.json")
        for name, description in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.run_dir / name), description["sha256"])
        for name, digest in manifest["reports"].items():
            self.assertEqual(file_hash(Path(name)), digest)


if __name__ == "__main__":
    unittest.main()
