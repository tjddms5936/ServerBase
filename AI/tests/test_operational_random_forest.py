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

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

import test_operational_dataset as dataset_fixtures
from data_preparation.operational_dataset import DatasetConfig, FEATURE_NAMES, build_dataset, file_hash
from evaluation.compare_operational_baselines import compare_models, prediction_transitions
from training.operational_data import load_operational_data
import training.train_operational_logistic_regression as lr_training
import training.train_operational_random_forest as rf_training


class OperationalRandomForestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational_rf_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        campaign = dataset_fixtures.make_campaign(self.directory)
        self.dataset_dir = self.directory / "dataset"
        build_dataset(DatasetConfig(dataset_id="fixture_v1", campaign_dirs=[campaign], output_dir=self.dataset_dir))
        self.lr_config = lr_training.TrainingConfig(dataset_id="fixture_v1", dataset_dir=self.dataset_dir,
                                                    artifact_dir=self.directory / "lr", report_prefix=self.directory / "lr_report")
        # 단위 테스트만 트리 수를 줄인다. 실제 초기 실험 설정은 100개로 고정한다.
        self.rf_config = rf_training.TrainingConfig(dataset_id="fixture_v1", dataset_dir=self.dataset_dir,
                                                    artifact_dir=self.directory / "rf", report_prefix=self.directory / "rf_report",
                                                    n_estimators=7)
        self.comparison_prefix = self.directory / "comparison"

    def prepare_models(self) -> None:
        lr_training.train_model(self.lr_config)
        rf_training.train_model(self.rf_config)

    def compare(self) -> dict:
        return compare_models(self.dataset_dir, "fixture_v1", self.lr_config.artifact_dir,
                              self.rf_config.artifact_dir, self.comparison_prefix)

    def refresh_artifact(self, name: str) -> None:
        path = self.rf_config.artifact_dir / "training_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["artifacts"][name]["sha256"] = file_hash(self.rf_config.artifact_dir / name)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_rf_fits_once_on_unscaled_train_only(self) -> None:
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        original = RandomForestClassifier.fit
        calls = []

        def tracked_fit(model, X, y, **kwargs):
            calls.append((X.copy(), y.copy()))
            return original(model, X, y, **kwargs)

        with patch.object(RandomForestClassifier, "fit", tracked_fit):
            report = rf_training.train_model(self.rf_config)
        self.assertEqual(len(calls), 1)
        pd.testing.assert_frame_equal(calls[0][0], data.train.X)
        np.testing.assert_array_equal(calls[0][1], data.train.y)
        pipeline = joblib.load(self.rf_config.artifact_dir / "pipeline.joblib")
        self.assertEqual(list(pipeline.named_steps), ["classifier"])
        self.assertEqual(report["training"]["trees_fitted"], 7)
        self.assertFalse(report["training"]["convergence"]["applicable"])
        self.assertEqual(list(pipeline.feature_names_in_), FEATURE_NAMES)

    def test_train_and_compare_work_without_test_files(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.dataset_dir / name).unlink()
        self.prepare_models()
        report = self.compare()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["test_evaluated"])
        self.assertFalse(report["final_model_selected"])
        self.assertEqual(sum(report["prediction_transitions"].values()), report["validation_rows"])

    def test_same_seed_reproduces_probabilities(self) -> None:
        rf_training.train_model(self.rf_config)
        values = self.rf_config.model_dump()
        values.update(artifact_dir=self.directory / "rf_recheck", report_prefix=self.directory / "rf_recheck_report")
        second = rf_training.TrainingConfig.model_validate(values)
        rf_training.train_model(second)
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        first_model = joblib.load(self.rf_config.artifact_dir / "pipeline.joblib")
        second_model = joblib.load(second.artifact_dir / "pipeline.joblib")
        np.testing.assert_array_equal(first_model.predict_proba(data.validation.X), second_model.predict_proba(data.validation.X))

    def test_comparison_does_not_fit_or_modify_existing_lr(self) -> None:
        self.prepare_models()
        paths = [path for path in self.lr_config.artifact_dir.iterdir()]
        paths += [self.lr_config.report_prefix.with_suffix(".json"), self.lr_config.report_prefix.with_suffix(".md")]
        before = {path: file_hash(path) for path in paths}
        with patch.object(Pipeline, "fit", side_effect=AssertionError("비교 중 학습 금지")):
            report = self.compare()
        self.assertEqual(before, {path: file_hash(path) for path in paths})
        self.assertEqual(report["threshold"], 0.5)
        self.assertEqual(report["models"]["rf"]["preprocessing"], "identity_no_scaler")

    def test_tampered_pipeline_is_rejected_before_deserialization(self) -> None:
        self.prepare_models()
        with (self.rf_config.artifact_dir / "pipeline.joblib").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.compare()
        self.assertFalse(self.comparison_prefix.with_suffix(".json").exists())

    def test_different_training_input_fingerprint_is_rejected(self) -> None:
        self.prepare_models()
        path = self.rf_config.artifact_dir / "training_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["input_sha256"]["X_train.csv"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "학습 입력"):
            self.compare()

    def test_different_threshold_is_rejected_even_with_updated_artifact_hash(self) -> None:
        self.prepare_models()
        path = self.rf_config.artifact_dir / "decision_policy.json"
        decision = json.loads(path.read_text(encoding="utf-8"))
        decision["threshold"] = 0.7
        path.write_text(json.dumps(decision), encoding="utf-8")
        self.refresh_artifact(path.name)
        with self.assertRaisesRegex(ValueError, "threshold"):
            self.compare()

    def test_reordered_saved_prediction_rows_are_rejected(self) -> None:
        self.prepare_models()
        path = self.rf_config.artifact_dir / "validation_predictions.csv"
        pd.read_csv(path).iloc[::-1].to_csv(path, index=False)
        self.refresh_artifact(path.name)
        with self.assertRaisesRegex(ValueError, "메시지별"):
            self.compare()

    def test_modified_training_metrics_are_rejected_by_reprediction(self) -> None:
        self.prepare_models()
        report_path = self.rf_config.report_prefix.with_suffix(".json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["validation_metrics"]["accuracy"] = -1
        report_path.write_text(json.dumps(report), encoding="utf-8")
        path = self.rf_config.artifact_dir / "training_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["reports"][str(report_path)] = file_hash(report_path)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "재예측"):
            self.compare()

    def test_existing_training_and_comparison_outputs_are_protected(self) -> None:
        self.prepare_models()
        self.compare()
        with self.assertRaises(FileExistsError):
            rf_training.train_model(self.rf_config)
        with self.assertRaises(FileExistsError):
            self.compare()

    def test_feature_importance_and_schema_remain_separate_from_labels(self) -> None:
        report = rf_training.train_model(self.rf_config)
        importance = {item["feature"]: item["importance"] for item in report["feature_importances"]}
        self.assertEqual(set(importance), set(FEATURE_NAMES))
        self.assertEqual(importance["error_count_total"], 0)
        self.assertAlmostEqual(sum(importance.values()), 1)
        schema = json.loads((self.rf_config.artifact_dir / "feature_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["raw_input_features"], FEATURE_NAMES)
        self.assertEqual(schema["preprocessing"]["kind"], "identity")
        self.assertNotIn("label", schema["raw_input_features"])

    def test_transition_categories_cover_each_message_once(self) -> None:
        y = np.array([0, 1, 0, 1, 0, 1])
        lr = np.array([1, 0, 0, 1, 0, 0])
        rf = np.array([0, 1, 1, 0, 0, 0])
        result = prediction_transitions(y, lr, rf)
        self.assertEqual(set(result.values()), {1})
        self.assertEqual(sum(result.values()), len(y))
        with self.assertRaises(ValueError):
            prediction_transitions(y, lr[:-1], rf)
        with self.assertRaises(ValueError):
            prediction_transitions(y, lr, np.array([2, 1, 1, 0, 0, 0]))

    def test_default_config_is_one_fixed_candidate(self) -> None:
        config = rf_training.load_config(AI_ROOT / "configs" / "operational_random_forest.json")
        self.assertEqual(config.n_estimators, 100)
        self.assertEqual(config.n_jobs, 1)
        self.assertEqual(config.threshold, 0.5)
        self.assertEqual(config.random_state, 42)
        with self.assertRaises(ValueError):
            rf_training.TrainingConfig.model_validate({**config.model_dump(), "threshold": 0.7})


if __name__ == "__main__":
    unittest.main()
