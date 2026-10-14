from __future__ import annotations

import sys
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
for name in (".deps", ".deps_lstm"):
    if (AI_ROOT / name).exists():
        sys.path.insert(0, str(AI_ROOT / name))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import ValidationError
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

import test_operational_lstm as lstm_fixtures
from test_operational_sequences import source_split
import evaluation.run_operational_final_evaluation as evaluation
from data_collection.quality import read_json
from data_preparation.operational_dataset import file_hash
from data_preparation.operational_sequences import make_windows
from evaluation.operational_cost_worker import latency_summary, measure, probability_agreement
from evaluation.operational_final_data import load_test, make_evaluation_windows, reserve_holdout, validate_evaluation_windows
from training.train_operational_logistic_regression import write_json


def fake_summary() -> dict:
    return {"process_runs": 1, "single_request_median_ms": 1.0, "single_request_worst_process_p95_ms": 2.0,
            "model_only_median_ms": 0.5, "batch_median_ms": 3.0, "batch_amortized_per_message_median_ms": 0.5,
            "maximum_native_process_peak_rss_bytes": 128 * evaluation.MIB,
            "required_model_files_bytes": 1024, "model_and_scaler_array_bytes": 100}


class WindowTests(unittest.TestCase):
    def test_matches_frozen_train_validation_window_algorithm(self) -> None:
        source = source_split([("c", "r", "s", index) for index in (3, 1, 2)])
        original = make_windows(source, 3, "fixture")
        final = make_evaluation_windows(source, 3, "fixture")
        validate_evaluation_windows(final, source, 3, "fixture")
        for name in ("X", "y", "lengths", "mask", "source_positions"):
            np.testing.assert_array_equal(getattr(original, name), getattr(final, name))
        pd.testing.assert_frame_equal(original.metadata, final.metadata)

    def test_test_metadata_stays_test_and_uses_past_only(self) -> None:
        source = source_split([("c", "r", "s", index) for index in range(1, 6)], split="test")
        windows = make_evaluation_windows(source, 3, "fixture")
        self.assertEqual(set(windows.metadata["split"]), {"test"})
        np.testing.assert_array_equal(windows.source_positions[-1], [2, 3, 4])
        self.assertEqual(windows.lengths[0], 1)
        self.assertTrue((windows.X[~windows.mask] == 0).all())
        shorter = make_evaluation_windows(source_split([("c", "r", "s", index) for index in range(1, 3)], split="test"), 3, "fixture")
        np.testing.assert_array_equal(shorter.X, windows.X[:2])

    def test_missing_message_counter_regression_and_future_mutation_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_evaluation_windows(source_split([("c", "r", "s", 1), ("c", "r", "s", 3)], split="test"), 3, "fixture")
        source = source_split([("c", "r", "s", 1), ("c", "r", "s", 2)], split="test")
        windows = make_evaluation_windows(source, 3, "fixture")
        windows.source_positions[0, 0] = 1
        with self.assertRaises(ValueError):
            validate_evaluation_windows(windows, source, 3, "fixture")
        source.X.loc[1, "bytes_received_total"] = 1
        with self.assertRaises(ValueError):
            make_evaluation_windows(source, 3, "fixture")


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = evaluation.load_config(AI_ROOT / "configs" / "operational_final_evaluation.json").criteria

    def test_selection_is_validation_recall_before_cost(self) -> None:
        models = {"fast": {"validation_metrics": {"recall": 0.7, "f1": 0.8, "false_positive_rate": 0.04}},
                  "strong": {"validation_metrics": {"recall": 0.9, "f1": 0.8, "false_positive_rate": 0.09}}}
        costs = {name: {"summary": fake_summary()} for name in models}
        costs["fast"]["summary"]["single_request_worst_process_p95_ms"] = 0.1
        chosen, _ = evaluation.select_before_test(models, costs, self.criteria)
        self.assertEqual(chosen, "strong")

    def test_cost_budget_and_unavailable_or_invalid_metrics_reject(self) -> None:
        metrics = {"recall": 0.9, "f1": 0.8, "false_positive_rate": 0.05}
        for key, value in (("single_request_worst_process_p95_ms", 100.0), ("maximum_native_process_peak_rss_bytes", None),
                           ("required_model_files_bytes", 100 * evaluation.MIB), ("single_request_worst_process_p95_ms", float("nan"))):
            cost = fake_summary()
            cost[key] = value
            self.assertTrue(evaluation.rejection_reasons(metrics, cost, self.criteria))

    def test_latency_summary_and_config_bounds(self) -> None:
        self.assertEqual(latency_summary([1.0, 2.0, 3.0])["median_ms"], 2.0)
        with self.assertRaises(ValueError):
            latency_summary([float("nan")])
        config = evaluation.load_config(AI_ROOT / "configs" / "operational_final_evaluation.json").model_dump()
        config["benchmark"]["cpu_threads"] = 8
        with self.assertRaises(ValidationError):
            evaluation.EvaluationConfig.model_validate(config)

    def test_duplicate_model_kind_is_not_an_extra_test_candidate(self) -> None:
        values = evaluation.load_config(AI_ROOT / "configs" / "operational_final_evaluation.json").model_dump()
        values["models"][1]["kind"] = "logistic_regression"
        with self.assertRaises(ValidationError):
            evaluation.EvaluationConfig.model_validate(values)

    def test_float32_tolerance_never_allows_threshold_decision_flip(self) -> None:
        self.assertTrue(probability_agreement(np.array([0.35691985]), np.array([0.35691878]), 0.5, 0.00001))
        self.assertFalse(probability_agreement(np.array([0.5000001]), np.array([0.4999999]), 0.5, 0.00001))


class FinalEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = lstm_fixtures.ExperimentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.data = fixture.root, fixture.data
        self.lstm = lstm_fixtures.experiment.run_experiment(fixture.config)
        plan = lstm_fixtures.experiment.load_plan(fixture.config.plan_path)
        comparison = read_json(plan.reference_comparison)
        comparison["comparison"]["models"]["best_rf_validation"]["kind"] = "random_forest"
        comparison_path = self.root / "final_augmentation_comparison.json"
        write_json(comparison_path, comparison)
        selected = comparison["selection"]["selected_trial"]
        values = evaluation.load_config(AI_ROOT / "configs" / "operational_final_evaluation.json").model_dump()
        values.update(experiment_id="fixture_final", dataset_id=self.data.dataset_id, dataset_dir=plan.base_dataset_dir,
                      sequence_dir=plan.sequence_dataset_dir, augmentation_report=comparison_path,
                      lstm_report=fixture.config.report_prefix.with_suffix(".json"), augmentation_root=plan.reference_selection.parent,
                      lstm_root=fixture.config.artifact_dir, artifact_dir=self.root / "final", holdout_registry_dir=self.root / "holdout_registry",
                      report_prefix=self.root / "final_report", expected_train_rows=6, expected_validation_rows=6, expected_test_rows=6,
                      window_size=3, models=[{"id": "selected_rf", "kind": "random_forest", "candidate_id": selected["candidate_id"], "threshold": selected["threshold"]}])
        values["benchmark"].update(process_repeats=1, warmup_repeats=1, single_request_repeats=3, batch_repeats=1)
        # 6행짜리 합성 fixture는 실제 실험의 성능 기준과 분리해 접근 순서·승인 경로만 검사한다.
        values["criteria"]["max_false_positive_rate"] = 1.0
        self.config = evaluation.EvaluationConfig.model_validate(values)

    def fake_benchmark(self, config, descriptors, data, windows, directory):
        directory.mkdir()
        evidence_path = directory / "fake_cost.json"
        write_json(evidence_path, {"fixture_only": True})
        return {name: {"summary": fake_summary(), "process_records": []} for name in descriptors}, {str(evidence_path): file_hash(evidence_path)}

    def evaluate(self, config=None):
        with patch.object(evaluation, "benchmark_models", self.fake_benchmark):
            return evaluation.run_evaluation(config or self.config)

    def test_test_access_only_after_candidate_lock_and_receipt_and_no_fit(self) -> None:
        original = Path.open
        observed = []

        def guarded(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv"}:
                self.assertTrue((self.config.artifact_dir / "candidate_lock.json").is_file())
                self.assertTrue(list(self.config.holdout_registry_dir.glob("*.json")))
                observed.append(path.name)
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded), patch.object(Pipeline, "fit", side_effect=AssertionError("모델 fit 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("scaler fit 금지")):
            report = self.evaluate()
        self.assertTrue(set(observed) >= {"X_test.csv", "y_test.csv", "metadata_test.csv"})
        self.assertTrue(report["test_evaluated"])
        self.assertFalse(report["selection_used_test"])
        self.assertEqual(report["selected_model_id"], "selected_rf")
        self.assertEqual(report["test_windows"]["windows"], 6)
        self.assertEqual(report["test_windows"]["length_one_windows"], 4)
        self.assertEqual(report["approval"]["status"], "approved_for_local_advisory_integration")
        self.assertFalse(report["approval"]["production_ready"])
        bundle = self.config.artifact_dir / "deployment_bundle"
        self.assertEqual(read_json(bundle / "decision_policy.json")["threshold"], self.config.models[0].threshold)
        self.assertEqual(file_hash(bundle / "pipeline.joblib"), report["models"]["selected_rf"]["files"]["model"]["sha256"])

    def test_missing_gate_rejects_before_test_csv_access(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=FileNotFoundError("잠금 없음")), self.assertRaises(FileNotFoundError):
            load_test(self.config.dataset_dir, self.data, self.root / "missing_gate", "bad", self.root / "missing_receipt")

    def test_no_pre_test_candidate_never_opens_test(self) -> None:
        def no_candidate(config, descriptors, data, windows, directory):
            costs, evidence = self.fake_benchmark(config, descriptors, data, windows, directory)
            for value in costs.values():
                value["summary"]["single_request_worst_process_p95_ms"] = 1000.0
            return costs, evidence

        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv"}:
                raise AssertionError("후보가 없으면 Test 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(evaluation, "benchmark_models", no_candidate), patch.object(Path, "open", guarded):
            report = evaluation.run_evaluation(self.config)
        self.assertFalse(report["test_evaluated"])
        self.assertEqual(report["status"], "incomplete_no_pre_test_candidate")

    def test_test_failure_withholds_bundle_without_model_reselection(self) -> None:
        original = evaluation.calculate_metrics
        calls = []
        def fail(y, probability, threshold):
            metrics = original(y, probability, threshold)
            metrics["recall"] = 0.0
            calls.append(threshold)
            return metrics
        with patch.object(evaluation, "calculate_metrics", fail):
            report = self.evaluate()
        self.assertEqual(report["selected_model_id"], "selected_rf")
        self.assertEqual(report["approval"]["status"], "withheld_after_test")
        self.assertIsNone(report["deployment_bundle"])
        self.assertFalse((self.config.artifact_dir / "deployment_bundle").exists())
        self.assertEqual(calls, [self.config.models[0].threshold])

    def test_replay_is_marked_and_changed_policy_receipt_is_rejected(self) -> None:
        first = self.evaluate()
        repeated = self.config.model_copy(update={"artifact_dir": self.root / "final_replay", "report_prefix": self.root / "final_replay_report"})
        second = self.evaluate(repeated)
        self.assertFalse(first["holdout_previously_reserved"])
        self.assertTrue(second["holdout_previously_reserved"])
        self.assertEqual(first["test_results"], second["test_results"])
        gate_path = self.config.artifact_dir / "candidate_lock.json"
        altered = read_json(gate_path)
        altered["policy_signature"] = "changed"
        path = self.root / "changed_gate.json"
        write_json(path, altered)
        with self.assertRaisesRegex(ValueError, "새 독립 평가"):
            reserve_holdout(self.config.holdout_registry_dir, path, file_hash(path))

    def test_corrupted_test_hash_blocks_completion(self) -> None:
        with (self.config.dataset_dir / "X_test.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "Test 파일 SHA"):
            self.evaluate()
        self.assertFalse((self.config.artifact_dir / "evaluation_manifest.json").exists())

    def test_existing_outputs_and_readonly_source_are_preserved(self) -> None:
        invalid = self.config.model_copy(update={"artifact_dir": self.config.dataset_dir / "invalid"})
        with self.assertRaises(ValueError):
            self.evaluate(invalid)
        self.evaluate()
        before = file_hash(self.config.artifact_dir / "evaluation_manifest.json")
        with self.assertRaises(FileExistsError):
            self.evaluate()
        self.assertEqual(file_hash(self.config.artifact_dir / "evaluation_manifest.json"), before)

    def test_real_cost_worker_measurement_reads_only_validation_npz(self) -> None:
        from evaluation.operational_final_models import freeze_models
        from data_preparation.operational_sequences import load_split
        models, _, _ = freeze_models(self.config, self.data, load_split(self.config.sequence_dir, "validation"))
        path = self.root / "cost_input.npz"
        with path.open("xb") as stream:
            np.savez_compressed(stream, X=self.data.validation.X.to_numpy())
        job = self.root / "cost_job.json"
        write_json(job, {"descriptor": models["selected_rf"], "benchmark": self.config.benchmark.model_dump(),
                         "input_path": str(path), "input_sha256": file_hash(path), "input_split": "validation", "sample_indices": [0, 1, 2]})
        result = measure(job)
        self.assertEqual(len(result["single_request"]["features_to_decision"]["samples_ms"]), 3)
        self.assertTrue(result["single_batch_decisions_equal"])
        self.assertGreater(result["memory"]["after_benchmark"]["rss_bytes"], 0)
        self.assertFalse(result["test_files_read"])

    def test_worker_failure_stops_before_candidate_and_test(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="테스트 worker 실패")
        with patch.object(evaluation.subprocess, "run", return_value=completed), self.assertRaisesRegex(ValueError, "비용 worker 실패"):
            evaluation.run_evaluation(self.config)
        self.assertFalse((self.config.artifact_dir / "candidate_lock.json").exists())
        self.assertFalse(self.config.holdout_registry_dir.exists())


if __name__ == "__main__":
    unittest.main()
