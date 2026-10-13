from __future__ import annotations

import hashlib
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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import test_operational_dataset as fixtures
from data_collection.augmentation import freeze_reference
from data_collection.campaign import write_json
from data_collection.quality import read_json
from data_preparation.operational_dataset import DatasetConfig, FEATURE_NAMES, build_dataset, file_hash
import evaluation.audit_frozen_operational_baseline as audit_module
from training.operational_data import OperationalSplit, load_operational_data
from training.run_operational_validation_experiment import ExperimentConfig
from training.train_operational_logistic_regression import TrainingConfig, train_model, calculate_metrics


def make_reference(directory: Path) -> Path:
    # 실제 선정 실험을 대신 주장하지 않는 테스트용 고정 정책 묶음이다. 모델은 임시 원본으로만 학습한다.
    old_campaign = fixtures.make_campaign(directory, "legacy", repeats=3)
    dataset = directory / "legacy_dataset"
    build_dataset(DatasetConfig(dataset_id="fixture_v1", campaign_dirs=[old_campaign], output_dir=dataset))
    data = load_operational_data(dataset, "fixture_v1")
    root = directory / "reference"
    root.mkdir()
    protocol = ExperimentConfig(experiment_id="fixture_reference", dataset_id="fixture_v1", dataset_dir=dataset,
                                artifact_dir=root, report_prefix=directory / "unused",
                                thresholds=[0.5, 0.7], candidates=[{"id": "lr_c_1", "kind": "logistic_regression", "C": 1.0}]).model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    write_json(root / "protocol_lock.json", {"status": "locked_before_fit", "protocol": protocol,
                                             "protocol_sha256": digest, "input_sha256": data.input_sha256})
    prefix = "candidates/lr_c_1"
    train_model(TrainingConfig(dataset_id="fixture_v1", dataset_dir=dataset, artifact_dir=root / prefix,
                               report_prefix=root / "candidate_reports" / "lr_c_1"))
    model_path = root / prefix / "pipeline.joblib"
    pipeline = joblib.load(model_path)
    metrics = calculate_metrics(data.validation.y, pipeline.predict_proba(data.validation.X)[:, 1], 0.7)
    write_json(root / "selection.json", {
        "status": "selected", "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "selected_trial": {"candidate_id": "lr_c_1", "kind": "logistic_regression", "threshold": 0.7, "metrics": metrics},
        "threshold": 0.7, "positive_class": 1, "comparison": ">=", "refit_with_validation": False,
        "test_evaluated": False, "label_map": data.label_map, "protocol_sha256": digest,
        "rule": protocol["selection_rule"], "model_path": f"{prefix}/pipeline.joblib", "model_sha256": file_hash(model_path),
        "feature_schema_path": f"{prefix}/feature_schema.json", "label_map_path": f"{prefix}/label_map.json",
    })
    paths = [root / "selection.json", root / "protocol_lock.json", root / "candidate_reports" / "lr_c_1.json"]
    paths.extend((root / prefix / name) for name in ("pipeline.joblib", "training_manifest.json", "feature_schema.json",
                                                   "label_map.json", "decision_policy.json"))
    write_json(root / "experiment_manifest.json", {"status": "complete", "protocol_sha256": digest,
                                                  "input_sha256": data.input_sha256,
                                                  "artifacts": {path.relative_to(root).as_posix(): {"sha256": file_hash(path)} for path in paths}})
    return root


class FrozenBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="frozen_baseline_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.reference = make_reference(self.directory)
        roots = [fixtures.make_campaign(self.directory, f"new_{index}", repeats=2,
                                        offset_seconds=1000 + index * 100, seed=100 + index) for index in range(3)]
        registry_path = self.directory / "assignment_manifest.json"
        write_json(registry_path, {"assignment_version": "v1", "prepared_before_collection": True,
                                   "target_dataset_id": "fixture_v2", "split_policy": "preassigned_campaign_holdout",
                                   "locked_at_utc": "2025-12-31T00:00:00+00:00",
                                   "reference_baseline": freeze_reference(self.reference / "selection.json"),
                                   "campaigns": [{"campaign_id": root.name, "slot_id": f"slot_{index}", "seed": 100 + index,
                                                  "split": split, "campaign_dir": str(root),
                                                  "plan_snapshot": read_json(root / "campaign_manifest.json")["plan_snapshot"]}
                                                 for index, (root, split) in enumerate(zip(roots, ("train", "validation", "test")))]})
        self.dataset = self.directory / "new_dataset"
        build_dataset(DatasetConfig(config_version="v2", dataset_id="fixture_v2", campaign_dirs=roots, output_dir=self.dataset,
                                    split_policy="preassigned_campaign_holdout", campaign_registry=registry_path))
        self.prefix = self.directory / "audit"

    def audit(self, **overrides) -> dict:
        values = dict(dataset_dir=self.dataset, dataset_id="fixture_v2", experiment_dir=self.reference,
                      report_prefix=self.prefix, expected_rows=6)
        values.update(overrides)
        return audit_module.audit(**values)

    def refresh_hash(self, relative: str) -> None:
        path = self.reference / "experiment_manifest.json"
        manifest = read_json(path)
        manifest["artifacts"][relative]["sha256"] = file_hash(self.reference / relative)
        write_json(path, manifest)

    def test_no_fit_or_traffic_and_only_new_validation_is_predicted_once(self) -> None:
        data = load_operational_data(self.dataset, "fixture_v2")
        model_files = [path for path in self.reference.rglob("*") if path.is_file()]
        before = {path: file_hash(path) for path in model_files}
        original = Pipeline.predict_proba
        calls = []

        def predict(pipeline, X, **kwargs):
            pd.testing.assert_frame_equal(X, data.validation.X)
            calls.append(len(X))
            return original(pipeline, X, **kwargs)

        with patch.object(Pipeline, "fit", side_effect=AssertionError("재학습 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("scaler fit 금지")), \
                patch("socket.create_connection", side_effect=AssertionError("트래픽 금지")), \
                patch.object(Pipeline, "predict_proba", predict):
            report = self.audit()
        self.assertEqual(calls, [6])
        self.assertEqual(before, {path: file_hash(path) for path in model_files})
        self.assertEqual(report["model_evidence"]["scaler_samples_seen"], 3)
        self.assertTrue(report["model_state_unchanged"])
        self.assertFalse(report["threshold_changed"])
        self.assertFalse(report["train_predicted"])

    def test_test_old_dataset_csv_jsonl_and_summary_are_not_opened(self) -> None:
        original = Path.open

        def guarded(path, *args, **kwargs):
            if (path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "collection_summary.json"} or
                    path.suffix == ".jsonl" or ("legacy_dataset" in path.parts and path.suffix == ".csv")):
                raise AssertionError("Test·기존 CSV·원본 JSONL·종료 요약 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            report = self.audit()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["legacy_dataset_csv_read"])
        self.assertFalse(report["raw_jsonl_read"])

    def test_missing_test_csv_does_not_block_audit(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.dataset / name).unlink()
        self.assertEqual(self.audit()["validation_metrics"]["rows"], 6)

    def test_prediction_and_error_csv_match_matrix_and_locked_threshold(self) -> None:
        report = self.audit()
        predictions = pd.read_csv(self.prefix.with_suffix(".predictions.csv"))
        errors = pd.read_csv(self.prefix.with_suffix(".errors.csv"))
        matrix = report["validation_metrics"]["confusion_matrix"]
        self.assertEqual(len(predictions), 6)
        self.assertEqual(len(errors), matrix["fp"] + matrix["fn"])
        self.assertTrue({"row_id", "source_line", "run_id", "session_id", "label", "anomaly_probability"}.issubset(predictions.columns))
        self.assertEqual(set(predictions["threshold"]), {0.7})
        np.testing.assert_array_equal(predictions.predicted_label, (predictions.anomaly_probability >= 0.7).astype("int64"))
        self.assertEqual(set(predictions.split), {"validation"})
        self.assertEqual(report["csv_outputs"]["errors"]["sha256"], file_hash(self.prefix.with_suffix(".errors.csv")))
        self.assertEqual(sum(run["fp"] for run in report["validation_by_run"].values()), matrix["fp"])
        self.assertEqual(sum(run["fn"] for run in report["validation_by_run"].values()), matrix["fn"])

    def test_header_rejection_has_separate_coverage(self) -> None:
        coverage = self.audit()["validation_coverage"]
        self.assertEqual(coverage["planned_runs"], 6)
        self.assertEqual(coverage["message_observable_runs"], 4)
        self.assertEqual(coverage["header_rejection_runs"], 2)
        self.assertEqual(coverage["planned_sessions"], 6)
        self.assertEqual(coverage["header_rejection_sessions"], 2)

    def test_tampered_model_is_rejected_before_loading(self) -> None:
        with (self.reference / "candidates" / "lr_c_1" / "pipeline.joblib").open("ab") as stream:
            stream.write(b"changed")
        with patch.object(joblib, "load", side_effect=AssertionError("변조 joblib 로딩 금지")):
            with self.assertRaises(ValueError):
                self.audit()
        self.assertFalse(self.prefix.with_suffix(".json").exists())

    def test_altered_selection_threshold_cannot_override_07(self) -> None:
        path = self.reference / "selection.json"
        selection = read_json(path)
        selection["threshold"] = selection["selected_trial"]["threshold"] = 0.5
        write_json(path, selection)
        self.refresh_hash("selection.json")
        with self.assertRaisesRegex(ValueError, "threshold 0.7"):
            self.audit()

    def test_registry_hash_change_is_rejected(self) -> None:
        path = self.directory / "assignment_manifest.json"
        registry = read_json(path)
        registry["campaigns"][1]["split"] = "train"
        write_json(path, registry)
        with self.assertRaisesRegex(ValueError, "registry의 SHA-256"):
            self.audit()

    def test_current_selection_must_match_precollection_reference(self) -> None:
        path = self.directory / "assignment_manifest.json"
        registry = read_json(path)
        registry["reference_baseline"]["threshold"] = 0.8
        write_json(path, registry)
        manifest_path = self.dataset / "dataset_manifest.json"
        manifest = read_json(manifest_path)
        manifest["source_file_sha256"][str(path)] = file_hash(path)
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "수집 전에 고정"):
            self.audit()

    def test_new_input_sha256_is_verified(self) -> None:
        with (self.dataset / "X_validation.csv").open("a", encoding="utf-8") as stream:
            stream.write("changed\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.audit()

    def test_wrong_expected_row_count_is_rejected_without_outputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "행 수"):
            self.audit(expected_rows=513)
        self.assertFalse(self.prefix.with_suffix(".predictions.csv").exists())

    def test_changed_model_memory_state_is_rejected(self) -> None:
        original = Pipeline.predict_proba

        def mutate(pipeline, X):
            pipeline.named_steps["classifier"].coef_[0, 0] += 1
            return original(pipeline, X)

        with patch.object(Pipeline, "predict_proba", mutate):
            with self.assertRaisesRegex(ValueError, "메모리 상태"):
                self.audit()
        self.assertFalse(self.prefix.with_suffix(".predictions.csv").exists())

    def test_nonfinite_probability_is_rejected(self) -> None:
        with patch.object(Pipeline, "predict_proba", return_value=np.full((6, 2), np.nan)):
            with self.assertRaisesRegex(ValueError, "모델 확률"):
                self.audit()

    def test_existing_outputs_and_input_directories_are_protected(self) -> None:
        self.audit()
        before = file_hash(self.prefix.with_suffix(".json"))
        with self.assertRaises(FileExistsError):
            self.audit()
        self.assertEqual(before, file_hash(self.prefix.with_suffix(".json")))
        with self.assertRaisesRegex(ValueError, "폴더 안"):
            self.audit(report_prefix=self.dataset / "invalid")

    def test_reference_scaler_metadata_mismatch_is_rejected(self) -> None:
        relative = "candidate_reports/lr_c_1.json"
        path = self.reference / relative
        report = read_json(path)
        report["learned_parameters"]["scaler_samples_seen"] += 1
        write_json(path, report)
        self.refresh_hash(relative)
        with self.assertRaisesRegex(ValueError, "scaler·가중치"):
            self.audit()


class PrefixDiagnosticTests(unittest.TestCase):
    def test_same_session_id_in_different_runs_and_prefix_denominators(self) -> None:
        metadata = pd.DataFrame({"run_id": ["a"] * 4 + ["b"] * 2, "session_id": ["1"] * 6,
                                 "scenario_id": ["controlled_burst_chat"] * 6, "request_index": [1, 2, 3, 4, 1, 2]})
        X = pd.DataFrame({"connection_duration_ms": [0, 1, 2, 3, 0, 1]})
        split = OperationalSplit(X, np.ones(6, dtype="int64"), metadata)
        result = audit_module.session_diagnostics(split, np.array([0.2, 0.7, 0.9, 0.1, 0.3, 0.4]))
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(result["sessions"][0]["first_positive_request_index"], 2)
        self.assertEqual(result["sessions"][0]["first_positive_elapsed_from_first_message_ms"], 1)
        self.assertIsNone(result["sessions"][1]["first_positive_request_index"])
        self.assertEqual([row["observed_messages"] for row in result["burst_by_request_index"]], [2, 2, 1, 1])
        self.assertEqual([row["fn"] for row in result["burst_by_request_index"]], [2, 1, 0, 1])
        self.assertEqual(result["by_scenario"]["controlled_burst_chat"]["sessions_with_any_positive"], 1)


if __name__ == "__main__":
    unittest.main()
