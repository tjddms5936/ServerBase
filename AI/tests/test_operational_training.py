from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.pipeline import Pipeline

import test_operational_dataset as dataset_fixtures
from data_preparation.operational_dataset import DatasetConfig, FEATURE_NAMES, build_dataset, file_hash, fingerprint
from training.operational_data import load_operational_data
from training.train_operational_logistic_regression import TrainingConfig, calculate_metrics, load_config, train_model


class OperationalTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational_training_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        campaign = dataset_fixtures.make_campaign(self.directory)
        self.dataset_dir = self.directory / "dataset"
        build_dataset(DatasetConfig(dataset_id="fixture_v1", campaign_dirs=[campaign], output_dir=self.dataset_dir))
        self.config = TrainingConfig(dataset_id="fixture_v1", dataset_dir=self.dataset_dir,
                                     artifact_dir=self.directory / "artifacts", report_prefix=self.directory / "report")

    def refresh_hash(self, name: str, rows: int | None = None) -> None:
        # 변조된 manifest를 신뢰하더라도 내용 검사로 거부하는지 확인하는 테스트다.
        path = self.dataset_dir / "dataset_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["output_files"][name]["sha256"] = file_hash(self.dataset_dir / name)
        if rows is not None:
            manifest["output_files"][name]["rows"] = rows
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_loader_does_not_open_test_csv(self) -> None:
        original = Path.read_bytes
        opened = []

        def guarded_read(path: Path) -> bytes:
            opened.append(path.name)
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv"}:
                raise AssertionError("최종 평가 CSV를 읽으면 안 됩니다.")
            return original(path)

        with patch.object(Path, "read_bytes", guarded_read):
            report = train_model(self.config)
        self.assertFalse(report["test_evaluated"])
        self.assertFalse(report["test_files_read"])
        self.assertNotIn("X_test.csv", opened)
        self.assertNotIn("y_test.csv", opened)
        self.assertNotIn("metadata_test.csv", opened)

    def test_missing_test_csv_does_not_prevent_training(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.dataset_dir / name).unlink()
        report = train_model(self.config)
        self.assertTrue(report["training"]["convergence"]["converged"])

    def test_scaler_is_fitted_only_on_train_even_with_validation_shift(self) -> None:
        path = self.dataset_dir / "X_validation.csv"
        values = pd.read_csv(path)
        values["connection_duration_ms"] += 100000
        values.to_csv(path, index=False)
        metadata_path = self.dataset_dir / "metadata_validation.csv"
        metadata = pd.read_csv(metadata_path, dtype=str)
        metadata["feature_hash"] = [fingerprint(row) for row in values.to_dict("records")]
        metadata.to_csv(metadata_path, index=False)
        self.refresh_hash(path.name)
        self.refresh_hash(metadata_path.name)
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        report = train_model(self.config)
        pipeline = joblib.load(self.config.artifact_dir / "pipeline.joblib")
        scaler = pipeline.named_steps["scaler"]
        np.testing.assert_allclose(scaler.mean_, data.train.X.mean().to_numpy())
        self.assertEqual(int(scaler.n_samples_seen_), len(data.train.X))
        self.assertLess(report["learned_parameters"]["scaler_mean"]["connection_duration_ms"], 100000)

    def test_hash_mismatch_is_rejected_before_fit_or_export(self) -> None:
        with (self.dataset_dir / "X_train.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with patch.object(Pipeline, "fit") as fit:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                train_model(self.config)
        fit.assert_not_called()
        self.assertFalse(self.config.artifact_dir.exists())

    def test_reordered_input_columns_are_rejected(self) -> None:
        path = self.dataset_dir / "X_train.csv"
        values = pd.read_csv(path)
        values[list(reversed(values.columns))].to_csv(path, index=False)
        self.refresh_hash(path.name)
        with self.assertRaisesRegex(ValueError, "CSV"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_reordered_metadata_is_rejected_by_feature_fingerprint(self) -> None:
        path = self.dataset_dir / "metadata_train.csv"
        pd.read_csv(path, dtype=str).iloc[[1, 0, 2]].to_csv(path, index=False)
        self.refresh_hash(path.name)
        with self.assertRaisesRegex(ValueError, "해시 불일치"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_invalid_label_is_rejected_without_integer_truncation(self) -> None:
        path = self.dataset_dir / "y_train.csv"
        values = pd.read_csv(path, dtype=str)
        values.loc[0, "label"] = "0.5"
        values.to_csv(path, index=False)
        self.refresh_hash(path.name)
        with self.assertRaisesRegex(ValueError, "정답"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_mismatched_target_rows_are_rejected(self) -> None:
        path = self.dataset_dir / "y_train.csv"
        pd.read_csv(path).iloc[:-1].to_csv(path, index=False)
        self.refresh_hash(path.name, rows=2)
        with self.assertRaisesRegex(ValueError, "행 수"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_nonfinite_feature_is_rejected(self) -> None:
        path = self.dataset_dir / "X_train.csv"
        values = pd.read_csv(path, dtype=str)
        values.loc[0, "connection_duration_ms"] = "inf"
        values.to_csv(path, index=False)
        self.refresh_hash(path.name)
        with self.assertRaises(ValueError):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_schema_unit_mismatch_is_rejected(self) -> None:
        path = self.dataset_dir / "feature_schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["feature_units"]["connection_duration_ms"] = "second"
        path.write_text(json.dumps(schema), encoding="utf-8")
        self.refresh_hash(path.name)
        with self.assertRaisesRegex(ValueError, "schema"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_run_overlap_is_rejected_even_if_split_metadata_is_rewritten(self) -> None:
        train_metadata = pd.read_csv(self.dataset_dir / "metadata_train.csv", dtype=str)
        validation_path = self.dataset_dir / "metadata_validation.csv"
        metadata = pd.read_csv(validation_path, dtype=str)
        before = metadata.at[0, "run_id"]
        after = train_metadata.at[0, "run_id"]
        metadata.loc[metadata["run_id"] == before, "run_id"] = after
        metadata.to_csv(validation_path, index=False)
        self.refresh_hash(validation_path.name)
        path = self.dataset_dir / "split_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for entry in manifest["runs"]:
            if entry["run_id"] == before:
                entry["run_id"] = after
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.refresh_hash(path.name)
        with self.assertRaisesRegex(ValueError, "누출"):
            load_operational_data(self.dataset_dir, "fixture_v1")

    def test_round_trip_bundle_includes_schema_threshold_and_predictions(self) -> None:
        report = train_model(self.config)
        manifest = json.loads((self.config.artifact_dir / "training_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "complete")
        self.assertFalse(manifest["deployment_ready"])
        self.assertEqual(report["threshold_selection"], "fixed_baseline_not_tuned")
        self.assertEqual(list(report["learned_parameters"]["weights"]), FEATURE_NAMES)
        self.assertEqual(report["learned_parameters"]["weights"]["error_count_total"], 0)
        schema = json.loads((self.config.artifact_dir / "feature_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["preprocessing"]["fit_scope"], "train_only")
        self.assertEqual(schema["raw_input_features"], FEATURE_NAMES)
        predictions = pd.read_csv(self.config.artifact_dir / "validation_predictions.csv")
        self.assertEqual(len(predictions), report["validation_metrics"]["rows"])
        self.assertEqual(set(predictions["split"]), {"validation"})
        for name, evidence in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.artifact_dir / name), evidence["sha256"])

    def test_existing_artifacts_are_not_overwritten(self) -> None:
        train_model(self.config)
        before = file_hash(self.config.artifact_dir / "pipeline.joblib")
        with self.assertRaises(FileExistsError):
            train_model(self.config)
        self.assertEqual(before, file_hash(self.config.artifact_dir / "pipeline.joblib"))

    def test_metrics_use_fixed_threshold_including_equality(self) -> None:
        result = calculate_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.5, 0.2, 0.8]), 0.5)
        self.assertEqual(result["confusion_matrix"]["values"], [[1, 1], [1, 1]])
        self.assertEqual(result["false_positive_rate"], 0.5)

    def test_convergence_warning_is_saved_and_not_claimed_as_success(self) -> None:
        original = Pipeline.fit

        def fit_with_warning(pipeline, *args, **kwargs):
            result = original(pipeline, *args, **kwargs)
            warnings.warn("단위 테스트 수렴 경고", ConvergenceWarning)
            return result

        with patch.object(Pipeline, "fit", fit_with_warning):
            report = train_model(self.config)
        self.assertFalse(report["training"]["convergence"]["converged"])
        self.assertIn("단위 테스트 수렴 경고", report["training"]["convergence"]["warnings"])

    def test_default_config_paths_and_parameters(self) -> None:
        config = load_config(AI_ROOT / "configs" / "operational_logistic_regression.json")
        self.assertEqual(config.dataset_dir, AI_ROOT / "data" / "processed" / "operational_message" / "v1")
        self.assertEqual(config.max_iter, 1000)
        self.assertEqual(config.threshold, 0.5)
        with self.assertRaises(ValueError):
            TrainingConfig.model_validate({**config.model_dump(), "threshold": 0.7})


if __name__ == "__main__":
    unittest.main()
