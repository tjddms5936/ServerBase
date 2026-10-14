from __future__ import annotations

import copy
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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
from sklearn.preprocessing import StandardScaler

import training.run_operational_recovery_experiment as experiment
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_prefix_features import transform_prefix_features, TRANSFORM_COLUMNS
from evaluation.compare_operational_recovery import choose_trial, make_trial, rejection_reasons
from training.operational_data import OperationalData
from test_operational_rf_diagnosis import source_split


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = read_json(AI_ROOT / "configs" / "operational_recovery_plan.json")["approval_criteria"]

    def trial(self, candidate="rf_raw7", threshold=0.3, fpr=0.05, batch=0.05, recall=0.8, f1=0.8):
        return {"candidate_id": candidate, "threshold": threshold,
                "metrics": {"false_positive_rate": fpr, "recall": recall, "f1": f1},
                "by_scenario": {"normal_batched_chat": {"false_positive_rate": batch}}}

    def test_normal_batched_constraint_is_mandatory_even_if_overall_passes(self) -> None:
        trial = self.trial(batch=0.100001)
        self.assertEqual(rejection_reasons(trial, self.criteria), ["정상 묶음 FPR 기준 미달"])
        self.assertIsNone(choose_trial([trial], self.criteria))

    def test_overall_recall_and_boundary_constraints(self) -> None:
        self.assertEqual(rejection_reasons(self.trial(fpr=0.1, batch=0.1, recall=0.5), self.criteria), [])
        for values in ({"fpr": 0.100001}, {"recall": 0.499999}, {"batch": None}, {"fpr": float("nan")}):
            self.assertTrue(rejection_reasons(self.trial(**values), self.criteria))

    def test_ranking_and_deterministic_candidate_threshold_ties(self) -> None:
        trials = [self.trial("b", 0.5), self.trial("a", 0.5), self.trial("a", 0.3)]
        self.assertEqual(choose_trial(trials, self.criteria), trials[2])
        strongest = self.trial("z", 0.7, recall=0.9, f1=0.6)
        self.assertEqual(choose_trial(trials + [strongest], self.criteria), strongest)

    def test_no_feasible_candidate_has_no_fallback(self) -> None:
        self.assertIsNone(choose_trial([self.trial(batch=0.5), self.trial(recall=0.1)], self.criteria))

    def test_first_message_and_all_validation_rows_are_preserved(self) -> None:
        source = source_split()
        trial = make_trial({"candidate_id": "rf_raw7", "input_transform_id": "raw7_v1"},
                           source, np.array([0.9, 0.1, 0.1, 0.9]), 0.3, self.criteria)
        self.assertEqual(trial["metrics"]["rows"], 4)
        self.assertEqual(trial["by_message_position"]["first_message"]["rows"], 2)
        self.assertEqual(trial["by_message_position"]["first_message"]["fp"], 1)


class RecoveryExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="recovery_fit_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        train, validation = source_split(split="train"), source_split()
        for name, source in (("t", train), ("v", validation)):
            source.metadata["campaign_id"] = name
            source.metadata["row_id"] = [f"{name}:{row}" for row in source.metadata.row_id]
        self.data = OperationalData("fixture", {"feature_contract_id": "server_application_message_v2", "raw_input_features": FEATURE_NAMES},
                                    {"normal": 0, "controlled_anomaly": 1}, train, validation, {})
        self.config = experiment.RecoveryConfig(
            experiment_id="fixture_recovery", dataset_id="fixture", dataset_dir=self.directory / "data",
            design_path=AI_ROOT / "configs" / "operational_recovery_plan.json",
            previous_plan=AI_ROOT / "configs" / "operational_augmentation_plan.json",
            collection_report=self.directory / "collection.json", reference_dataset_id="old_fixture",
            reference_dataset_dir=self.directory / "old_data", reference_model_dir=self.directory / "old_model",
            artifact_dir=self.directory / "run", report_prefix=self.directory / "training_report",
            comparison_report_prefix=self.directory / "comparison_report", expected_train_rows=4, expected_validation_rows=4)
        # 실제 사용자 모델 대신 임시 기준 모델을 준비하고 보호할 파일도 임시 폴더에 둔다.
        self.reference = Pipeline([("classifier", RandomForestClassifier(n_estimators=3, min_samples_leaf=1,
                                                                          random_state=7, n_jobs=1))])
        self.reference.fit(train.X, train.y)
        self.protected_file = self.directory / "reference.bin"
        self.protected_file.write_bytes(b"fixed-reference")
        self.evidence = {str(self.protected_file): file_hash(self.protected_file)}
        self.reference_description = {"candidate_id": "rf_leaf_5", "threshold": 0.3, "model_sha256": "fixture",
                                      "previous_train_rows": 4, "deployment_approval": "withheld_after_test"}

    def execute(self, config=None):
        registry = {"campaigns": [{"campaign_dir": str(self.directory / "raw" / "campaign")}]}
        with patch.object(experiment, "load_operational_data", return_value=self.data), \
                patch.object(experiment, "verify_provenance", return_value=(registry, dict(self.evidence))), \
                patch.object(experiment, "load_reference", return_value=(self.reference, self.reference_description, dict(self.evidence))), \
                redirect_stdout(io.StringIO()):
            return experiment.run(config or self.config)

    def config_with(self, **values):
        return experiment.RecoveryConfig.model_validate({**self.config.model_dump(), **values})

    def test_four_fit_calls_use_only_transformed_train_and_lock_exists_first(self) -> None:
        original = Pipeline.fit
        calls = []
        design = read_json(self.config.design_path)

        def guarded_fit(pipeline, X, y, **kwargs):
            lock = read_json(self.config.artifact_dir / "protocol_lock.json")
            self.assertEqual(lock["status"], "locked_before_fit_and_new_validation_predictions")
            candidate = design["development_experiment"]["candidates"][len(calls)]
            expected = transform_prefix_features(self.data.train, candidate["input_transform_id"])
            pd.testing.assert_frame_equal(X, expected)
            np.testing.assert_array_equal(y, self.data.train.y)
            self.assertEqual(lock["protocol"]["transformed_input_sha256"][candidate["id"]]["train"], experiment.matrix_hash(X))
            calls.append(len(X.columns))
            return original(pipeline, X, y, **kwargs)

        with patch.object(Pipeline, "fit", guarded_fit), patch.object(StandardScaler, "fit", side_effect=AssertionError("scaler 학습 금지")):
            report = self.execute()
        self.assertEqual(calls, [7, 5, 5, 6])
        self.assertEqual(report["trial_count"], 20)
        self.assertFalse(report["refit_with_validation"])

    def test_test_raw_summary_and_network_are_not_used(self) -> None:
        real_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "collection_summary.json"} or path.suffix == ".jsonl":
                raise AssertionError("Test·수집 원본·종료 summary 접근 금지")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open), patch("socket.create_connection", side_effect=AssertionError("송신 금지")):
            report = self.execute()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["raw_records_read"])
        self.assertFalse(report["deployment_ready"])

    def test_saved_models_schemas_and_default_policy_are_separate(self) -> None:
        report = self.execute()
        for candidate in report["candidates"]:
            directory = Path(candidate["model_path"]).parent
            schema = read_json(directory / "model_input_schema.json")
            self.assertEqual(schema["raw_input_features"], FEATURE_NAMES)
            self.assertEqual(schema["model_input_features"], TRANSFORM_COLUMNS[candidate["input_transform_id"]])
            restored = joblib.load(directory / "pipeline.joblib")
            X = transform_prefix_features(self.data.validation, candidate["input_transform_id"])
            saved = pd.read_csv(directory / "validation_predictions.csv", float_precision="round_trip")
            np.testing.assert_array_equal(restored.predict_proba(X)[:, 1], saved.anomaly_probability)
            self.assertEqual(read_json(directory / "default_decision_policy.json")["threshold"], 0.5)
            self.assertEqual(candidate["trees_fitted"], 100)
        selection = read_json(self.config.artifact_dir / "selection.json")
        self.assertFalse(selection["cost_verified"])
        self.assertFalse(selection["test_confirmed"])

    def test_comparison_rows_and_transitions_reconcile(self) -> None:
        report = self.execute()
        saved = pd.read_csv(report["comparison_csv"]["path"], float_precision="round_trip")
        self.assertEqual(saved.row_id.tolist(), self.data.validation.metadata.row_id.tolist())
        np.testing.assert_array_equal(saved.label, self.data.validation.y)
        for name, model in report["comparison"]["models"].items():
            np.testing.assert_array_equal(saved[f"{name}__prediction"],
                (saved[f"{name}__probability"] >= saved[f"{name}__threshold"]).astype("int64"))
            if "transitions_from_reference" in model:
                self.assertEqual(sum(model["transitions_from_reference"].values()), 4)

    def test_default_plan_no_feasible_result_is_complete_without_approval(self) -> None:
        report = self.execute()
        self.assertEqual(report["selection"]["status"], "no_feasible_candidate")
        self.assertIsNone(report["selection"]["selected_trial"])
        self.assertIsNone(read_json(self.config.artifact_dir / "selection.json")["model_path"])
        self.assertEqual(read_json(self.config.artifact_dir / "experiment_manifest.json")["status"], "complete")

    def test_manifest_inventory_and_reports_match_saved_hashes(self) -> None:
        self.execute()
        manifest = read_json(self.config.artifact_dir / "experiment_manifest.json")
        for name, description in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.artifact_dir / name), description["sha256"])
        for name, digest in manifest["reports"].items():
            self.assertEqual(file_hash(Path(name)), digest)

    def test_existing_outputs_are_not_overwritten(self) -> None:
        self.execute()
        original = file_hash(self.config.artifact_dir / "experiment_manifest.json")
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(file_hash(self.config.artifact_dir / "experiment_manifest.json"), original)

    def test_incorrect_row_counts_and_output_collisions_abort_before_fit(self) -> None:
        for config in (self.config_with(expected_train_rows=100),
                       self.config_with(comparison_report_prefix=self.config.report_prefix),
                       self.config_with(artifact_dir=self.config.dataset_dir / "run")):
            with patch.object(Pipeline, "fit", side_effect=AssertionError("잘못된 준비로 학습 금지")), self.assertRaises(ValueError):
                self.execute(config)
        self.assertFalse(self.config.artifact_dir.exists())

    def test_source_or_protocol_change_prevents_full_completion(self) -> None:
        original = Pipeline.fit

        def changing_fit(pipeline, *args, **kwargs):
            result = original(pipeline, *args, **kwargs)
            self.protected_file.write_bytes(b"changed")
            return result

        with patch.object(Pipeline, "fit", changing_fit), self.assertRaisesRegex(ValueError, "고정 파일"):
            self.execute()
        self.assertFalse((self.config.artifact_dir / "experiment_manifest.json").exists())
        self.assertEqual(read_json(self.config.artifact_dir / "experiment_failure.json")["status"], "failed")

    def test_matrix_hash_preserves_column_and_row_order(self) -> None:
        X = self.data.train.X
        self.assertNotEqual(experiment.matrix_hash(X), experiment.matrix_hash(X.iloc[::-1]))
        self.assertNotEqual(experiment.matrix_hash(X), experiment.matrix_hash(X[X.columns[::-1]]))


if __name__ == "__main__":
    unittest.main()
