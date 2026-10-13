from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.campaign import prepare_campaign, write_json
from data_collection.quality import read_json
from data_collection.traffic import CampaignPlan, TrafficProfile
from data_preparation.operational_dataset import (
    DatasetConfig, FEATURE_NAMES, build_dataset, fingerprint, load_config,
    load_runs, save_report, split_runs, validate_partitions,
)
import test_feature_alignment as fixtures
from evaluation.audit_feature_alignment import assess_model_schema
from inference_api.payload_validation import OPERATIONAL_CONTRACT


def make_campaign(directory: Path, campaign_id: str = "fixture", repeats: int = 3,
                  purpose: str = "controlled_experiment", offset_seconds: int = 0, seed: int = 42) -> Path:
    # 실제 서버를 실행하지 않는 단위 테스트 원본이다. 임시 폴더 밖에는 저장하지 않는다.
    executable = directory / "fixture_server.exe"
    executable.write_bytes(b"unit-test-only")
    profiles = [TrafficProfile(scenario_id=name, sessions=1, concurrency=1,
                               messages_min=2, messages_max=2, text_sizes=[16])
                for name in ("normal_chat", "malformed_length_chat", "invalid_frame_size")]
    plan = CampaignPlan(plan_version="v1", seed=seed, repeats=repeats, profiles=profiles)
    root = prepare_campaign(plan, campaign_id, directory, executable,
                            AI_ROOT / "configs" / "collection_scenarios.json", purpose)
    campaign = read_json(root / "campaign_manifest.json")
    scenarios = {item["scenario_id"]: item for item in campaign["scenario_manifest_snapshot"]["scenarios"]}
    epoch = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    for order, entry in enumerate(campaign["runs"]):
        start = epoch + timedelta(seconds=order * 10)
        expected = read_json(root / entry["expectations_file"])
        session = expected["sessions"][0]
        run_dir = root / "runs" / entry["run_id"]
        run_dir.mkdir(parents=True)
        records, received = [], 0
        for index, frame in enumerate(session["expected_message_frames"], 1):
            payload = fixtures.FeatureAlignmentTests.message_payload()
            received += frame["packet_size"]
            payload.update(queue_sequence_id=index, timestamp=(start + timedelta(milliseconds=index * 20)).isoformat())
            payload["message"].update(packet_id=0, request_index=index, packet_size=frame["packet_size"],
                                      payload_size=frame["packet_size"] - 8, message_interval_ms=0 if index == 1 else 1.5)
            prefix = dict(connection_duration_ms=index * 10.0, bytes_received_total=received,
                          bytes_sent_total=(index - 1) * 24, error_count_total=0)
            payload["session"].update(prefix)
            payload["features"].update(prefix, packet_size=frame["packet_size"], payload_size=frame["packet_size"] - 8,
                                       message_interval_ms=0 if index == 1 else 1.5)
            records.append(payload)
        count = len(session["expected_message_frames"])
        summary_payload = fixtures.FeatureAlignmentTests.summary_payload()
        fields = dict(connection_duration_ms=(count + 1) * 10.0,
                      bytes_received_total=sum(frame["wire_size"] for frame in session["frames"]),
                      bytes_sent_total=session["expected_responses"] * 24, recv_event_count=len(session["frames"]),
                      send_event_count=session["expected_responses"], request_count=count,
                      error_count=session["expected_error_count"], avg_message_interval_ms=1.5 if count > 1 else 0)
        summary_payload.update(queue_sequence_id=count + 1, timestamp=(start + timedelta(seconds=1)).isoformat())
        summary_payload["session"].update(fields, last_error_message=session["expected_last_error_message"])
        summary_payload["features"].update(fields)
        records.append(summary_payload)
        with (run_dir / "features.jsonl").open("w", encoding="utf-8") as stream:
            for sequence, payload in enumerate(records, 1):
                stream.write(json.dumps({
                    "record_schema_version": "v1", "collection_sequence_id": sequence,
                    "received_at_utc": payload["timestamp"], "run_id": entry["run_id"],
                    "scenario_id": entry["scenario_id"], "server_build_id": campaign["server_build_id"],
                    "session_id": 1, "request_index": payload.get("message", {}).get("request_index"),
                    "label_reference": "run_manifest.json:selected_scenario", "payload": payload,
                }) + "\n")
        write_json(run_dir / "run_manifest.json", {
            "created_at_utc": start.isoformat(), "run_id": entry["run_id"], "scenario_id": entry["scenario_id"],
            "dataset_purpose": purpose, "label_source": "independent_scenario_definition",
            "selected_scenario": scenarios[entry["scenario_id"]],
            **{name: campaign[name] for name in ("source_manifest_sha256", "scenario_manifest_snapshot",
                                                "server_build_id", "operational_contract_sha256", "operational_contract_snapshot")},
        })
        stats = {name: 0 for name in ("dropped_full", "dropped_closed", "dropped_failed", "failed_records",
                                     "pending", "in_flight", "unfinished")}
        stats.update({name: len(records) for name in ("attempted", "enqueued", "written", "flushed")})
        write_json(run_dir / "collection_summary.json", {
            **stats, "run_id": entry["run_id"], "scenario_id": entry["scenario_id"], "complete": True,
            "shutdown_complete": True, "shutdown_timed_out": False, "write_error": None, "summary_error": None,
            "finished_at_utc": (start + timedelta(seconds=9)).isoformat(),
        })
        write_json(root / "outcomes" / f"{entry['run_id']}_traffic.json", {
            "run_id": entry["run_id"], "scenario_id": entry["scenario_id"], "passed": True,
            "sessions": [{"client_index": 0, "passed": True, "connected": True,
                          "frames_sent": len(session["frames"]), "bytes_sent": fields["bytes_received_total"],
                          "responses_received": session["expected_responses"]}],
        })
        write_json(root / "outcomes" / f"{entry['run_id']}_runtime.json", {
            "run_id": entry["run_id"], "passed": True, "server_shutdown": {"graceful": True}, "api_shutdown": {"graceful": True},
        })
    return root


class OperationalDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational_dataset_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = make_campaign(self.directory)

    def config(self, **overrides) -> DatasetConfig:
        values = dict(dataset_id="fixture_v1", campaign_dirs=[self.root], output_dir=self.directory / "dataset")
        values.update(overrides)
        return DatasetConfig(**values)

    def read_csv(self, path: Path) -> tuple[list[str], list[dict]]:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            return reader.fieldnames, list(reader)

    def test_build_exports_only_message_features_with_aligned_targets(self) -> None:
        report = build_dataset(self.config())
        self.assertEqual(report["total_message_rows"], 9)
        self.assertEqual(report["selected_runs"], 6)
        self.assertEqual(len(report["excluded_sources"]), 3)
        self.assertFalse(report["preprocessing_fitted"])
        self.assertFalse(report["model_training_performed"])
        for split in ("train", "validation", "test"):
            columns, x = self.read_csv(self.directory / "dataset" / f"X_{split}.csv")
            y_columns, y = self.read_csv(self.directory / "dataset" / f"y_{split}.csv")
            _, metadata = self.read_csv(self.directory / "dataset" / f"metadata_{split}.csv")
            self.assertEqual(columns, FEATURE_NAMES)
            self.assertEqual(y_columns, ["label"])
            self.assertEqual(len(x), len(y))
            self.assertEqual(len(x), len(metadata))
            self.assertEqual(report["splits"][split]["rows"], 3)
            self.assertEqual({item["split"] for item in metadata}, {split})
        self.assertTrue((self.directory / "dataset" / "dataset_manifest.json").is_file())
        save_report(report, self.directory / "report")
        self.assertTrue((self.directory / "report.md").is_file())

    def test_chronological_blocks_and_composite_session_keys_do_not_overlap(self) -> None:
        report = build_dataset(self.config())
        self.assertEqual([block["split"] for block in report["blocks"]], ["train", "validation", "test"])
        for pair in report["identity_overlaps"].values():
            self.assertEqual(pair, {"runs": 0, "sessions": 0, "rows": 0})
        metadata = self.read_csv(self.directory / "dataset" / "metadata_train.csv")[1]
        self.assertEqual({row["session_id"] for row in metadata}, {"1"})
        self.assertEqual({row["repeat_index"] for row in metadata}, {"0"})

    def test_three_blocks_are_required(self) -> None:
        other = make_campaign(self.directory, "two_blocks", repeats=2)
        with self.assertRaisesRegex(ValueError, "반복 묶음이 부족"):
            build_dataset(self.config(campaign_dirs=[other]))
        self.assertFalse((self.directory / "dataset").exists())

    def test_duplicate_features_are_reported_without_changing_labels(self) -> None:
        report = build_dataset(self.config())
        self.assertGreater(report["duplicate_features"]["cross_split_feature_groups"], 0)
        self.assertGreater(report["duplicate_features"]["conflicting_label_feature_groups"], 0)
        self.assertEqual(report["splits"]["train"]["label_distribution"], {"0": 2, "1": 1})

    def test_strict_duplicate_policy_aborts_before_export(self) -> None:
        with self.assertRaisesRegex(ValueError, "같은 feature"):
            build_dataset(self.config(duplicate_feature_policy="error"))
        self.assertFalse((self.directory / "dataset").exists())

    def test_integration_test_campaign_is_excluded(self) -> None:
        extra = make_campaign(self.directory, "integration", purpose="integration_test")
        report = build_dataset(self.config(campaign_dirs=[extra, self.root]))
        self.assertEqual(report["total_message_rows"], 9)
        self.assertTrue(any(item["campaign_id"] == "integration" for item in report["excluded_sources"]))

    def test_duplicate_source_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "경로를 중복"):
            build_dataset(self.config(campaign_dirs=[self.root, self.root]))

    def test_identity_overlap_is_rejected(self) -> None:
        runs, _, _, _ = load_runs(self.config())
        assigned, _ = split_runs(runs, self.config())
        assigned["validation"].append(copy.deepcopy(assigned["train"][0]))
        with self.assertRaisesRegex(ValueError, "같은 run/session"):
            validate_partitions(assigned)

    def test_missing_anomaly_run_is_not_hidden_by_row_shuffling(self) -> None:
        entry = next(item for item in read_json(self.root / "campaign_manifest.json")["runs"]
                     if item["repeat_index"] == 1 and item["scenario_id"] == "malformed_length_chat")
        (self.root / "runs" / entry["run_id"] / "collection_summary.json").unlink()
        with self.assertRaisesRegex(ValueError, "정답이 모두 필요"):
            build_dataset(self.config())
        self.assertFalse((self.directory / "dataset").exists())

    def test_overlapping_capture_blocks_are_rejected(self) -> None:
        entry = next(item for item in read_json(self.root / "campaign_manifest.json")["runs"] if item["repeat_index"] == 0)
        path = self.root / "runs" / entry["run_id"] / "collection_summary.json"
        summary = read_json(path)
        summary["finished_at_utc"] = "2026-01-01T00:10:00+00:00"
        write_json(path, summary)
        with self.assertRaisesRegex(ValueError, "시간 범위가 겹칩니다"):
            build_dataset(self.config())

    def test_later_campaign_is_held_out_without_mixing_its_runs(self) -> None:
        later = make_campaign(self.directory, "later", offset_seconds=1000)
        report = build_dataset(self.config(campaign_dirs=[later, self.root]))
        self.assertEqual(report["splits"]["train"]["runs"], 8)
        self.assertTrue(all(block["block_id"].startswith("later:") for block in report["blocks"] if block["split"] in {"validation", "test"}))

    def test_existing_dataset_and_raw_subdirectory_are_not_overwritten(self) -> None:
        build_dataset(self.config())
        with self.assertRaises(FileExistsError):
            build_dataset(self.config())
        with self.assertRaisesRegex(ValueError, "원본"):
            build_dataset(self.config(output_dir=self.root / "dataset"))

    def test_failed_export_has_no_complete_manifest(self) -> None:
        with patch("data_preparation.operational_dataset.write_csv", side_effect=OSError("테스트 저장 실패")):
            with self.assertRaises(OSError):
                build_dataset(self.config())
        self.assertFalse((self.directory / "dataset" / "dataset_manifest.json").exists())

    def test_source_change_between_validation_and_export_is_detected(self) -> None:
        original = split_runs
        path = self.root / "campaign_manifest.json"
        def mutate_source(runs, config):
            assigned = original(runs, config)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return assigned
        with patch("data_preparation.operational_dataset.split_runs", side_effect=mutate_source):
            with self.assertRaisesRegex(ValueError, "원본 파일이 변경"):
                build_dataset(self.config())
        self.assertFalse((self.directory / "dataset").exists())

    def test_feature_order_mismatch_is_rejected(self) -> None:
        with patch("data_preparation.operational_dataset.MESSAGE_FEATURE_ORDER", list(reversed(FEATURE_NAMES))):
            with self.assertRaisesRegex(ValueError, "입력 순서"):
                build_dataset(self.config())

    def test_fingerprint_preserves_large_integer_counts(self) -> None:
        first = dict(packet_size=30, payload_size=22, connection_duration_ms=1.0, message_interval_ms=0.0,
                     bytes_received_total=2**53, bytes_sent_total=0, error_count_total=0)
        second = {**first, "bytes_received_total": 2**53 + 1}
        self.assertNotEqual(fingerprint(first), fingerprint(second))

    def test_config_paths_resolve_from_config_location(self) -> None:
        path = self.directory / "config.json"
        values = self.config().model_dump(mode="json")
        values["campaign_dirs"] = ["fixture"]
        values["output_dir"] = "dataset"
        write_json(path, values)
        config = load_config(path)
        self.assertEqual(config.campaign_dirs, [self.root])
        self.assertEqual(config.output_dir, self.directory / "dataset")

    def test_exported_schema_matches_runtime_contract_without_fitted_scaler(self) -> None:
        build_dataset(self.config())
        schema = read_json(self.directory / "dataset" / "feature_schema.json")
        checked = assess_model_schema(OPERATIONAL_CONTRACT, "message", schema)
        self.assertTrue(checked["schema_compatible"], checked)
        self.assertEqual(schema["preprocessing"]["status"], "not_fitted")

    def test_duplicate_campaign_ids_and_unknown_purpose_are_rejected(self) -> None:
        other = make_campaign(self.directory, "other", offset_seconds=1000)
        path = other / "campaign_manifest.json"
        manifest = read_json(path)
        manifest["campaign_id"] = "fixture"
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "campaign_id가 중복"):
            build_dataset(self.config(campaign_dirs=[self.root, other]))
        manifest["campaign_id"] = "other"
        manifest["dataset_purpose"] = "unknown"
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "수집 목적"):
            build_dataset(self.config(campaign_dirs=[other]))

    def test_non_utc_block_boundary_is_rejected(self) -> None:
        entry = read_json(self.root / "campaign_manifest.json")["runs"][0]
        path = self.root / "runs" / entry["run_id"] / "run_manifest.json"
        manifest = read_json(path)
        manifest["created_at_utc"] = "2026-01-01T00:00:00"
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "UTC"):
            build_dataset(self.config())


if __name__ == "__main__":
    raise SystemExit(unittest.main())
