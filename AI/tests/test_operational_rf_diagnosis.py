from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import ValidationError
from sklearn.pipeline import Pipeline

from data_collection.quality import read_json
from data_collection.recovery_design import compile_recovery_plans, summarize_recovery_design
from data_collection.traffic import planned_runs
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_prefix_features import prefix_observations, transform_prefix_features, TRANSFORM_COLUMNS
from evaluation import analyze_operational_rf_errors as diagnosis
from training.operational_data import OperationalSplit, OperationalData
from training.train_operational_logistic_regression import calculate_metrics, write_json


def source_split(records=None, split="validation") -> OperationalSplit:
    records = records or [("c", "r", "s", 1, 0.0, 66), ("c", "r", "s", 2, 12.0, 66),
                          ("c", "r", "s", 3, 0.1, 66), ("c", "r2", "s", 1, 0.0, 22)]
    features, metadata = [], []
    for campaign, run, session, index, interval, received in records:
        features.append([22, 14, 20.0 + index, interval, received, (index - 1) * 23, 0])
        metadata.append({"campaign_id": campaign, "run_id": run, "session_id": session,
                         "request_index": str(index), "row_id": f"{campaign}:{run}:{session}:{index}", "split": split,
                         "scenario_id": "normal_batched_chat" if run == "r" else "controlled_burst_chat"})
    labels = np.array([0 if item["scenario_id"] == "normal_batched_chat" else 1 for item in metadata], dtype="int64")
    return OperationalSplit(pd.DataFrame(features, columns=FEATURE_NAMES, dtype="float64"), labels, pd.DataFrame(metadata))


