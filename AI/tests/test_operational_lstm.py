from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
for name in (".deps", ".deps_lstm"):
    if (AI_ROOT / name).exists():
        sys.path.insert(0, str(AI_ROOT / name))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import torch
from pydantic import ValidationError
from sklearn.preprocessing import StandardScaler

import test_operational_augmentation as fixtures
import run_lstm_reproducibility as reproducibility
import training.run_operational_lstm_experiment as experiment
from data_collection.quality import read_json
from data_preparation.operational_dataset import build_dataset, file_hash
from data_preparation.operational_sequences import SequenceConfig, build_dataset as build_sequences
from evaluation.compare_operational_lstm import coverage_metrics
from training.operational_data import load_operational_data
from training.operational_lstm import MessageLSTM, StopState, cpu_runtime, evaluate, load_checkpoint, save_checkpoint, transform_windows
from training.run_operational_validation_experiment import ExperimentConfig as ReferenceConfig, run_experiment as run_reference
from training.train_operational_logistic_regression import write_json


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = cpu_runtime(42, 1)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.model = MessageLSTM(4)
        self.X = torch.randn(3, 4, 7)
        self.lengths = torch.tensor([1, 4, 2], dtype=torch.int64)

    def test_padding_never_changes_prediction(self) -> None:
        before = self.model(self.X, self.lengths)
        changed = self.X.clone()
        for index, length in enumerate(self.lengths):
            changed[index, length:] = 100000
        torch.testing.assert_close(self.model(changed, self.lengths), before, rtol=0, atol=0)

    def test_unsorted_batch_matches_individual_last_valid_outputs(self) -> None:
        batch = self.model(self.X, self.lengths)
        individually = torch.cat([self.model(self.X[index:index + 1, :length], torch.tensor([length]))
                                   for index, length in enumerate(self.lengths.tolist())])
        torch.testing.assert_close(batch, individually, rtol=1e-6, atol=1e-7)

    def test_hidden_state_is_not_carried_between_calls(self) -> None:
        expected = self.model(self.X, self.lengths)
        self.model(self.X * 10, self.lengths)
        torch.testing.assert_close(self.model(self.X, self.lengths), expected, rtol=0, atol=0)

    def test_invalid_lengths_dtype_and_shape_are_rejected(self) -> None:
        for lengths in (torch.tensor([0, 4, 2]), torch.tensor([1, 5, 2]), self.lengths.float(), self.lengths[:2]):
            with self.subTest(lengths=lengths), self.assertRaises(ValueError):
                self.model(self.X, lengths)
        with self.assertRaises(ValueError):
            self.model(self.X.double(), self.lengths)

    def test_validation_evaluation_does_not_change_parameters_or_gradients(self) -> None:
        state = {name: value.clone() for name, value in self.model.state_dict().items()}
        loss, probability = evaluate(self.model, self.X, self.lengths, torch.tensor([0.0, 1.0, 1.0]), 2)
        self.assertTrue(np.isfinite(loss))
        self.assertEqual(probability.shape, (3,))
        for name, value in self.model.state_dict().items():
            torch.testing.assert_close(state[name], value, rtol=0, atol=0)
        self.assertTrue(all(parameter.grad is None for parameter in self.model.parameters()))

    def test_checkpoint_reload_and_wrong_contract(self) -> None:
        candidate = {"id": "fixture", "hidden_size": 4, "num_layers": 1, "dropout": 0.0, "bidirectional": False}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.pt"
            save_checkpoint(path, self.model, candidate, 2, 0.5, 4)
            restored, checkpoint = load_checkpoint(path, candidate, 4)
            torch.testing.assert_close(restored(self.X, self.lengths), self.model(self.X, self.lengths), rtol=0, atol=0)
            self.assertEqual(checkpoint["best_epoch"], 2)
            with self.assertRaises(ValueError):
                load_checkpoint(path, candidate, 8)
            with self.assertRaises(FileExistsError):
                save_checkpoint(path, self.model, candidate, 2, 0.5, 4)
            payload = torch.load(path, weights_only=True)
            payload["state_dict"]["head.bias"] += 1
            torch.save(payload, path)
            with self.assertRaises(ValueError):
                load_checkpoint(path, candidate, 4)

    def test_early_stopping_strict_minimum_and_patience(self) -> None:
        state = StopState(2)
        self.assertEqual(state.update(1, 0.6), (True, False))
        self.assertEqual(state.update(2, 0.5), (True, False))
        self.assertEqual(state.update(3, 0.5), (False, False))
        self.assertEqual(state.update(4, 0.7), (False, True))
        self.assertEqual(state.best_epoch, 2)
        with self.assertRaises(ValueError):
            state.update(5, float("nan"))

    def test_coverage_keeps_single_message_and_missing_class(self) -> None:
        coverage = coverage_metrics(np.array([0, 1, 1]), np.array([1, 0, 1]), np.array([1, 1, 4]), 4)
        self.assertEqual(coverage["length_one"]["rows"], 2)
        self.assertEqual(coverage["length_one"]["fp"], 1)
        self.assertEqual(coverage["length_one"]["fn"], 1)
        self.assertIsNone(coverage["full_window"]["false_positive_rate"])


class ExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = fixtures.CampaignHoldoutTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.directory
        base = fixture.config()
        build_dataset(base)
        self.data = load_operational_data(base.output_dir, base.dataset_id)
        sequence_dir = self.root / "sequences"
        build_sequences(SequenceConfig(dataset_id="fixture_sequence", source_dataset_id=base.dataset_id,
                                       source_dataset_dir=base.output_dir, output_dir=sequence_dir, window_size=3,
                                       expected_train_rows=6, expected_validation_rows=6), self.root / "sequence_report")
        reference_config = ReferenceConfig(config_version="v1", experiment_id="fixture_rf", dataset_id=base.dataset_id,
            dataset_dir=base.output_dir, artifact_dir=self.root / "reference", report_prefix=self.root / "reference_report",
            thresholds=[0.3, 0.5, 0.7], selection_rule={"max_false_positive_rate": 1.0, "min_recall": 0.0},
            common_parameters={"rf_n_estimators": 5}, candidates=[{"id": "rf_leaf_1", "kind": "random_forest", "min_samples_leaf": 1}])
        reference_report = run_reference(reference_config)
        selection = reference_report["selection"]
        selected = selection["selected_trial"]
        self.assertIsNotNone(selected)
        comparison_path = self.root / "reference_comparison.json"
        write_json(comparison_path, {"selection": selection, "comparison": {"models": {"best_rf_validation": {
            "candidate_id": selected["candidate_id"], "threshold": selected["threshold"],
            "model_sha256": selection["model_sha256"], "validation_metrics": selected["metrics"]}}}})
        plan = read_json(AI_ROOT / "configs" / "operational_lstm_plan.json")
        plan.update(sequence_dataset_id="fixture_sequence", sequence_dataset_dir=str(sequence_dir), base_dataset_id=base.dataset_id,
                    base_dataset_dir=str(base.output_dir), reference_selection=str(reference_config.artifact_dir / "selection.json"),
                    reference_comparison=str(comparison_path), window_size=3, thresholds=[0.3, 0.5, 0.7],
                    selection_rule=reference_config.selection_rule.model_dump())
        plan["training"].update(max_epochs=2, batch_size=4, early_stopping_patience=1)
        plan["candidates"][0].update(id="lstm_h2", hidden_size=2)
        plan["candidates"][1].update(id="lstm_h4", hidden_size=4)
        plan_path = self.root / "plan.json"
        write_json(plan_path, plan)
        self.config = experiment.ExperimentConfig(config_version="v1", experiment_id="fixture_lstm", plan_path=plan_path,
                                                  artifact_dir=self.root / "lstm", report_prefix=self.root / "lstm_report",
                                                  expected_train_rows=6, expected_validation_rows=6)

    def test_actual_default_plan_and_config_are_valid(self) -> None:
        config = experiment.load_config(AI_ROOT / "configs" / "operational_lstm_experiment.json")
        plan = experiment.load_plan(config.plan_path)
        self.assertEqual([candidate.hidden_size for candidate in plan.candidates], [16, 32])
        self.assertEqual(plan.training.max_epochs, 30)
        self.assertEqual(len(plan.thresholds), 7)

    def test_scaler_fit_is_unique_original_train_after_protocol_lock(self) -> None:
        original = StandardScaler.fit
        fits = []

        def fit(scaler, X, *args, **kwargs):
            lock = read_json(self.config.artifact_dir / "protocol_lock.json")
            self.assertEqual(lock["status"], "locked_before_scaler_and_model_fit")
            np.testing.assert_array_equal(X.to_numpy(), self.data.train.X.to_numpy())
            fits.append(len(X))
            return original(scaler, X, *args, **kwargs)

        with patch.object(StandardScaler, "fit", fit):
            report = experiment.run_experiment(self.config)
        self.assertEqual(fits, [6])
        self.assertEqual(report["scaler"]["samples_seen"], 6)
        self.assertEqual(len(report["trials"]), 6)
        self.assertTrue(report["model_training_performed"])
        self.assertTrue(all(candidate["training"]["reload_prediction_equal"] for candidate in report["candidates"]))
        self.assertEqual(report["comparison"]["models"]["reference_rf"]["validation_metrics"]["rows"], 6)

    def test_test_raw_summary_and_network_are_never_accessed(self) -> None:
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "collection_summary.json"} or path.suffix == ".jsonl":
                raise AssertionError("Test·원본 JSONL·종료 요약 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded), patch("socket.create_connection", side_effect=AssertionError("트래픽 금지")):
            report = experiment.run_experiment(self.config)
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["test_evaluated"])
        self.assertFalse(report["refit_with_validation"])

    def test_transform_leaves_padding_zero_and_uses_train_scaler(self) -> None:
        plan = experiment.load_plan(self.config.plan_path)
        windows, _, _ = experiment.load_sequence_data(plan, self.data)
        scaler = StandardScaler().fit(self.data.train.X)
        values = transform_windows(scaler, windows["validation"])
        self.assertEqual(values.dtype, np.dtype("float32"))
        self.assertTrue((values[~windows["validation"].mask] == 0).all())
        anchors = np.arange(len(self.data.validation.y))
        np.testing.assert_array_equal(values[anchors, windows["validation"].lengths - 1],
                                      scaler.transform(self.data.validation.X).astype("float32"))

    def test_corrupted_sequence_and_reference_fail_before_fit(self) -> None:
        plan = experiment.load_plan(self.config.plan_path)
        with (plan.sequence_dataset_dir / "metadata_train.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with patch.object(StandardScaler, "fit", side_effect=AssertionError("fit 금지")), self.assertRaises(ValueError):
            experiment.run_experiment(self.config)
        self.assertFalse(self.config.artifact_dir.exists())

    def test_reference_threshold_metric_mismatch_fails_before_fit(self) -> None:
        plan = experiment.load_plan(self.config.plan_path)
        comparison = read_json(plan.reference_comparison)
        comparison["comparison"]["models"]["best_rf_validation"]["threshold"] = 0.99
        plan.reference_comparison.unlink()
        write_json(plan.reference_comparison, comparison)
        with patch.object(StandardScaler, "fit", side_effect=AssertionError("fit 금지")), self.assertRaises(ValueError):
            experiment.run_experiment(self.config)

    def test_wrong_sequence_semantics_are_rejected_even_with_updated_hash(self) -> None:
        plan = experiment.load_plan(self.config.plan_path)
        path = plan.sequence_dataset_dir / "sequence_schema.json"
        schema = read_json(path)
        schema["padding"]["mask_valid"] = False
        path.unlink()
        write_json(path, schema)
        manifest_path = plan.sequence_dataset_dir / "sequence_manifest.json"
        manifest = read_json(manifest_path)
        manifest["output_files"]["sequence_schema.json"]["sha256"] = file_hash(path)
        manifest_path.unlink()
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "입력 계약"):
            experiment.run_experiment(self.config)

    def test_no_feasible_candidate_completes_without_selected_model(self) -> None:
        with patch.object(experiment, "choose_trial", return_value=None), \
                patch.object(experiment, "eligibility_reasons", return_value=["테스트 미충족"]):
            report = experiment.run_experiment(self.config)
        self.assertEqual(report["selection"]["status"], "no_feasible_candidate")
        self.assertEqual(report["selection"]["eligible_trial_count"], 0)
        self.assertIsNone(report["selection"]["model_path"])
        self.assertFalse((self.config.artifact_dir / "selected_validation_predictions.csv").exists())
        self.assertTrue((self.config.artifact_dir / "experiment_manifest.json").exists())

    def test_existing_outputs_and_source_paths_are_preserved(self) -> None:
        plan = experiment.load_plan(self.config.plan_path)
        invalid = self.config.model_copy(update={"artifact_dir": plan.base_dataset_dir / "invalid"})
        with self.assertRaises(ValueError):
            experiment.run_experiment(invalid)
        report = experiment.run_experiment(self.config)
        before = file_hash(self.config.artifact_dir / "experiment_manifest.json")
        with self.assertRaises(FileExistsError):
            experiment.run_experiment(self.config)
        self.assertEqual(file_hash(self.config.artifact_dir / "experiment_manifest.json"), before)
        self.assertEqual(report["status"], "complete")

    def test_source_change_blocks_complete_manifest(self) -> None:
        original = experiment.fit_candidate
        plan = experiment.load_plan(self.config.plan_path)

        def fit(*args, **kwargs):
            result = original(*args, **kwargs)
            with (plan.base_dataset_dir / "X_train.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return result

        with patch.object(experiment, "fit_candidate", fit), self.assertRaises(ValueError):
            experiment.run_experiment(self.config)
        self.assertFalse((self.config.artifact_dir / "experiment_manifest.json").exists())

    def test_repeated_seed_metrics_weights_and_predictions_are_identical(self) -> None:
        first = experiment.run_experiment(self.config)
        second_config = self.config.model_copy(update={"artifact_dir": self.root / "lstm_replay", "report_prefix": self.root / "lstm_replay_report"})
        second = experiment.run_experiment(second_config)
        self.assertEqual(first["trials"], second["trials"])
        self.assertEqual(first["selection"]["selected_trial"], second["selection"]["selected_trial"])
        for left, right in zip(first["candidates"], second["candidates"]):
            self.assertEqual(left["training"]["weight_sha256"], right["training"]["weight_sha256"])
            self.assertEqual(left["training"]["history"], right["training"]["history"])
        self.assertEqual(file_hash(self.config.report_prefix.with_suffix(".predictions.csv")),
                         file_hash(second_config.report_prefix.with_suffix(".predictions.csv")))

    def test_plan_rejects_unsupported_or_unbounded_settings(self) -> None:
        values = experiment.load_plan(self.config.plan_path).model_dump()
        for key, change in (("device", "cuda"), ("target_policy", "session_final_label"), ("window_size", 0)):
            altered = copy.deepcopy(values)
            altered[key] = change
            with self.subTest(key=key), self.assertRaises(ValidationError):
                experiment.LstmPlan.model_validate(altered)
        values["training"]["max_epochs"] = 10000
        with self.assertRaises(ValidationError):
            experiment.LstmPlan.model_validate(values)

    def test_reproducibility_command_writes_evidence_and_preserves_source(self) -> None:
        experiment.run_experiment(self.config)
        config_path = self.root / "experiment.json"
        write_json(config_path, self.config.model_dump(mode="json"))
        output = self.root / "reproducibility.json"
        result = reproducibility.verify(config_path, output)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["temporary_replay_artifacts_removed"])
        self.assertTrue(result["comparison_predictions_equal"])
        self.assertEqual(read_json(output), result)
        with self.assertRaises(FileExistsError):
            reproducibility.verify(config_path, output)

    def test_reproducibility_rejects_changed_source_report_before_retraining(self) -> None:
        experiment.run_experiment(self.config)
        config_path = self.root / "experiment.json"
        write_json(config_path, self.config.model_dump(mode="json"))
        with self.config.report_prefix.with_suffix(".json").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with patch.object(reproducibility, "run_experiment", side_effect=AssertionError("재학습 금지")), self.assertRaises(ValueError):
            reproducibility.verify(config_path, self.root / "reproducibility.json")


if __name__ == "__main__":
    unittest.main()
