from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import ValidationError
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import evaluation.run_operational_recovery_test as evaluation
import test_operational_recovery_cost as cost_fixtures
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, METADATA_COLUMNS, file_hash, fingerprint
from evaluation.operational_final_data import TEST_FILES, holdout_key
from inference_api.payload_validation import OPERATIONAL_CONTRACT
from training.operational_data import OperationalSplit
from training.train_operational_logistic_regression import write_json


def replace_json(path: Path, value: dict) -> None:
    # 임시 테스트 파일만 갱신한다. 실제 사용자 실험·모델은 읽거나 수정하지 않는다.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class RecoveryTestTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = cost_fixtures.RecoveryCostTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.root, self.data = fixture, fixture.root, fixture.data
        self.data.schema["feature_dtypes"] = {name: OPERATIONAL_CONTRACT["features"][name]["dtype"] for name in FEATURE_NAMES}
        replace_json(fixture.model_root / "raw_feature_schema.json", self.data.schema)
        X = self.data.validation.X.copy()
        X["message_interval_ms"] = 0.0
        labels = self.data.validation.y.copy()
        metadata = []
        entries = []
        for index, row in X.iterrows():
            campaign, run = f"test_c{index // 2}", f"test_run_{index}"
            scenario = "normal_batched_chat" if labels[index] == 0 else "controlled_burst_chat"
            typed = {name: int(row[name]) if self.data.schema["feature_dtypes"][name] == "integer" else float(row[name]) for name in FEATURE_NAMES}
            record = {name: "1" for name in METADATA_COLUMNS}
            record.update(row_id=f"test:{index}", campaign_id=campaign, run_id=run, session_id="1", request_index="1",
                scenario_id=scenario, split="test", repeat_index="0", block_id="test", feature_hash=fingerprint(typed),
                timestamp="2026-01-01T00:00:00Z", received_at_utc="2026-01-01T00:00:00Z")
            metadata.append(record)
            entries.append({"campaign_id": campaign, "run_id": run, "scenario_id": scenario, "split": "test",
                            "repeat_index": 0, "block_id": "test", "rows": 1, "sessions": 1})
        self.test = OperationalSplit(X, labels, pd.DataFrame(metadata, columns=METADATA_COLUMNS))
        raw = X.copy()
        for name in FEATURE_NAMES:
            if self.data.schema["feature_dtypes"][name] == "integer":
                raw[name] = raw[name].astype("int64")
        frames = (raw, pd.DataFrame({"label": labels}), self.test.metadata)
        for name, frame in zip(TEST_FILES, frames, strict=True):
            frame.to_csv(fixture.dataset / name, index=False)
        replace_json(fixture.dataset / "split_manifest.json", {"runs": entries})
        manifest = {"dataset_id": "fixture", "splits": {"test": {"rows": 4, "label_distribution": {"0": 2, "1": 2}}},
            "output_files": {name: {"sha256": file_hash(fixture.dataset / name), "rows": len(frame), "columns": list(frame.columns)}
                             for name, frame in zip(TEST_FILES, frames, strict=True)}, "excluded_sources": []}
        replace_json(fixture.dataset / "dataset_manifest.json", manifest)
        self.data.input_sha256 = {"dataset_manifest.json": file_hash(fixture.dataset / "dataset_manifest.json")}
        training = read_json(fixture.training_report)
        training["input_sha256"] = self.data.input_sha256
        replace_json(fixture.training_report, training)
        fixture.refresh_manifest()
        cost_config = fixture.config.model_copy(update={"expected_test_rows": 4})

        def fake_benchmark(config, descriptors, data, windows, directory):
            costs, evidence = fixture.fake_benchmark(config, descriptors, data, windows, directory)
            descriptor = next(iter(descriptors.values()))
            model_dir = directory / descriptor["id"]
            model_dir.mkdir()
            input_path = model_dir / "validation_input.npz"
            with input_path.open("xb") as stream:
                np.savez_compressed(stream, X=data.validation.X.to_numpy())
            job = {"descriptor": descriptor, "benchmark": config.benchmark.model_dump(), "input_path": str(input_path),
                   "input_sha256": file_hash(input_path), "input_split": "validation"}
            write_json(model_dir / "job.json", job)
            evidence.update({str(path): file_hash(path) for path in model_dir.iterdir()})
            return costs, evidence

        fixture.execute(fake_benchmark, cost_config)
        values = evaluation.load_config(AI_ROOT / "configs" / "operational_recovery_test.json").model_dump()
        values.update(experiment_id="fixture_recovery_test", dataset_id="fixture", dataset_dir=fixture.dataset,
            cost_root=cost_config.artifact_dir, cost_report=cost_config.report_prefix.with_suffix(".json"),
            expected_cost_manifest_sha256=file_hash(cost_config.artifact_dir / "cost_manifest.json"),
            expected_candidate_lock_sha256=file_hash(cost_config.artifact_dir / "candidate_lock.json"),
            artifact_dir=self.root / "evaluation", report_prefix=self.root / "evaluation_report",
            holdout_registry_dir=self.root / "holdout_usage", expected_test_rows=4)
        self.config = evaluation.TestConfig.model_validate(values)

    def execute(self, config=None):
        with patch.object(evaluation, "load_operational_data", return_value=self.data), redirect_stdout(io.StringIO()):
            return evaluation.run(config or self.config)

    def test_test_read_requires_verified_cost_protocol_and_receipt_without_fit(self) -> None:
        original = Path.open
        opened = []
        def guarded(path, *args, **kwargs):
            if path.name in TEST_FILES:
                protocol = read_json(self.config.artifact_dir / "evaluation_protocol_lock.json")
                self.assertEqual(protocol["status"], "locked_before_holdout_reservation_and_test")
                self.assertTrue(list(self.config.holdout_registry_dir.glob("*.json")))
                opened.append(path.name)
            if path.suffix == ".jsonl" or path.name == "operational_final_evaluation.predictions.csv":
                raise AssertionError("수집 원본·기존 Test 예측 접근 금지")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded), patch.object(Pipeline, "fit", side_effect=AssertionError("재학습 금지")), \
                patch.object(RandomForestClassifier, "fit", side_effect=AssertionError("재학습 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("정규화 학습 금지")), \
                patch("socket.create_connection", side_effect=AssertionError("송신 금지")):
            result = self.execute()
        self.assertEqual(set(opened), set(TEST_FILES))
        self.assertEqual(result["approval"]["status"], "approved_for_local_advisory_integration")
        self.assertFalse(result["selection_used_test"])
        self.assertFalse(result["model_training_performed"])
        self.assertFalse(result["cost_remeasured"])
        self.assertEqual(result["remaining_major_stages"], 3)

    def test_only_locked_model_is_loaded_and_threshold_never_changes(self) -> None:
        original = evaluation.FrozenRuntime
        models = []
        def guarded(descriptor):
            models.append(descriptor)
            self.assertEqual(descriptor["candidate_id"], "rf_raw7")
            self.assertEqual(descriptor["threshold"], 0.7)
            return original(descriptor)
        with patch.object(evaluation, "FrozenRuntime", guarded):
            result = self.execute()
        self.assertEqual(len(models), 1)
        self.assertEqual(result["threshold"], 0.7)
        self.assertFalse(result["thresholds_changed"])

    def test_cost_manifest_or_lock_change_blocks_before_test_reservation(self) -> None:
        for key in ("expected_candidate_lock_sha256", "expected_cost_manifest_sha256"):
            config = self.config.model_copy(update={key: "0" * 64})
            with self.assertRaisesRegex(ValueError, "해시"):
                self.execute(config)
        self.assertFalse(self.config.holdout_registry_dir.exists())
        self.assertFalse(self.config.artifact_dir.exists())

    def test_incomplete_cost_never_accesses_test(self) -> None:
        path = self.config.cost_root / "cost_manifest.json"
        value = read_json(path)
        value["status"] = "failed"
        replace_json(path, value)
        config = self.config.model_copy(update={"expected_cost_manifest_sha256": file_hash(path)})
        with self.assertRaisesRegex(ValueError, "완료 비용"):
            self.execute(config)
        self.assertFalse(self.config.holdout_registry_dir.exists())

    def test_model_and_saved_cost_hash_changes_reject_before_receipt(self) -> None:
        path = self.config.cost_root / "candidate_bundle" / "pipeline.joblib"
        with path.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.config.holdout_registry_dir.exists())

    def test_failed_test_keeps_policy_and_withholds_bundle(self) -> None:
        original = evaluation.evaluate_test
        def all_negative(source, runtime):
            with patch.object(runtime, "predict_raw", return_value=np.zeros(len(source.y))):
                return original(source, runtime)
        with patch.object(evaluation, "evaluate_test", all_negative):
            result = self.execute()
        self.assertEqual(result["approval"]["status"], "withheld_after_test")
        self.assertEqual(result["threshold"], 0.7)
        self.assertEqual(result["remaining_major_stages"], 4)
        self.assertIsNone(result["deployment_bundle"])
        self.assertFalse((self.config.artifact_dir / "deployment_bundle").exists())
        self.assertEqual(read_json(self.config.artifact_dir / "evaluation_manifest.json")["status"], "complete")

    def test_normal_batched_gate_is_enforced_even_if_overall_fpr_passes(self) -> None:
        gate = read_json(self.config.cost_root / "candidate_lock.json")
        result = {"metrics": {"false_positive_rate": 0.05, "recall": 0.9},
                  "by_scenario": {"normal_batched_chat": {"false_positive_rate": 0.100001}}}
        self.assertIn("정상 묶음 FPR 기준 미달", evaluation.rejection_reasons(result, gate["criteria"]))
        result["by_scenario"]["normal_batched_chat"]["false_positive_rate"] = 0.1
        self.assertEqual(evaluation.rejection_reasons(result, gate["criteria"]), [])

    def test_predictions_and_bundle_retain_exact_model_policy_and_schemas(self) -> None:
        result = self.execute()
        frame = pd.read_csv(self.config.report_prefix.with_suffix(".predictions.csv"), float_precision="round_trip")
        self.assertEqual(frame.row_id.tolist(), self.test.metadata.row_id.tolist())
        np.testing.assert_array_equal(frame.label, self.test.y)
        np.testing.assert_array_equal(frame.prediction, (frame.anomaly_probability >= 0.7).astype("int64"))
        self.assertEqual(len(frame), 4)
        bundle = result["deployment_bundle"]
        self.assertEqual(len(bundle["manifest"]["files"]), 5)
        self.assertFalse(bundle["manifest"]["requires_ordered_session_prefix"])
        self.assertFalse(bundle["manifest"]["production_ready"])
        self.assertFalse(bundle["manifest"]["automatic_blocking_allowed"])
        self.assertFalse(bundle["manifest"]["fastapi_integration_implemented"])
        self.assertTrue(bundle["manifest"]["requires_complete_evaluation_manifest"])
        self.assertTrue(Path(bundle["manifest"]["evaluation_manifest_path"]).is_file())
        for role, file in bundle["manifest"]["files"].items():
            path = Path(bundle["directory"]) / file["path"]
            self.assertEqual(file_hash(path), file["sha256"])
            if role != "decision_policy":
                self.assertEqual(file["sha256"], result["descriptor"]["files"][role]["sha256"])
        policy = read_json(Path(bundle["directory"]) / "decision_policy.json")
        self.assertEqual(policy["threshold"], 0.7)
        self.assertTrue(policy["test_confirmed"])

    def test_manifest_inventory_reports_and_preserved_cost_sources(self) -> None:
        before = file_hash(self.config.cost_root / "cost_manifest.json")
        result = self.execute()
        manifest = read_json(self.config.artifact_dir / "evaluation_manifest.json")
        for name, item in manifest["artifacts"].items():
            self.assertEqual(file_hash(self.config.artifact_dir / name), item["sha256"])
        evaluation.verify_hashes(manifest["reports"])
        evaluation.verify_hashes(result["protected_sha256"])
        self.assertEqual(file_hash(self.config.cost_root / "cost_manifest.json"), before)

    def test_unapproved_verdict_cannot_export_a_bundle(self) -> None:
        gate = read_json(self.config.cost_root / "candidate_lock.json")
        path = self.root / "unapproved_verdict.json"
        write_json(path, {"status": "withheld_after_test", "reasons": ["실패"],
                         "candidate_lock_path": str(self.config.cost_root / "candidate_lock.json")})
        target = self.root / "unapproved_bundle"
        with self.assertRaisesRegex(ValueError, "승인 판정"):
            evaluation.export_approved_bundle(target, gate, self.data, path)
        self.assertFalse(target.exists())

    def test_post_export_write_failure_cannot_claim_a_completed_usable_bundle(self) -> None:
        with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("임시 결과 저장 실패")), self.assertRaises(OSError):
            self.execute()
        self.assertTrue(list(self.config.holdout_registry_dir.glob("*.json")))
        self.assertFalse((self.config.artifact_dir / "evaluation_manifest.json").exists())
        bundle_manifest = read_json(self.config.artifact_dir / "deployment_bundle" / "deployment_manifest.json")
        self.assertTrue(bundle_manifest["requires_complete_evaluation_manifest"])
        self.assertFalse(Path(bundle_manifest["evaluation_manifest_path"]).exists())
        self.assertEqual(read_json(self.config.artifact_dir / "evaluation_failure.json")["status"], "failed")

    def test_same_policy_replay_is_marked_and_not_fresh_independent_test(self) -> None:
        first = self.execute()
        config = self.config.model_copy(update={"artifact_dir": self.root / "replay", "report_prefix": self.root / "replay_report"})
        second = self.execute(config)
        self.assertFalse(first["holdout_previously_reserved"])
        self.assertTrue(second["holdout_previously_reserved"])
        self.assertEqual(second["evaluation_kind"], "same_policy_recheck_of_exposed_test")
        self.assertEqual(first["test"], second["test"])

    def test_receipt_for_changed_policy_is_rejected_without_test_access(self) -> None:
        gate = read_json(self.config.cost_root / "candidate_lock.json")
        registry = self.config.holdout_registry_dir
        registry.mkdir()
        write_json(registry / f"{holdout_key(gate['expected_test_sha256'])}.json", {
            "expected_test_sha256": gate["expected_test_sha256"], "policy_signature": "different_policy",
            "selected_model_id": gate["selected_model_id"]})
        with self.assertRaisesRegex(ValueError, "새 독립 평가"):
            self.execute()
        self.assertFalse((self.config.artifact_dir / "evaluation_manifest.json").exists())

    def test_corrupted_test_retains_reservation_but_no_complete_manifest(self) -> None:
        with (self.config.dataset_dir / "X_test.csv").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "Test 파일 SHA"):
            self.execute()
        self.assertTrue(list(self.config.holdout_registry_dir.glob("*.json")))
        self.assertFalse((self.config.artifact_dir / "evaluation_manifest.json").exists())
        self.assertFalse((self.config.artifact_dir / "deployment_bundle").exists())
        self.assertTrue(read_json(self.config.artifact_dir / "evaluation_failure.json")["holdout_reserved_this_attempt"])

    def test_interrupt_retains_holdout_record_and_does_not_claim_completion(self) -> None:
        with patch.object(evaluation, "load_test", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertTrue(list(self.config.holdout_registry_dir.glob("*.json")))
        self.assertFalse((self.config.artifact_dir / "evaluation_manifest.json").exists())
        self.assertEqual(read_json(self.config.artifact_dir / "evaluation_failure.json")["status"], "failed")

    def test_existing_outputs_and_immutable_paths_are_protected(self) -> None:
        for values in ({"artifact_dir": self.config.dataset_dir / "bad"}, {"artifact_dir": self.config.cost_root / "bad"},
                       {"report_prefix": self.config.artifact_dir / "inside"},
                       {"holdout_registry_dir": self.config.dataset_dir / "usage"}):
            with self.assertRaises(ValueError):
                self.execute(self.config.model_copy(update=values))
        self.execute()
        before = file_hash(self.config.artifact_dir / "evaluation_manifest.json")
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(file_hash(self.config.artifact_dir / "evaluation_manifest.json"), before)

    def test_threshold_model_and_invalid_probability_config_are_rejected(self) -> None:
        values = self.config.model_dump()
        for change in ({"threshold": 0.6}, {"candidate_id": "rf_prefix_mean5"}, {"expected_candidate_lock_sha256": "bad"}):
            with self.assertRaises(ValidationError):
                evaluation.TestConfig.model_validate({**values, **change})
        gate = read_json(self.config.cost_root / "candidate_lock.json")
        runtime = evaluation.FrozenRuntime(gate["descriptor"])
        with patch.object(runtime, "predict_raw", return_value=np.full(4, np.nan)), self.assertRaisesRegex(ValueError, "확률"):
            evaluation.evaluate_test(self.test, runtime)

    def test_session_gap_first_interval_and_counter_regression_are_rejected(self) -> None:
        for column, value in (("request_index", "2"),):
            source = copy.deepcopy(self.test)
            source.metadata.loc[0, column] = value
            with self.assertRaises(ValueError):
                evaluation.validate_test_sessions(source)
        source = copy.deepcopy(self.test)
        source.X.loc[0, "message_interval_ms"] = 1.0
        with self.assertRaises(ValueError):
            evaluation.validate_test_sessions(source)
        source = copy.deepcopy(self.test)
        source.metadata.loc[1, ["campaign_id", "run_id", "session_id"]] = source.metadata.loc[0, ["campaign_id", "run_id", "session_id"]]
        source.metadata.loc[1, "request_index"] = "2"
        source.X.loc[1, "bytes_received_total"] = 1.0
        with self.assertRaises(ValueError):
            evaluation.validate_test_sessions(source)


if __name__ == "__main__":
    unittest.main()
