from __future__ import annotations

import copy
import io
import os
import subprocess
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
from pydantic import ValidationError
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import evaluation.run_operational_recovery_cost as cost
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.compare_operational_recovery import make_trial
from evaluation.operational_cost_worker import latency_summary, measure
from training.operational_data import OperationalData, OperationalSplit
from training.train_operational_logistic_regression import write_json


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = read_json(AI_ROOT / "configs" / "operational_recovery_plan.json")["approval_criteria"]
        self.summary = {"single_request_worst_process_p95_ms": 25.0,
                        "maximum_native_process_peak_rss_bytes": 512 * cost.MIB,
                        "required_model_files_bytes": 50 * cost.MIB}

    def test_exact_boundaries_pass_without_rounding(self) -> None:
        self.assertEqual(cost.cost_rejections(self.summary, self.criteria), [])
        for name in self.summary:
            self.assertTrue(cost.cost_rejections({**self.summary, name: self.summary[name] + 0.001}, self.criteria))

    def test_missing_nonfinite_negative_and_zero_peak_are_rejected(self) -> None:
        for name in self.summary:
            for value in (None, float("nan"), float("inf"), -1):
                self.assertTrue(cost.cost_rejections({**self.summary, name: value}, self.criteria))
        self.assertTrue(cost.cost_rejections({**self.summary, "maximum_native_process_peak_rss_bytes": 0}, self.criteria))

    def test_policy_and_compute_threads_cannot_be_changed(self) -> None:
        values = cost.load_config(AI_ROOT / "configs" / "operational_recovery_cost.json").model_dump()
        for update in ({"candidate_id": "rf_prefix_mean5"}, {"threshold": 0.6}, {"input_transform_id": "without_io5_v1"}):
            with self.assertRaises(ValidationError):
                cost.CostConfig.model_validate({**values, **update})
        values["benchmark"]["cpu_threads"] = 2
        with self.assertRaises(ValidationError):
            cost.CostConfig.model_validate(values)

    def test_protected_test_paths_are_rejected_before_hashing(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv", "test_input.npz", "records.jsonl", "collection_summary.json"):
            with patch.object(cost, "verify_hashes", side_effect=AssertionError("금지 파일 해시 접근")), self.assertRaises(ValueError):
                cost.verify_without_holdout({name: "not_read"})


class RecoveryCostTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model_root = self.root / "training"
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.candidate = self.model_root / "candidates" / "rf_raw7"
        self.candidate.mkdir(parents=True)
        self.design = AI_ROOT / "configs" / "operational_recovery_plan.json"
        design = read_json(self.design)

        def split(name, labels):
            # 임시 합성 자료로 파일 보호·접근 순서만 검사하며 실제 사용자 성능을 대신하지 않는다.
            X = pd.DataFrame([[22 if label == 0 else 200, 14 if label == 0 else 192,
                               20.0 if label == 0 else 1.0, 10.0 if label == 0 else 0.0,
                               22 if label == 0 else 200, 30 if label == 0 else 0, 0] for label in labels],
                             columns=FEATURE_NAMES, dtype="float64")
            metadata = pd.DataFrame({"row_id": [f"{name}:{index}" for index in range(len(labels))],
                "campaign_id": name, "run_id": [f"{name}:{index}" for index in range(len(labels))], "session_id": "1",
                "request_index": "1", "split": name,
                "scenario_id": ["normal_batched_chat" if label == 0 else "controlled_burst_chat" for label in labels]})
            return OperationalSplit(X, np.asarray(labels, dtype="int64"), metadata)

        train, validation = split("train", [0, 1] * 20), split("validation", [0, 1, 0, 1])
        write_json(self.dataset / "dataset_manifest.json", {"splits": {"test": {"rows": 2}},
            "output_files": {name: {"sha256": str(index) * 64} for index, name in enumerate(cost.TEST_FILES)}})
        schema = {"feature_contract_id": "server_application_message_v2", "raw_input_features": FEATURE_NAMES}
        self.data = OperationalData("fixture", schema, {"normal": 0, "controlled_anomaly": 1}, train, validation,
                                    {"dataset_manifest.json": file_hash(self.dataset / "dataset_manifest.json")})
        pipeline = Pipeline([("classifier", RandomForestClassifier(**design["development_experiment"]["model_parameters"]))]).fit(train.X, train.y)
        joblib.dump(pipeline, self.candidate / "pipeline.joblib", compress=3)
        probability = pipeline.predict_proba(validation.X)[:, 1]
        self.trial = make_trial({"candidate_id": "rf_raw7", "input_transform_id": "raw7_v1"}, validation,
                               probability, 0.7, design["approval_criteria"])
        self.assertTrue(self.trial["validation_feasible"])
        saved = validation.metadata.copy()
        saved["label"], saved["anomaly_probability"] = validation.y, probability
        saved.to_csv(self.candidate / "validation_predictions.csv", index=False)
        write_json(self.candidate / "training_summary.json", {"train_rows": len(train.y), "fit_scope": "new_train_only",
            "model_sha256": file_hash(self.candidate / "pipeline.joblib"), "train_matrix_sha256": cost.matrix_hash(train.X),
            "validation_matrix_sha256": cost.matrix_hash(validation.X)})
        write_json(self.candidate / "model_input_schema.json", {"input_transform_id": "raw7_v1", "model_input_features": FEATURE_NAMES,
            "raw_input_features": FEATURE_NAMES, "requires_ordered_session_prefix": False, "preprocessing": "identity_no_scaler"})
        write_json(self.candidate / "default_decision_policy.json", {"threshold": 0.5})
        write_json(self.model_root / "raw_feature_schema.json", schema)
        write_json(self.model_root / "label_map.json", self.data.label_map)
        self.protected = self.root / "protected_source.bin"
        self.protected.write_bytes(b"protected-source")
        protocol = {"recovery_plan": design, "full_rf_parameters": pipeline.named_steps["classifier"].get_params()}
        self.protocol_hash = cost.digest(protocol)
        self.training_report = self.root / "training_report.json"
        write_json(self.training_report, {"status": "complete", "dataset_id": "fixture", "input_sha256": self.data.input_sha256,
            "protocol": protocol, "protocol_sha256": self.protocol_hash, "selection": {"selected_trial": self.trial},
            "trials": [self.trial], "protected_sha256": {str(self.protected): file_hash(self.protected)}})
        write_json(self.model_root / "protocol_lock.json", {"protocol": protocol, "protocol_sha256": self.protocol_hash})
        write_json(self.model_root / "selection.json", {"status": "selected_pending_cost_and_test", "selected_trial": self.trial,
            "protocol_sha256": self.protocol_hash, "threshold": 0.7, "positive_class": 1, "comparison": ">=",
            "model_path": str(self.candidate / "pipeline.joblib")})
        self.refresh_manifest()
        values = cost.load_config(AI_ROOT / "configs" / "operational_recovery_cost.json").model_dump()
        values.update(experiment_id="fixture_cost", dataset_id="fixture", dataset_dir=self.dataset, training_root=self.model_root,
                      training_report=self.training_report, artifact_dir=self.root / "cost", report_prefix=self.root / "report",
                      expected_train_rows=40, expected_validation_rows=4, expected_test_rows=2)
        values["benchmark"].update(warmup_repeats=1, single_request_repeats=3, batch_repeats=1)
        self.config = cost.CostConfig.model_validate(values)

    def refresh_manifest(self) -> None:
        path = self.model_root / "experiment_manifest.json"
        value = {"status": "complete", "input_sha256": self.data.input_sha256, "protocol_sha256": self.protocol_hash,
            "artifacts": {item.relative_to(self.model_root).as_posix(): {"sha256": file_hash(item)}
                          for item in self.model_root.rglob("*") if item.is_file() and item != path},
            "reports": {str(self.training_report): file_hash(self.training_report)}, "test_evaluated": False, "deployment_ready": False}
        if path.exists():
            path.unlink()
        write_json(path, value)

    def fake_benchmark(self, config, descriptors, data, windows, directory):
        self.assertIsNone(windows)
        self.assertTrue((config.artifact_dir / "cost_protocol_lock.json").is_file())
        self.assertEqual(len(descriptors), 1)
        descriptor = next(iter(descriptors.values()))
        indices = np.random.default_rng(config.benchmark.sampling_seed).choice(len(data.validation.y),
            size=config.benchmark.single_request_repeats, replace=False).tolist()
        runs = []
        for index in range(config.benchmark.process_repeats):
            runs.append({"status": "complete", "pid": index + 100, "input_split": "validation", "input_rows": len(data.validation.y),
                "descriptor": copy.deepcopy(descriptor), "sample_indices": indices, "torch_imported": False,
                "model_training_performed": False, "test_files_read": False, "single_batch_decisions_equal": True,
                "single_request": {name: latency_summary([1.0] * config.benchmark.single_request_repeats)
                                   for name in ("features_to_decision", "model_only")},
                "batch": {**latency_summary([2.0] * config.benchmark.batch_repeats), "amortized_per_message_median_ms": 0.5},
                "memory": {"after_benchmark": {"native_peak_working_set_bytes": 128 * cost.MIB}, "model_and_scaler_array_bytes": 1000},
                "required_model_files_bytes": sum(item["bytes"] for item in descriptor["files"].values()),
                "environment": {"cpu_threads": 1, "threadpools": [{"num_threads": 1}]}})
        directory.mkdir()
        evidence_path = directory / "fixture_cost.json"
        write_json(evidence_path, {"runs": runs})
        return {descriptor["id"]: {"summary": cost.summarize_cost(runs), "process_records": runs}}, {str(evidence_path): file_hash(evidence_path)}

    def execute(self, benchmark=None, config=None):
        with patch.object(cost, "load_operational_data", return_value=self.data), \
                patch.object(cost, "benchmark_models", benchmark or self.fake_benchmark), redirect_stdout(io.StringIO()):
            return cost.run(config or self.config)

    def test_no_fit_network_test_or_raw_access_and_lock_before_measurement(self) -> None:
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in cost.TEST_FILES or path.suffix == ".jsonl" or path.name == "collection_summary.json":
                raise AssertionError("Test·수집 레코드 접근 금지")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded), patch.object(Pipeline, "fit", side_effect=AssertionError("재학습 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("정규화 학습 금지")), \
                patch("socket.create_connection", side_effect=AssertionError("송신 금지")):
            result = self.execute()
        self.assertEqual(result["status"], "candidate_locked_pending_independent_test")
        for name in ("test_evaluated", "holdout_reserved", "deployment_ready"):
            self.assertFalse(result[name])
        self.assertFalse((self.config.artifact_dir / "deployment_bundle").exists())
        gate = read_json(Path(result["candidate_lock_path"]))
        self.assertEqual(gate["status"], "candidate_locked_before_test")
        self.assertEqual(gate["descriptor"]["threshold"], 0.7)
        self.assertEqual(set(gate["expected_test_sha256"]), set(cost.TEST_FILES))
        cost.verify_without_holdout(gate["pre_test_protected_sha256"])

    def test_candidate_copy_preserves_default_and_requires_no_prefix(self) -> None:
        before = file_hash(self.candidate / "pipeline.joblib")
        result = self.execute()
        files = result["descriptor"]["files"]
        self.assertEqual(len(files), 5)
        self.assertEqual(file_hash(Path(files["model"]["path"])), before)
        self.assertEqual(read_json(self.candidate / "default_decision_policy.json")["threshold"], 0.5)
        self.assertEqual(read_json(Path(files["decision_policy"]["path"]))["threshold"], 0.7)
        self.assertFalse(result["descriptor"]["requires_ordered_session_prefix"])
        self.assertEqual(result["cost"]["summary"]["required_model_files_bytes"], sum(item["bytes"] for item in files.values()))

    def test_cost_failure_has_no_fallback_or_test_lock(self) -> None:
        def over_budget(*args):
            costs, evidence = self.fake_benchmark(*args)
            record = next(iter(costs.values()))
            for run in record["process_records"]:
                run["single_request"]["features_to_decision"] = latency_summary([100.0] * 3)
            record["summary"] = cost.summarize_cost(record["process_records"])
            return costs, evidence
        result = self.execute(over_budget)
        self.assertEqual(result["status"], "withheld_before_test_due_to_cost")
        self.assertIsNone(result["candidate_lock_path"])
        self.assertFalse((self.config.artifact_dir / "candidate_lock.json").exists())
        self.assertEqual(read_json(self.config.artifact_dir / "cost_manifest.json")["status"], "complete")

    def test_exception_or_interrupt_records_failure_and_restores_environment(self) -> None:
        for number, exception in enumerate((subprocess.TimeoutExpired("fixture", 1), KeyboardInterrupt())):
            config = self.config.model_copy(update={"artifact_dir": self.root / f"failed_{number}"})
            with patch.dict(os.environ, {"OMP_NUM_THREADS": "9"}):
                with self.assertRaises(type(exception)):
                    self.execute(benchmark=lambda *args: (_ for _ in ()).throw(exception), config=config)
                self.assertEqual(os.environ["OMP_NUM_THREADS"], "9")
            self.assertFalse((config.artifact_dir / "cost_manifest.json").exists())
            self.assertFalse((config.artifact_dir / "candidate_lock.json").exists())
            self.assertEqual(read_json(config.artifact_dir / "cost_failure.json")["status"], "failed")

    def test_artifact_inventory_reports_and_no_overwrite(self) -> None:
        self.execute()
        manifest = read_json(self.config.artifact_dir / "cost_manifest.json")
        for name, item in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.artifact_dir / name), item["sha256"])
        for name, expected in manifest["reports"].items():
            self.assertEqual(file_hash(Path(name)), expected)
        before = file_hash(self.config.artifact_dir / "cost_manifest.json")
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(file_hash(self.config.artifact_dir / "cost_manifest.json"), before)

    def test_protected_source_tampering_aborts_before_measurement(self) -> None:
        with self.protected.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.config.artifact_dir.exists())

    def test_stateful_schema_cannot_be_run_as_raw7(self) -> None:
        path = self.candidate / "model_input_schema.json"
        schema = read_json(path)
        schema["requires_ordered_session_prefix"] = True
        path.unlink()
        write_json(path, schema)
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "원본 입력"):
            self.execute()
        self.assertFalse((self.config.artifact_dir / "cost_protocol_lock.json").exists())

    def test_saved_probability_mismatch_rejects_before_worker(self) -> None:
        path = self.candidate / "validation_predictions.csv"
        saved = pd.read_csv(path)
        saved.loc[0, "anomaly_probability"] = 0.1
        saved.to_csv(path, index=False)
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "확률"):
            self.execute()
        self.assertFalse((self.config.artifact_dir / "cost_protocol_lock.json").exists())

    def test_source_output_paths_and_incorrect_counts_are_rejected(self) -> None:
        for values in ({"artifact_dir": self.dataset / "bad"}, {"artifact_dir": self.model_root / "bad"},
                       {"expected_validation_rows": 400}, {"expected_test_rows": 20},
                       {"report_prefix": self.config.artifact_dir / "inside"}):
            with self.assertRaises(ValueError):
                self.execute(config=self.config.model_copy(update=values))

    def test_inconsistent_threads_samples_or_duplicate_processes_reject_lock(self) -> None:
        for number, mutation in enumerate(("pid", "torch", "threads", "samples", "descriptor")):
            config = self.config.model_copy(update={"artifact_dir": self.root / f"bad_{number}"})
            def invalid(*args):
                records, evidence = self.fake_benchmark(*args)
                result = next(iter(records.values()))
                run = result["process_records"][0]
                if mutation == "pid":
                    run["pid"] = result["process_records"][1]["pid"]
                elif mutation == "torch":
                    run["torch_imported"] = True
                elif mutation == "threads":
                    run["environment"]["threadpools"][0]["num_threads"] = 2
                elif mutation == "samples":
                    run["single_request"]["model_only"]["samples_ms"].pop()
                else:
                    run["descriptor"]["threshold"] = 0.5
                return records, evidence
            with self.assertRaises(ValueError):
                self.execute(invalid, config)
            self.assertFalse((config.artifact_dir / "candidate_lock.json").exists())

    def test_real_worker_single_batch_judgments_match_selected_policy(self) -> None:
        result = self.execute()
        path = self.root / "validation_input.npz"
        with path.open("xb") as stream:
            np.savez_compressed(stream, X=self.data.validation.X.to_numpy())
        job = self.root / "worker_job.json"
        write_json(job, {"descriptor": result["descriptor"], "benchmark": self.config.benchmark.model_dump(),
            "input_path": str(path), "input_sha256": file_hash(path), "input_split": "validation", "sample_indices": [0, 1, 2]})
        measured = measure(job)
        self.assertTrue(measured["single_batch_decisions_equal"])
        self.assertEqual(len(measured["single_request"]["features_to_decision"]["samples_ms"]), 3)
        self.assertGreater(measured["memory"]["after_benchmark"]["rss_bytes"], 0)
        self.assertEqual(measured["descriptor"]["threshold"], 0.7)


if __name__ == "__main__":
    unittest.main()
