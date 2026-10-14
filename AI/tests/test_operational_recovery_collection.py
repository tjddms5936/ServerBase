from __future__ import annotations

import copy
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

from data_collection import recovery
from data_collection.campaign import file_hash, write_json
from data_collection.quality import read_json
from data_collection.recovery_design import compile_recovery_plans, summarize_recovery_design


class RecoveryCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rec_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.design_path = self.directory / "design.json"
        self.design = read_json(AI_ROOT / "configs" / "operational_recovery_plan.json")
        write_json(self.design_path, self.design)
        self.previous_plan = AI_ROOT / "configs" / "operational_augmentation_plan.json"
        self.server_exe = self.directory / "server.exe"
        self.server_exe.write_bytes(b"fixture")
        self.model = self.directory / "model.joblib"
        self.model.write_bytes(b"not-a-real-model-do-not-load")
        self.final_report, self.diagnosis_report = self.directory / "final.json", self.directory / "diagnosis.json"
        write_json(self.final_report, {"status": "complete", "selected_model_id": "selected_rf",
                   "approval": {"status": "withheld_after_test"}, "models": {"selected_rf": {
                       "candidate_id": "rf_leaf_5", "threshold": 0.3, "files": {
                           "model": {"path": str(self.model), "sha256": file_hash(self.model)}}}}})
        write_json(self.diagnosis_report, {"status": "complete", "candidate_id": "rf_leaf_5", "threshold": 0.3,
                   "recovery_plan_sha256": file_hash(self.design_path),
                   "previous_test_evidence": {"sha256": file_hash(self.final_report)},
                   "protected_sha256": {str(self.model): file_hash(self.model)}})
        self.dataset = self.directory / "dataset"
        self.report = self.directory / "report"
        self.dataset_report = self.directory / "dataset_report"

    def prepare(self) -> Path:
        return recovery.prepare_recovery_batch(self.design_path, self.previous_plan, "rec_fixture", self.directory,
            self.server_exe, AI_ROOT / "configs" / "collection_scenarios.json", self.dataset, self.report,
            self.dataset_report, self.final_report, self.diagnosis_report)

    def test_reference_is_frozen_without_deserialization_prediction_or_fit(self) -> None:
        with patch.object(joblib, "load", side_effect=AssertionError("수집 전에 모델을 로드하면 안 됩니다.")):
            reference = recovery.freeze_recovery_reference(self.final_report, self.diagnosis_report, self.design_path)
        self.assertEqual(reference["threshold"], 0.3)
        self.assertTrue(reference["previous_test_exposed"])
        self.assertFalse(reference["model_loaded"])
        self.assertFalse(reference["prediction_performed"])

    def test_changed_design_or_model_fails_before_preparation(self) -> None:
        self.model.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "고정 파일"):
            self.prepare()
        self.assertFalse((self.directory / "rec_fixture").exists())
        self.model.write_bytes(b"not-a-real-model-do-not-load")
        changed = copy.deepcopy(self.design)
        changed["scope"] = "준비 이후 변경된 설계"
        write_json(self.design_path, changed)
        with self.assertRaisesRegex(ValueError, "고정 보정 계획"):
            self.prepare()

    def test_roles_and_all_capture_inputs_are_locked_before_collection(self) -> None:
        with patch.object(recovery, "run_campaign", side_effect=AssertionError("준비 단계 송신 금지")):
            root = self.prepare()
        registry, prepared = read_json(root / "assignment_manifest.json"), read_json(root / "prepared_manifest.json")
        self.assertEqual([entry["split"] for entry in registry["campaigns"]], ["train"] * 3 + ["validation"] + ["test"] * 2)
        self.assertEqual(len(prepared["capture_input_sha256"]), 762)
        self.assertEqual(prepared["assignment_sha256"], file_hash(root / "assignment_manifest.json"))
        self.assertFalse((root / "batch_runtime.json").exists())
        self.assertFalse(self.dataset.exists())
        for entry in registry["campaigns"]:
            campaign = read_json(Path(entry["campaign_dir"]) / "campaign_manifest.json")
            self.assertEqual(len(campaign["runs"]), 63)
            self.assertLessEqual(registry["locked_at_utc"], campaign["created_at_utc"])
        self.assertEqual(len(recovery.recovery_coverage(registry)), 6)

    def test_modified_run_configuration_rejected_before_network_start(self) -> None:
        root = self.prepare()
        prepared = read_json(root / "prepared_manifest.json")
        config_path = next(Path(path) for path in prepared["capture_input_sha256"] if Path(path).parent.name == "configs")
        config_path.write_text("{}", encoding="utf-8")
        with patch.object(recovery, "ensure_ports_free") as ports, patch.object(recovery, "run_campaign") as run:
            with self.assertRaisesRegex(ValueError, "고정 파일"):
                recovery.collect_recovery_batch(root, self.server_exe)
            ports.assert_not_called()
            run.assert_not_called()
        self.assertEqual(read_json(root / "batch_runtime.json")["status"], "failed")
        self.assertFalse(self.dataset.exists())

    def test_modified_server_binary_and_path_are_rejected(self) -> None:
        root = self.prepare()
        alternate = self.directory / "other.exe"
        alternate.write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError, "경로"):
            recovery.collect_recovery_batch(root, alternate)
        self.server_exe.write_bytes(b"different-build")
        with patch.object(recovery, "run_campaign") as run:
            with self.assertRaisesRegex(ValueError, "고정 파일"):
                recovery.collect_recovery_batch(root, self.server_exe)
            run.assert_not_called()

    def test_quality_failure_stops_batch_without_build_or_retry(self) -> None:
        root = self.prepare()
        with patch.object(recovery, "ensure_ports_free"), \
                patch.object(recovery, "run_campaign", return_value={"passed": False}) as run, \
                patch.object(recovery, "audit_campaign", return_value={"passed": False}), \
                patch.object(recovery, "save_quality_report"), patch.object(recovery, "build_dataset") as build:
            with self.assertRaisesRegex(RuntimeError, "실패 run"):
                recovery.collect_recovery_batch(root, self.server_exe)
            self.assertEqual(run.call_count, 1)
            build.assert_not_called()
        self.assertEqual(read_json(root / "batch_runtime.json")["status"], "failed")
        self.assertFalse((root / "completion_manifest.json").exists())
        with self.assertRaisesRegex(FileExistsError, "재송신"):
            recovery.collect_recovery_batch(root, self.server_exe)

    def test_keyboard_interrupt_is_recorded_and_not_auto_resumed(self) -> None:
        root = self.prepare()
        with patch.object(recovery, "ensure_ports_free"), \
                patch.object(recovery, "run_campaign", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                recovery.collect_recovery_batch(root, self.server_exe)
        runtime = read_json(root / "batch_runtime.json")
        self.assertEqual(runtime["status"], "failed")
        self.assertIn("KeyboardInterrupt", runtime["error"])

    def test_existing_dataset_or_report_fails_before_capture(self) -> None:
        self.dataset.mkdir()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.dataset.rmdir()
        self.report.with_suffix(".md").write_text("기존 결과", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.report.with_suffix(".md").read_text(encoding="utf-8"), "기존 결과")

    def test_report_collision_and_raw_nested_dataset_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            recovery.require_new_outputs(self.dataset, self.report, self.report)
        with self.assertRaises(ValueError):
            recovery.prepare_recovery_batch(self.design_path, self.previous_plan, "rec_fixture", self.directory,
                self.server_exe, AI_ROOT / "configs" / "collection_scenarios.json", self.directory / "rec_fixture" / "data",
                self.report, self.dataset_report, self.final_report, self.diagnosis_report)

    def test_split_message_counts_are_checked_against_precollection_expectation(self) -> None:
        plans = compile_recovery_plans(self.design, read_json(self.previous_plan))
        planned = summarize_recovery_design(self.design, plans)
        registry = {"planned_summary": planned}
        quality = {"passed": True, "runs": 378, "passed_runs": 378,
                   "totals": {"sessions": 1512, "summaries": 1512, "messages": 16120}}
        dataset = {"splits": {name: {"rows": values["expected_message_rows"], "campaigns": values["campaigns"]}
                              for name, values in planned["splits"].items()}}
        recovery.verify_collected_counts(registry, quality, dataset)
        dataset["splits"]["test"]["rows"] -= 1
        with self.assertRaisesRegex(ValueError, "test"):
            recovery.verify_collected_counts(registry, quality, dataset)

    def test_coverage_does_not_allow_pacing_batch_confounding(self) -> None:
        plans = compile_recovery_plans(self.design, read_json(self.previous_plan))
        entries = [{**slot, "campaign_id": f"c{index}", "plan_snapshot": plan.model_dump(mode="json")}
                   for index, (slot, plan) in enumerate(plans)]
        registry = {"campaigns": entries}
        self.assertEqual(len(recovery.recovery_coverage(registry)), 6)
        first = entries[0]["plan_snapshot"]
        for profile in first["repeat_profiles"][1]:
            if profile["scenario_id"] == "normal_batched_chat":
                profile["interval_min_ms"], profile["interval_max_ms"] = 1, 4
        with self.assertRaisesRegex(ValueError, "9조합"):
            recovery.recovery_coverage(registry)

    def test_partial_preparation_is_not_accepted_for_collection(self) -> None:
        root = self.directory / "partial"
        root.mkdir()
        write_json(root / "assignment_manifest.json", {"batch_kind": "recovery_v1"})
        with self.assertRaises(FileNotFoundError):
            recovery.collect_recovery_batch(root, self.server_exe)


if __name__ == "__main__":
    unittest.main()
