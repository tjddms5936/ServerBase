from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.augmentation import compile_plans, freeze_reference, prepare_batch, collect_batch, verify_hashes
from data_collection.campaign import file_hash, write_json, validate_capture_paths
from data_collection.quality import read_json, audit_campaign
from data_collection.traffic import CampaignPlan, TrafficProfile, build_expectations, planned_runs, run_session, planned_send_groups
from data_preparation.operational_dataset import DatasetConfig, build_dataset
import test_operational_dataset as fixtures


class AugmentationPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.design = read_json(AI_ROOT / "configs" / "operational_augmentation_plan.json")

    def test_six_plans_have_unique_seeds_and_fixed_roles(self) -> None:
        plans = compile_plans(self.design)
        self.assertEqual([slot["split"] for slot, _ in plans], ["train"] * 3 + ["validation"] + ["test"] * 2)
        seeds = [seed for _, plan in plans for _, _, seed in planned_runs(plan)]
        self.assertEqual(len(seeds), 84)
        self.assertEqual(len(set(seeds)), 84)
        self.assertEqual([plan.model_dump() for _, plan in plans], [plan.model_dump() for _, plan in compile_plans(self.design)])

    def test_crossed_controls_cover_designed_levels(self) -> None:
        plans = compile_plans(self.design)
        profiles = [profile for _, plan in plans for _, profile, _ in planned_runs(plan)]
        self.assertEqual({profile.concurrency for profile in profiles}, {1, 2, 4})
        for scenario in self.design["scenario_ids"]:
            selected = [profile for profile in profiles if profile.scenario_id == scenario]
            self.assertEqual({profile.concurrency for profile in selected}, {1, 2, 4})
        self.assertEqual({profile.batch_size for profile in profiles if profile.scenario_id == "normal_batched_chat"}, {1, 4, 8})
        self.assertEqual({profile.fragment_size_bytes for profile in profiles if profile.scenario_id == "normal_fragmented_chat"}, {7, 19, 31})
        self.assertTrue(all(profile.sessions == 4 and profile.messages_max <= 24 for profile in profiles))
        self.assertTrue(all(profile.interval_max_ms == 0 for profile in profiles if profile.scenario_id == "controlled_burst_chat"))

    def test_order_changes_do_not_change_run_seeds(self) -> None:
        _, plan = compile_plans(self.design)[0]
        changed = plan.model_dump()
        changed["profiles"].reverse()
        changed["repeat_profiles"][0].reverse()
        other = CampaignPlan.model_validate(changed)
        self.assertEqual({(repeat, profile.scenario_id): seed for repeat, profile, seed in planned_runs(plan)},
                         {(repeat, profile.scenario_id): seed for repeat, profile, seed in planned_runs(other)})

    def test_duplicate_or_unsafe_design_is_rejected(self) -> None:
        for key, value in (("max_text_bytes", 1024), ("external_traffic_allowed", True), ("destination", "192.0.2.1:4000")):
            design = copy.deepcopy(self.design)
            design["safety_limits"][key] = value
            with self.assertRaises(ValueError):
                compile_plans(design)
        design = copy.deepcopy(self.design)
        design["campaign_slots"][1]["seed"] = design["campaign_slots"][0]["seed"]
        with self.assertRaises(ValueError):
            compile_plans(design)

    def test_long_windows_paths_are_rejected_before_capture(self) -> None:
        _, plan = compile_plans(self.design)[0]
        with patch("data_collection.campaign.os.name", "nt"):
            with self.assertRaisesRegex(ValueError, "경로 길이"):
                validate_capture_paths(AI_ROOT / ("x" * 230), "fixture", plan)

    def test_v1_and_v2_definitions_cannot_be_mixed(self) -> None:
        profile = TrafficProfile(scenario_id="normal_chat", batch_size=4)
        with self.assertRaises(ValueError):
            CampaignPlan(plan_version="v1", seed=1, repeats=1, profiles=[profile])
        with self.assertRaises(ValueError):
            CampaignPlan(plan_version="v2", seed=1, repeats=1, profiles=[TrafficProfile(scenario_id="normal_chat")])

    def test_actual_sendall_uses_configured_batch_and_fragment_sizes(self) -> None:
        for scenario, batch, fragment in (("normal_batched_chat", 8, None), ("normal_batched_chat", 1, None),
                                           ("normal_fragmented_chat", 1, 7), ("normal_fragmented_chat", 1, 19)):
            with self.subTest(scenario=scenario, batch=batch, fragment=fragment):
                profile = TrafficProfile(scenario_id=scenario, sessions=1, concurrency=1, messages_min=8,
                                         messages_max=8, text_sizes=[24], batch_size=batch, fragment_size_bytes=fragment)
                expected = build_expectations(profile, 42, "test", 0)
                session = expected["sessions"][0]
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.recv.return_value = b""
                with patch("data_collection.traffic.connect_local", return_value=connection), \
                        patch("data_collection.traffic.read_reply"), patch("data_collection.traffic.time.sleep"):
                    result = run_session(session, scenario, profile)
                self.assertTrue(result["passed"], result["error"])
                sent = [call.args[0] for call in connection.sendall.call_args_list]
                self.assertEqual(len(sent), session["expected_sendall_calls"])
                self.assertEqual(result["sendall_calls"], len(sent))
                self.assertEqual(b"".join(sent), b"".join(bytes.fromhex(frame["wire_hex"]) for frame in session["frames"]))
                if fragment:
                    self.assertTrue(all(len(part) <= fragment for part in sent))
                keys = ("first_frame_index", "frame_count", "wire_size", "wait_after_ms")
                self.assertEqual([{key: group[key] for key in keys} for group in result["send_groups"]],
                                 planned_send_groups(session, session["transport_controls"]))

    def test_short_malformed_and_header_reject_have_distinct_coverage(self) -> None:
        for name, count in (("malformed_chat_payload", 4), ("malformed_length_chat", 4), ("invalid_frame_size", 0)):
            profile = TrafficProfile(scenario_id=name, batch_size=1)
            expected = build_expectations(profile, 7, "test", 1)
            self.assertEqual(expected["expected_messages"], count)
            self.assertEqual(expected["expected_summaries"], 4)
            self.assertEqual(expected["expectation_version"], "v2")

    def test_reference_is_hashed_without_loading_or_predicting(self) -> None:
        frozen = freeze_reference(AI_ROOT / self.design["reference_selection"].replace("../", "", 1))
        self.assertEqual(frozen["threshold"], 0.7)
        self.assertFalse(frozen["prediction_performed"])
        self.assertFalse(frozen["fit_performed"])
        verify_hashes(frozen["file_sha256"])

    def test_all_campaigns_are_prepared_after_role_lock_before_any_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "test.exe"
            executable.write_bytes(b"test")
            root = prepare_batch(AI_ROOT / "configs" / "operational_augmentation_plan.json", "fixture_batch",
                                 directory, executable, AI_ROOT / "configs" / "collection_scenarios.json")
            registry = read_json(root / "assignment_manifest.json")
            self.assertEqual(len(registry["campaigns"]), 6)
            for entry in registry["campaigns"]:
                campaign_root = Path(entry["campaign_dir"])
                manifest = read_json(campaign_root / "campaign_manifest.json")
                self.assertLessEqual(registry["locked_at_utc"], manifest["created_at_utc"])
                self.assertEqual(len(manifest["runs"]), 14)
                self.assertFalse((campaign_root / "runs").exists())
                self.assertFalse((campaign_root / "campaign_runtime.json").exists())
            executable.write_bytes(b"changed")
            with patch("data_collection.augmentation.run_campaign") as run:
                with self.assertRaisesRegex(ValueError, "고정 파일"):
                    collect_batch(root, executable, directory / "dataset", directory / "report", directory / "dataset_report")
                run.assert_not_called()
            self.assertEqual(read_json(root / "batch_runtime.json")["status"], "failed")


class CampaignHoldoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.roots = [fixtures.make_campaign(self.directory, f"campaign_{index}", repeats=2,
                                            offset_seconds=index * 100, seed=100 + index) for index in range(3)]
        self.registry_path = self.directory / "assignment_manifest.json"
        # 시간 순서와 무관하게 사전 역할이 우선함을 검증한다.
        self.registry = {"assignment_version": "v1", "prepared_before_collection": True,
                         "split_policy": "preassigned_campaign_holdout", "locked_at_utc": "2025-12-31T00:00:00+00:00",
                         "campaigns": [{"campaign_id": root.name, "slot_id": f"slot_{index}", "seed": 100 + index,
                                        "split": split, "campaign_dir": str(root),
                                        "plan_snapshot": read_json(root / "campaign_manifest.json")["plan_snapshot"]}
                                       for index, (root, split) in enumerate(zip(self.roots, ("test", "train", "validation")))]}
        write_json(self.registry_path, self.registry)

    def config(self) -> DatasetConfig:
        return DatasetConfig(config_version="v2", dataset_id="test_campaign_holdout", campaign_dirs=self.roots,
                             output_dir=self.directory / "dataset", split_policy="preassigned_campaign_holdout",
                             campaign_registry=self.registry_path)

    def test_campaign_all_repeats_are_kept_in_preassigned_split(self) -> None:
        report = build_dataset(self.config())
        self.assertEqual(report["total_message_rows"], 18)
        self.assertEqual([block["split"] for block in report["blocks"]], ["test", "train", "validation"])
        for split in ("train", "validation", "test"):
            self.assertEqual(report["splits"][split]["campaigns"], 1)
            self.assertEqual(report["splits"][split]["rows"], 6)
            self.assertEqual(report["splits"][split]["runs"], 4)
        self.assertTrue(all(value == 0 for pair in report["identity_overlaps"].values() for value in pair.values()))
        manifest = read_json(self.directory / "dataset" / "split_manifest.json")
        for campaign in self.registry["campaigns"]:
            runs = [run for run in manifest["runs"] if run["campaign_id"] == campaign["campaign_id"]]
            self.assertEqual({run["split"] for run in runs}, {campaign["split"]})
            self.assertEqual({run["repeat_index"] for run in runs}, {0, 1})

    def test_registry_seed_plan_and_timestamp_are_verified(self) -> None:
        for mutation in ("seed", "plan", "late", "missing", "duplicate"):
            with self.subTest(mutation=mutation):
                registry = copy.deepcopy(self.registry)
                if mutation == "seed":
                    registry["campaigns"][0]["seed"] = 999
                elif mutation == "plan":
                    registry["campaigns"][0]["plan_snapshot"]["seed"] = 999
                elif mutation == "late":
                    registry["locked_at_utc"] = "2099-01-01T00:00:00+00:00"
                elif mutation == "missing":
                    registry["campaigns"].pop()
                else:
                    registry["campaigns"][0]["campaign_id"] = registry["campaigns"][1]["campaign_id"]
                write_json(self.registry_path, registry)
                with self.assertRaises(ValueError):
                    build_dataset(self.config())
                self.assertFalse((self.directory / "dataset").exists())

    def test_failed_run_blocks_entire_campaign_dataset(self) -> None:
        campaign = read_json(self.roots[0] / "campaign_manifest.json")
        entry = campaign["runs"][0]
        (self.roots[0] / "runs" / entry["run_id"] / "collection_summary.json").unlink()
        with self.assertRaisesRegex(ValueError, "실패 캠페인"):
            build_dataset(self.config())

    def test_old_v1_fixture_remains_valid(self) -> None:
        self.assertTrue(audit_campaign(self.roots[0])["passed"])
        with self.assertRaises(ValueError):
            DatasetConfig(dataset_id="bad", campaign_dirs=self.roots, output_dir=self.directory / "out",
                          split_policy="preassigned_campaign_holdout", campaign_registry=self.registry_path)

    def test_existing_dataset_is_not_overwritten(self) -> None:
        build_dataset(self.config())
        with self.assertRaises(FileExistsError):
            build_dataset(self.config())

    def test_v2_quality_rejects_wrong_actual_transport_or_group_log(self) -> None:
        root = self.roots[0]
        path = root / "campaign_manifest.json"
        campaign = read_json(path)
        values = campaign["plan_snapshot"]
        values["plan_version"] = "v2"
        for profile in values["profiles"]:
            profile["batch_size"] = 1
        plan = CampaignPlan.model_validate(values)
        campaign["plan_snapshot"] = plan.model_dump(mode="json")
        for entry, (_, profile, seed) in zip(campaign["runs"], planned_runs(plan)):
            entry["seed"] = seed
            expected = build_expectations(profile, seed, entry["run_id"], entry["independent_label"])
            expectation_path = root / entry["expectations_file"]
            write_json(expectation_path, expected)
            entry["expectations_sha256"] = file_hash(expectation_path)
            outcome_path = root / "outcomes" / f"{entry['run_id']}_traffic.json"
            outcome = read_json(outcome_path)
            session = expected["sessions"][0]
            outcome["sessions"][0].update(transport_controls=session["transport_controls"],
                                          sendall_calls=session["expected_sendall_calls"],
                                          send_groups=[{**group, "start_interval_ms": 0, "send_duration_ms": 0}
                                                       for group in planned_send_groups(session, session["transport_controls"])])
            write_json(outcome_path, outcome)
        write_json(path, campaign)
        self.assertTrue(audit_campaign(root)["passed"])
        outcome_path = root / "outcomes" / f"{campaign['runs'][0]['run_id']}_traffic.json"
        original = read_json(outcome_path)
        for key in ("transport_controls", "sendall_calls", "send_groups"):
            outcome = copy.deepcopy(original)
            outcome["sessions"][0][key] = {} if key == "transport_controls" else -1 if key == "sendall_calls" else []
            write_json(outcome_path, outcome)
            self.assertFalse(audit_campaign(root)["passed"], key)

    def test_six_campaign_batch_stops_on_quality_failure_without_building_dataset(self) -> None:
        executable = self.directory / "fixture_server.exe"
        root = prepare_batch(AI_ROOT / "configs" / "operational_augmentation_plan.json", "failing_batch",
                             self.directory, executable, AI_ROOT / "configs" / "collection_scenarios.json")
        with patch("data_collection.augmentation.ensure_ports_free"), \
                patch("data_collection.augmentation.run_campaign", return_value={"passed": False}) as run, \
                patch("data_collection.augmentation.audit_campaign", return_value={"passed": False}) as audit, \
                patch("data_collection.augmentation.save_quality_report"), \
                patch("data_collection.augmentation.build_dataset") as build:
            with self.assertRaisesRegex(RuntimeError, "실패 run"):
                collect_batch(root, executable, self.directory / "out", self.directory / "report", self.directory / "dataset_report")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(audit.call_count, 1)
            build.assert_not_called()
        runtime = read_json(root / "batch_runtime.json")
        self.assertEqual(runtime["status"], "failed")
        self.assertEqual(len(runtime["executed_campaigns"]), 1)


if __name__ == "__main__":
    unittest.main()
