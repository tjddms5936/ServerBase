from __future__ import annotations

import contextlib
import hashlib
import io
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
from data_preparation.operational_dataset import DatasetConfig, build_dataset, file_hash, fingerprint
from training.operational_data import load_operational_data
from training.train_operational_logistic_regression import calculate_metrics
import training.run_operational_validation_experiment as experiment


class OperationalValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational_validation_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        campaign = dataset_fixtures.make_campaign(self.directory)
        self.dataset_dir = self.directory / "dataset"
        build_dataset(DatasetConfig(dataset_id="fixture_v1", campaign_dirs=[campaign], output_dir=self.dataset_dir))
        # 단위 테스트에서는 작은 후보 집합을 사용한다. 실제 실험은 설정 파일의 6개 후보로 실행한다.
        self.config = experiment.ExperimentConfig(
            experiment_id="fixture_selection_v1", dataset_id="fixture_v1", dataset_dir=self.dataset_dir,
            artifact_dir=self.directory / "selection", report_prefix=self.directory / "report",
            thresholds=[0.3, 0.5, 0.7], selection_rule={"max_false_positive_rate": 1.0, "min_recall": 0.5},
            common_parameters={"rf_n_estimators": 7}, candidates=[
                {"id": "lr_c_1", "kind": "logistic_regression", "C": 1.0},
                {"id": "rf_leaf_1", "kind": "random_forest", "min_samples_leaf": 1},
            ],
        )

    def run_experiment(self, config=None) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            return experiment.run_experiment(config or self.config)

    def config_with(self, **overrides) -> experiment.ExperimentConfig:
        values = self.config.model_dump()
        values.update(overrides)
        return experiment.ExperimentConfig.model_validate(values)

    @staticmethod
    def trial(candidate_id="a", threshold=0.5, recall=0.8, f1=0.7, fpr=0.05, acceptable=True) -> dict:
        return {"candidate_id": candidate_id, "threshold": threshold, "training_acceptable": acceptable,
                "eligible": True, "metrics": {"recall": recall, "f1": f1, "false_positive_rate": fpr}}

    def test_default_protocol_has_six_models_and_42_trials(self) -> None:
        config = experiment.load_config(AI_ROOT / "configs" / "operational_validation_experiment.json")
        self.assertEqual(len(config.candidates), 6)
        self.assertEqual(len(config.thresholds), 7)
        self.assertEqual(config.selection_rule.max_false_positive_rate, 0.1)
        self.assertEqual(config.selection_rule.min_recall, 0.5)
        self.assertEqual(config.common_parameters.rf_n_estimators, 100)

    def test_invalid_candidate_ids_thresholds_and_rules_are_rejected(self) -> None:
        changes = [
            {"thresholds": [0.3, 0.3, 0.5]}, {"thresholds": [0.7, 0.5]}, {"thresholds": [0.3]},
            {"thresholds": [0.0, 0.5]}, {"thresholds": [0.5, 1.0]}, {"thresholds": [float("nan"), 0.5]},
            {"thresholds": [True, 0.5]}, {"candidates": []},
            {"selection_rule": {"max_false_positive_rate": 1.1}},
            {"selection_rule": {"min_recall": -0.1}},
            {"selection_rule": {"tie_breakers": ["threshold_asc"]}},
            {"candidates": [{"id": "../escape", "kind": "logistic_regression", "C": 1.0}]},
            {"candidates": [{"id": "a", "kind": "logistic_regression", "C": -1.0}]},
            {"candidates": [{"id": "a", "kind": "random_forest", "min_samples_leaf": 0}]},
            {"candidates": [self.config.candidates[0].model_dump()] * 2},
            {"candidates": [{"id": "a", "kind": "logistic_regression", "C": 1.0},
                            {"id": "b", "kind": "logistic_regression", "C": 1.0}]},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.config_with(**change)

    def test_selection_recomputes_eligibility_and_honors_inclusive_bounds(self) -> None:
        rule = experiment.SelectionRule(max_false_positive_rate=0.1, min_recall=0.5)
        boundary = self.trial(recall=0.5, fpr=0.1)
        boundary["eligible"] = False
        unacceptable = [self.trial(recall=1.0, fpr=0.101), self.trial(recall=0.49), self.trial(acceptable=False)]
        self.assertIs(experiment.choose_trial(unacceptable + [boundary], rule), boundary)
        self.assertIsNone(experiment.choose_trial(unacceptable, rule))

    def test_all_normal_and_nonfinite_trials_are_not_selected(self) -> None:
        rule = experiment.SelectionRule()
        trials = [self.trial(recall=0.0, f1=0.0, fpr=0.0), self.trial(fpr=float("nan")),
                  self.trial(recall=float("inf")), self.trial(f1=-1.0)]
        self.assertIsNone(experiment.choose_trial(trials, rule))

    def test_all_tie_breakers_are_deterministic(self) -> None:
        rule = experiment.SelectionRule()
        cases = [
            (self.trial(recall=0.9, f1=0.6), self.trial(recall=0.8, f1=0.9)),
            (self.trial(f1=0.8), self.trial(f1=0.7)),
            (self.trial(fpr=0.03), self.trial(fpr=0.05)),
            (self.trial(candidate_id="a"), self.trial(candidate_id="b")),
            (self.trial(threshold=0.3), self.trial(threshold=0.7)),
        ]
        for preferred, other in cases:
            self.assertIs(experiment.choose_trial([other, preferred], rule), preferred)
            self.assertIs(experiment.choose_trial([preferred, other], rule), preferred)

    def test_protocol_is_locked_before_first_fit(self) -> None:
        original = experiment.fit_candidate
        observed = []

        def tracked_fit(candidate, config, data):
            lock = json.loads((config.artifact_dir / "protocol_lock.json").read_text(encoding="utf-8"))
            encoded = json.dumps(lock["protocol"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.assertEqual(lock["status"], "locked_before_fit")
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), lock["protocol_sha256"])
            self.assertEqual(lock["input_sha256"], data.input_sha256)
            observed.append(candidate.id)
            return original(candidate, config, data)

        with patch.object(experiment, "fit_candidate", tracked_fit):
            report = self.run_experiment()
        self.assertEqual(len(observed), 2)
        self.assertEqual(report["protocol"]["thresholds"], [0.3, 0.5, 0.7])

    def test_model_fit_count_is_candidate_count_not_threshold_count(self) -> None:
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        original = Pipeline.fit
        calls = []

        def tracked_fit(pipeline, X, y, **kwargs):
            pd.testing.assert_frame_equal(X, data.train.X)
            np.testing.assert_array_equal(y, data.train.y)
            calls.append(len(y))
            return original(pipeline, X, y, **kwargs)

        with patch.object(Pipeline, "fit", tracked_fit):
            report = self.run_experiment()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(report["trials"]), 6)
        self.assertFalse(report["refit_with_validation"])

    def test_experiment_never_opens_test_csv(self) -> None:
        original = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv"}:
                raise AssertionError("선정 중 Test 파일 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            report = self.run_experiment()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["test_evaluated"])
        self.assertFalse(report["final_model_selected"])

    def test_missing_test_csv_does_not_prevent_selection(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.dataset_dir / name).unlink()
        report = self.run_experiment()
        self.assertEqual(report["selection"]["status"], "selected")

    def test_selected_pointer_and_prediction_csv_use_selected_threshold(self) -> None:
        report = self.run_experiment()
        selection = json.loads((self.config.artifact_dir / "selection.json").read_text(encoding="utf-8"))
        selected = selection["selected_trial"]
        self.assertEqual(selection["status"], "selected")
        self.assertEqual(selection, report["selection"])
        self.assertIsNotNone(selected)
        self.assertNotEqual(selection["threshold"], 0.5)
        model_path = self.config.artifact_dir / selection["model_path"]
        self.assertEqual(file_hash(model_path), selection["model_sha256"])
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        pipeline = joblib.load(model_path)
        probability = pipeline.predict_proba(data.validation.X)[:, 1]
        self.assertEqual(calculate_metrics(data.validation.y, probability, selection["threshold"]), selected["metrics"])
        saved = pd.read_csv(self.config.artifact_dir / "selected_validation_predictions.csv")
        np.testing.assert_array_equal(saved["predicted_label"], (probability >= selection["threshold"]).astype("int64"))
        default_policy = json.loads((model_path.parent / "decision_policy.json").read_text(encoding="utf-8"))
        self.assertEqual(default_policy["threshold"], 0.5)
        self.assertFalse(selection["deployment_ready"])

    def test_no_feasible_candidate_is_complete_but_has_no_selected_model(self) -> None:
        # 원본 단위 테스트에 동일 입력·다른 정답이 있어 FPR=0과 Recall=1을 동시에 만족할 수 없다.
        config = self.config_with(selection_rule={"max_false_positive_rate": 0.0, "min_recall": 1.0})
        report = self.run_experiment(config)
        selection = report["selection"]
        self.assertEqual(selection["status"], "no_feasible_candidate")
        self.assertEqual(selection["eligible_trial_count"], 0)
        for name in ("selected_trial", "model_path", "model_sha256", "feature_schema_path", "threshold"):
            self.assertIsNone(selection[name])
        self.assertFalse((config.artifact_dir / "selected_validation_predictions.csv").exists())
        manifest = json.loads((config.artifact_dir / "experiment_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["selection_status"], "no_feasible_candidate")

    def test_nonconverged_lr_is_excluded_even_if_metrics_satisfy_rule(self) -> None:
        original = Pipeline.fit

        def fit_with_warning(pipeline, *args, **kwargs):
            result = original(pipeline, *args, **kwargs)
            warnings.warn("단위 테스트 수렴 경고", ConvergenceWarning)
            return result

        config = self.config_with(candidates=[self.config.candidates[0].model_dump()])
        with patch.object(Pipeline, "fit", fit_with_warning):
            report = self.run_experiment(config)
        self.assertFalse(report["candidates"][0]["training_acceptable"])
        self.assertEqual(report["selection"]["status"], "no_feasible_candidate")
        self.assertTrue(all(not trial["eligible"] for trial in report["trials"]))

    def test_validation_shift_does_not_change_scaler_training_statistics(self) -> None:
        path = self.dataset_dir / "X_validation.csv"
        values = pd.read_csv(path)
        values["connection_duration_ms"] += 100000
        values.to_csv(path, index=False)
        metadata_path = self.dataset_dir / "metadata_validation.csv"
        metadata = pd.read_csv(metadata_path, dtype=str)
        metadata["feature_hash"] = [fingerprint(row) for row in values.to_dict("records")]
        metadata.to_csv(metadata_path, index=False)
        manifest_path = self.dataset_dir / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for updated in (path, metadata_path):
            manifest["output_files"][updated.name]["sha256"] = file_hash(updated)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.run_experiment()
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        pipeline = joblib.load(self.config.artifact_dir / "candidates" / "lr_c_1" / "pipeline.joblib")
        scaler = pipeline.named_steps["scaler"]
        np.testing.assert_allclose(scaler.mean_, data.train.X.mean().to_numpy())
        self.assertEqual(int(scaler.n_samples_seen_), len(data.train.y))

    def test_inventory_hashes_and_selected_trial_are_reproducible(self) -> None:
        first = self.run_experiment()
        manifest = json.loads((self.config.artifact_dir / "experiment_manifest.json").read_text(encoding="utf-8"))
        for name, evidence in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.artifact_dir / name), evidence["sha256"])
        second_config = self.config_with(artifact_dir=self.directory / "recheck", report_prefix=self.directory / "recheck_report")
        second = self.run_experiment(second_config)
        self.assertEqual(first["selection"]["selected_trial"], second["selection"]["selected_trial"])

    def test_existing_experiment_is_not_overwritten(self) -> None:
        self.run_experiment()
        before = file_hash(self.config.artifact_dir / "experiment_manifest.json")
        with self.assertRaises(FileExistsError):
            self.run_experiment()
        self.assertEqual(before, file_hash(self.config.artifact_dir / "experiment_manifest.json"))

    def test_outputs_cannot_be_written_inside_immutable_dataset(self) -> None:
        config = self.config_with(artifact_dir=self.dataset_dir / "invalid_output")
        with self.assertRaisesRegex(ValueError, "고정 데이터셋 내부"):
            self.run_experiment(config)
        self.assertFalse(config.artifact_dir.exists())

    def test_changed_input_aborts_without_complete_manifest(self) -> None:
        original = experiment.fit_candidate

        def change_input_after_fit(candidate, config, data):
            result = original(candidate, config, data)
            with (self.dataset_dir / "X_train.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return result

        config = self.config_with(candidates=[self.config.candidates[0].model_dump()])
        with patch.object(experiment, "fit_candidate", change_input_after_fit), self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_experiment(config)
        self.assertFalse((config.artifact_dir / "experiment_manifest.json").exists())
        self.assertFalse(config.report_prefix.with_suffix(".json").exists())

    def test_changed_protocol_lock_aborts_without_complete_manifest(self) -> None:
        original = experiment.fit_candidate

        def change_protocol_after_fit(candidate, config, data):
            result = original(candidate, config, data)
            (config.artifact_dir / "protocol_lock.json").write_text("{}", encoding="utf-8")
            return result

        config = self.config_with(candidates=[self.config.candidates[0].model_dump()])
        with patch.object(experiment, "fit_candidate", change_protocol_after_fit), self.assertRaisesRegex(ValueError, "잠금 파일"):
            self.run_experiment(config)
        self.assertFalse((config.artifact_dir / "experiment_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