class PrefixTests(unittest.TestCase):
    def test_prefix_means_bytes_and_first_message(self) -> None:
        result = prefix_observations(source_split())
        np.testing.assert_allclose(result.prefix_mean_interval_ms, [0, 12, 6.05, 0])
        np.testing.assert_array_equal(result.parsed_bytes_prefix_total, [22, 44, 66, 22])
        np.testing.assert_array_equal(result.recv_ahead_of_parsed_bytes, [44, 22, 0, 0])
        self.assertEqual(result.recv_counter_same_as_previous.tolist(), [False, True, True, False])

    def test_future_mutation_does_not_change_earlier_prefix(self) -> None:
        source = source_split()
        before = prefix_observations(source)
        source.X.loc[2, "message_interval_ms"] = 200.0
        source.X.loc[2, "bytes_received_total"] = 1000.0
        after = prefix_observations(source)
        pd.testing.assert_frame_equal(before.iloc[:2], after.iloc[:2])

    def test_same_session_id_in_different_runs_or_campaigns_is_isolated(self) -> None:
        source = source_split([("a", "r", "s", 1, 0.0, 22), ("b", "r", "s", 1, 0.0, 22),
                               ("a", "r2", "s", 1, 0.0, 22)])
        self.assertEqual(prefix_observations(source).is_first_message.tolist(), [True, True, True])

    def test_interleaved_and_shuffled_rows_keep_original_output_alignment(self) -> None:
        source = source_split([("c", "r", "s", 3, 0.1, 66), ("c", "r2", "s", 1, 0.0, 22),
                               ("c", "r", "s", 1, 0.0, 66), ("c", "r", "s", 2, 12.0, 66)])
        np.testing.assert_allclose(prefix_observations(source).prefix_mean_interval_ms, [6.05, 0, 0, 12])

    def test_missing_duplicate_or_invalid_request_index_is_rejected(self) -> None:
        for indices in ([1, 3], [1, 1], [2], [1, 1.5]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                prefix_observations(source_split([("c", "r", "s", index, 0.0, 100) for index in indices]))

    def test_test_or_mixed_splits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            prefix_observations(source_split(split="test"))
        source = source_split()
        source.metadata.loc[0, "split"] = "train"
        with self.assertRaises(ValueError):
            prefix_observations(source)

    def test_counter_time_and_frame_integrity(self) -> None:
        for name, position, value in (("bytes_received_total", 2, 10), ("bytes_sent_total", 2, 0),
                                      ("connection_duration_ms", 2, 0), ("message_interval_ms", 0, 1),
                                      ("packet_size", 1, 100), ("message_interval_ms", 1, float("nan"))):
            source = source_split()
            source.X.loc[position, name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                prefix_observations(source)

    def test_candidate_dimensions_labels_and_raw_values_unchanged(self) -> None:
        source = source_split()
        original = source.X.copy()
        for transform_id, columns in TRANSFORM_COLUMNS.items():
            transformed = transform_prefix_features(source, transform_id)
            self.assertEqual(list(transformed.columns), columns)
            source.y = 1 - source.y
            pd.testing.assert_frame_equal(transformed, transform_prefix_features(source, transform_id))
        pd.testing.assert_frame_equal(source.X, original)
        with self.assertRaises(ValueError):
            transform_prefix_features(source, "unknown")


class RecoveryDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.design = read_json(AI_ROOT / "configs" / "operational_recovery_plan.json")
        self.previous = read_json(AI_ROOT / "configs" / "operational_augmentation_plan.json")

    def test_deterministic_full_batch_pacing_crossing_in_every_campaign(self) -> None:
        plans = compile_recovery_plans(self.design, self.previous)
        repeated = compile_recovery_plans(self.design, self.previous)
        self.assertEqual([plan.model_dump() for _, plan in plans], [plan.model_dump() for _, plan in repeated])
        for _, plan in plans:
            combinations = set()
            for repeat in range(plan.repeats):
                profiles = {item.scenario_id: item for item in plan.profiles_for_repeat(repeat)}
                normal, burst = profiles["normal_batched_chat"], profiles["controlled_burst_chat"]
                combinations.add((normal.batch_size, normal.interval_min_ms, normal.interval_max_ms))
                for name in ("batch_size", "concurrency", "sessions", "messages_min", "messages_max", "text_sizes"):
                    self.assertEqual(getattr(normal, name), getattr(burst, name))
                self.assertEqual((burst.interval_min_ms, burst.interval_max_ms), (0, 0))
            self.assertEqual(len(combinations), 9)
        summary = summarize_recovery_design(self.design, plans)
        self.assertEqual(summary["planned_runs"], 378)
        self.assertEqual(summary["planned_sessions"], 1512)
        self.assertEqual(summary["planned_trial_count"], 20)
        self.assertFalse(summary["collection_performed"])

    def test_new_seed_and_run_seed_uniqueness(self) -> None:
        plans = compile_recovery_plans(self.design, self.previous)
        seeds = [seed for _, plan in plans for _, _, seed in planned_runs(plan)]
        self.assertEqual(len(set(seeds)), 378)
        self.design["campaign_slots"][0]["seed"] = self.previous["campaign_slots"][0]["seed"]
        with self.assertRaises(ValueError):
            compile_recovery_plans(self.design, self.previous)

    def test_split_duplicates_safety_and_candidate_expansion_are_rejected(self) -> None:
        bad_values = []
        duplicate = copy.deepcopy(self.design)
        duplicate["campaign_slots"][1]["slot_id"] = duplicate["campaign_slots"][0]["slot_id"]
        bad_values.append(duplicate)
        for section, field, value in (("safety_limits", "destination", "8.8.8.8:4000"),
                                      ("safety_limits", "auto_collect_now", True),
                                      ("generator_controls", "batch_sizes", [1, 4, 16]),
                                      ("development_experiment", "thresholds", [0.1, 0.3]),
                                      ("approval_criteria", "max_false_positive_rate", 0.2)):
            altered = copy.deepcopy(self.design)
            altered[section][field] = value
            bad_values.append(altered)
        for altered in bad_values:
            with self.assertRaises(ValueError):
                compile_recovery_plans(altered, self.previous)


class DiagnosisTests(unittest.TestCase):
    def test_first_message_is_separate_and_fp_fn_counts_match(self) -> None:
        summary, rows = diagnosis.diagnose_split(source_split(), np.array([0.4, 0.1, 0.6, 0.2]), 0.3, 1.0)
        self.assertEqual(rows.outcome.tolist(), ["FP", "TN", "FP", "FN"])
        self.assertEqual(rows.interval_bucket.tolist(), ["first_message", "over_5ms", "at_most_1ms", "first_message"])
        self.assertEqual(summary["metrics"]["confusion_matrix"]["fp"], 2)
        self.assertEqual(summary["metrics"]["confusion_matrix"]["fn"], 1)
        self.assertEqual(summary["normal_session_diagnostics"]["sessions_with_any_fp"], 1)

    def test_probability_and_empty_summary_validation(self) -> None:
        self.assertIsNone(diagnosis.numeric_summary(np.array([]))["median"])
        for values in (np.array([0.1]), np.array([0.1, 0.2, np.nan, 0.3]), np.array([0.1, 0.2, 2.0, 0.3])):
            with self.assertRaises(ValueError):
                diagnosis.diagnose_split(source_split(), values, 0.3, 1.0)

    def test_config_forbids_threshold_changes(self) -> None:
        values = diagnosis.load_config(AI_ROOT / "configs" / "operational_rf_diagnosis.json").model_dump()
        values["threshold"] = 0.5
        with self.assertRaises(ValidationError):
            diagnosis.DiagnosisConfig.model_validate(values)

    def test_guarded_end_to_end_diagnosis_preserves_sources_and_output_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = diagnosis.load_config(AI_ROOT / "configs" / "operational_rf_diagnosis.json")
            replacements = {name: root / name for name in ("dataset_dir", "model_dir", "artifact_dir", "report_prefix")}
            for name in ("comparison_report", "final_report", "recovery_plan", "previous_plan"):
                replacements[name] = root / f"{name}.json"
            config = diagnosis.DiagnosisConfig.model_validate({**config.model_dump(), **replacements})
            config_path = root / "config.json"
            write_json(config_path, config.model_dump(mode="json"))
            for name in ("recovery_plan", "previous_plan"):
                source_name = "operational_recovery_plan.json" if name == "recovery_plan" else "operational_augmentation_plan.json"
                write_json(getattr(config, name), read_json(AI_ROOT / "configs" / source_name))
            for path in (config.model_dir / "pipeline.joblib", config.model_dir / "training_manifest.json",
                         root / "training_report.json", root / "data_collection" / "recovery_design.py",
                         root / "data_preparation" / "operational_prefix_features.py",
                         root.parent / "Server" / "ServerBase" / "Session.cpp",
                         root.parent / "Server" / "FeatureExtraction" / "FeatureCollector.cpp"):
                # 서버 경로도 임시 루트 내부에 두어 실제 작업 파일을 건드리지 않는다.
                if path.is_relative_to(root.parent / "Server"):
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("검증 fixture", encoding="utf-8")
            train, validation = source_split(split="train"), source_split()
            data = OperationalData("fixture", {}, {}, train, validation, {})
            probability = np.array([0.4, 0.1, 0.6, 0.8])
            metrics = calculate_metrics(validation.y, probability, 0.3)
            write_json(config.comparison_report, {"comparison": {"models": {"best_rf_validation": {
                "candidate_id": "rf_leaf_5", "threshold": 0.3, "validation_metrics": metrics}}}})
            model_path = config.model_dir / "pipeline.joblib"
            write_json(config.final_report, {"status": "complete", "approval": {"status": "withheld_after_test"},
                "selected_model_id": "selected_rf", "models": {"selected_rf": {"candidate_id": "rf_leaf_5", "threshold": 0.3,
                "files": {"model": {"sha256": file_hash(model_path)}}}},
                "test_results": {"selected_rf": {"metrics": {}, "by_scenario": {"normal_batched_chat": {}}}}})
            saved = validation.metadata.copy()
            saved["label"], saved["anomaly_probability"] = validation.y, probability
            pipeline = SimpleNamespace(named_steps={"classifier": SimpleNamespace(feature_importances_=np.ones(7) / 7)},
                                       predict_proba=lambda X: np.column_stack((1 - probability, probability)))
            evidence = {"report_path": str(root / "training_report.json"), "artifacts": {"pipeline.joblib": {}}}
            original_hash = file_hash(model_path)
            real_open, real_hash = Path.open, file_hash

            def guarded_open(path, *args, **kwargs):
                if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "operational_final_evaluation.predictions.csv"}:
                    raise AssertionError("이번 진단에서 Test 원본·예측 CSV를 열면 안 됩니다.")
                return real_open(path, *args, **kwargs)

            # 외부 서버 파일은 검사 경로만 모의 처리한다. 실제 실행에서는 실제 해시를 확인한다.
            def guarded_hash(path):
                return "server-fixture" if path.is_relative_to(root.parent / "Server") else real_hash(path)

            def guarded_verify(hashes):
                for path, digest in hashes.items():
                    self.assertEqual(guarded_hash(Path(path)), digest)

            with patch.object(diagnosis, "AI_ROOT", root), patch.object(diagnosis, "file_hash", guarded_hash), \
                    patch.object(diagnosis, "verify_hashes", guarded_verify), patch.object(Path, "open", guarded_open), \
                    patch.object(diagnosis, "load_operational_data", return_value=data), \
                    patch.object(diagnosis, "load_verified_model", return_value=(pipeline,
                        {"validation_metrics": calculate_metrics(validation.y, probability, 0.5)}, evidence, saved)), \
                    patch.object(Pipeline, "fit", side_effect=AssertionError("학습 금지")), \
                    patch("socket.create_connection", side_effect=AssertionError("트래픽 송신 금지")):
                report = diagnosis.run_diagnosis(config, config_path)
                self.assertFalse(report["test_files_read"])
                self.assertFalse(report["model_fit_performed"])
                manifest = read_json(config.artifact_dir / "diagnosis_manifest.json")
                for path, digest in manifest["outputs"].items():
                    self.assertEqual(real_hash(Path(path)), digest)
                rows = pd.read_csv(config.report_prefix.with_suffix(".predictions.csv"))
                self.assertEqual(len(rows), 8)
                self.assertEqual(set(rows["split"]), {"train", "validation"})
                with self.assertRaises(FileExistsError):
                    diagnosis.run_diagnosis(config, config_path)
            self.assertEqual(file_hash(model_path), original_hash)


if __name__ == "__main__":
    unittest.main()
