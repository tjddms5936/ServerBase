from __future__ import annotations

import json
import socket
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from pydantic import ValidationError

from data_collection.campaign import prepare_campaign, write_json
from data_collection.collector import AsyncJsonlCollector
from data_collection.config import load_collection_config
from data_collection.quality import audit_campaign, parse_json, read_json, save_quality_report
from data_collection.runtime import ensure_ports_free
from data_collection.traffic import (
    CampaignPlan, SCENARIO_LABELS, TrafficProfile, build_expectations, chat_frame, generate_traffic, read_reply,
)
import test_feature_alignment as fixtures


class TrafficGeneratorTests(unittest.TestCase):
    def test_chat_wire_format_matches_cpp_protocol(self) -> None:
        frame = chat_frame(b"hello", -42)
        self.assertEqual(struct.unpack("!ii", frame[:8]), (0, 19))
        self.assertEqual(struct.unpack("!H", frame[8:10])[0], 5)
        self.assertEqual(frame[10:15], b"hello")
        self.assertEqual(struct.unpack("!i", frame[15:])[0], -42)
        with self.assertRaises(ValueError):
            chat_frame(b"x" * 1025)

    def test_seed_is_reproducible_and_different_runs_vary(self) -> None:
        profile = TrafficProfile(scenario_id="normal_chat")
        first = build_expectations(profile, 42, "test", 0)
        self.assertEqual(first, build_expectations(profile, 42, "test", 0))
        self.assertNotEqual(first["sessions"], build_expectations(profile, 142, "test", 0)["sessions"])

    def test_all_scenarios_have_explicit_snapshot_coverage(self) -> None:
        for name, label in SCENARIO_LABELS.items():
            with self.subTest(name=name):
                options = dict(messages_min=12, messages_max=16, interval_min_ms=0, interval_max_ms=0) if name == "controlled_burst_chat" else {}
                expected = build_expectations(TrafficProfile(scenario_id=name, **options), 42, "test", label)
                self.assertEqual(expected["expected_summaries"], 4)
                if name == "invalid_frame_size":
                    self.assertEqual(expected["expected_messages"], 0)
                    self.assertFalse(expected["message_observable"])
                else:
                    self.assertGreater(expected["expected_messages"], 0)
                if name == "controlled_burst_chat":
                    self.assertTrue(all(session["expected_error_count"] == 0 for session in expected["sessions"]))
                    self.assertEqual(expected["independent_label"], 1)

    def test_malformed_length_can_have_identical_normal_frame_size(self) -> None:
        normal = build_expectations(TrafficProfile(scenario_id="normal_chat", messages_min=1, messages_max=1), 42, "test", 0)
        malformed = build_expectations(TrafficProfile(scenario_id="malformed_length_chat", messages_min=1, messages_max=1), 42, "test", 1)
        for good, bad in zip(normal["sessions"], malformed["sessions"]):
            self.assertEqual(good["frames"][0]["wire_size"], bad["frames"][0]["wire_size"])
            self.assertNotEqual(good["frames"][0]["wire_hex"], bad["frames"][0]["wire_hex"])

    def test_unsafe_workload_and_wrong_labels_are_rejected(self) -> None:
        for override in [{"sessions": 17}, {"concurrency": 5}, {"messages_max": 33}, {"text_sizes": [4096]}, {"interval_max_ms": 101}, {"messages_min": 8, "messages_max": 4}]:
            with self.subTest(override=override), self.assertRaises(ValidationError):
                TrafficProfile(scenario_id="normal_chat", **override)
        with self.assertRaises(ValueError):
            build_expectations(TrafficProfile(scenario_id="normal_chat"), 42, "test", 1)
        with self.assertRaises(ValidationError):
            TrafficProfile(scenario_id="controlled_burst_chat")
        with self.assertRaises(ValidationError):
            CampaignPlan(plan_version="v1", seed=42, repeats=1, profiles=[TrafficProfile(scenario_id="normal_chat")] * 2)

    def test_failed_client_is_not_silently_omitted(self) -> None:
        expected = build_expectations(TrafficProfile(scenario_id="normal_chat"), 42, "test", 0)
        with patch("data_collection.traffic.connect_local", side_effect=OSError("테스트 연결 실패")):
            outcome = generate_traffic(expected)
        self.assertFalse(outcome["passed"])
        self.assertEqual(len(outcome["sessions"]), 4)
        self.assertEqual(outcome["connected_sessions"], 0)
        self.assertTrue(all(session["error"] for session in outcome["sessions"]))

    def test_reply_parser_rejects_trailing_or_bad_content(self) -> None:
        valid = b"Hello my world"
        class ReplySocket:
            def __init__(self, data):
                self.data = data
            def recv(self, size):
                part, self.data = self.data[:size], self.data[size:]
                return part
        for text in [valid, valid + b"x"]:
            frame = struct.pack("!iiH", 1, 10 + len(text), len(text)) + text
            if text == valid:
                read_reply(ReplySocket(frame))
            else:
                with self.assertRaises(RuntimeError):
                    read_reply(ReplySocket(frame))

    def test_port_preflight_does_not_connect_or_stop_other_processes(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            used_port = probe.getsockname()[1]
            probe.listen()
            with patch("data_collection.runtime.socket.socket") as factory:
                factory.return_value.__enter__.return_value.bind.side_effect = OSError("사용 중")
                with self.assertRaises(RuntimeError):
                    ensure_ports_free()
            self.assertGreater(used_port, 0)


class OperationalQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="traffic_quality_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.server = self.directory / "fake_server.exe"
        self.server.write_bytes("테스트 빌드".encode("utf-8"))

    def capture_fixture(self, name: str = "normal_chat", purpose: str = "controlled_experiment") -> Path:
        profile = TrafficProfile(scenario_id=name, sessions=1, concurrency=1, messages_min=2, messages_max=2, text_sizes=[16])
        plan = CampaignPlan(plan_version="v1", seed=42, repeats=1, profiles=[profile])
        root = prepare_campaign(plan, "test_campaign", self.directory, self.server, AI_ROOT / "configs" / "collection_scenarios.json", purpose)
        entry = read_json(root / "campaign_manifest.json")["runs"][0]
        expected = read_json(root / entry["expectations_file"])
        collector = AsyncJsonlCollector(load_collection_config(root / entry["config_file"]))
        received = 0
        count = len(expected["sessions"][0]["expected_message_frames"])
        try:
            for index, frame in enumerate(expected["sessions"][0]["expected_message_frames"], 1):
                payload = fixtures.FeatureAlignmentTests.message_payload()
                received += frame["packet_size"]
                payload["queue_sequence_id"] = index
                payload["message"].update(packet_id=0, request_index=index, packet_size=frame["packet_size"], payload_size=frame["packet_size"] - 8, message_interval_ms=0 if index == 1 else 1.5)
                payload["session"].update(connection_duration_ms=10.0 * index, bytes_received_total=received, bytes_sent_total=24 * (index - 1))
                payload["features"].update(packet_size=frame["packet_size"], payload_size=frame["packet_size"] - 8, connection_duration_ms=10.0 * index, message_interval_ms=0 if index == 1 else 1.5, bytes_received_total=received, bytes_sent_total=24 * (index - 1))
                collector.try_collect(payload)
            session = expected["sessions"][0]
            payload = fixtures.FeatureAlignmentTests.summary_payload()
            fields = dict(connection_duration_ms=10.0 * (count + 1), bytes_received_total=sum(frame["wire_size"] for frame in session["frames"]), bytes_sent_total=24 * session["expected_responses"], recv_event_count=len(session["frames"]), send_event_count=session["expected_responses"], request_count=count, error_count=session["expected_error_count"], avg_message_interval_ms=1.5 if count > 1 else 0)
            payload["queue_sequence_id"] = count + 1
            payload["session"].update(fields, last_error_message=session["expected_last_error_message"])
            payload["features"].update(fields)
            collector.try_collect(payload)
        finally:
            collector.stop()
        write_json(root / "outcomes" / f"{entry['run_id']}_traffic.json", {
            "run_id": entry["run_id"], "scenario_id": name, "passed": True,
            "sessions": [{"client_index": 0, "passed": True, "connected": True,
                          "frames_sent": len(session["frames"]), "bytes_sent": sum(frame["wire_size"] for frame in session["frames"]),
                          "responses_received": session["expected_responses"]}],
        })
        write_json(root / "outcomes" / f"{entry['run_id']}_runtime.json", {
            "run_id": entry["run_id"], "passed": True,
            "server_shutdown": {"graceful": True}, "api_shutdown": {"graceful": True},
        })
        return root

    def record_path(self, root: Path) -> Path:
        return next((root / "runs").glob("*/features.jsonl"))

    def test_valid_capture_and_label_distribution(self) -> None:
        root = self.capture_fixture()
        report = audit_campaign(root)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["eligible_runs"], 1)
        self.assertEqual(report["validated_label_distribution"]["messages"], {"0": 2, "1": 0})
        save_quality_report(report, self.directory / "report")
        self.assertTrue((self.directory / "report.md").is_file())

    def test_missing_message_is_detected_even_if_totals_are_rewritten(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
        summary_path = path.parent / "collection_summary.json"
        summary = read_json(summary_path)
        for name in ("attempted", "enqueued", "written", "flushed"):
            summary[name] = 2
        write_json(summary_path, summary)
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["totals"]["missing_messages"], 1)
        self.assertEqual(report["eligible_runs"], 0)

    def test_duplicate_message_is_detected(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root)
        text = path.read_text(encoding="utf-8")
        path.write_text(text + text.splitlines()[0] + "\n", encoding="utf-8")
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["totals"]["duplicate_records"], 1)

    def test_missing_summary_and_partial_line_are_rejected(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:-1]), encoding="utf-8")
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["totals"]["missing_summaries"], 1)

    def test_wrong_label_source_or_manifest_is_rejected(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root).parent / "run_manifest.json"
        manifest = read_json(path)
        manifest["label_source"] = "dummy_prediction"
        manifest["selected_scenario"]["label"] = 1
        write_json(path, manifest)
        self.assertFalse(audit_campaign(root)["passed"])

    def test_expectation_tampering_is_rejected(self) -> None:
        root = self.capture_fixture()
        path = next((root / "expectations").glob("*.json"))
        expected = read_json(path)
        expected["expected_messages"] = 1
        write_json(path, expected)
        self.assertFalse(audit_campaign(root)["passed"])

    def test_cross_run_metadata_and_old_contract_are_rejected(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root)
        records = [parse_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[0]["run_id"] = "other_run"
        records[1]["payload"]["feature_contract_id"] = "server_application_message_v1"
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["totals"]["invalid_records"], 1)

    def test_decreasing_prefix_counter_is_rejected(self) -> None:
        root = self.capture_fixture()
        path = self.record_path(root)
        records = [parse_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[1]["payload"]["features"]["bytes_received_total"] = 0
        records[1]["payload"]["session"]["bytes_received_total"] = 0
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertTrue(any("감소" in issue for issue in report["runs"][0]["issues"]))

    def test_removed_run_entry_is_detected_from_plan(self) -> None:
        root = self.capture_fixture()
        path = root / "campaign_manifest.json"
        campaign = read_json(path)
        campaign["runs"] = []
        write_json(path, campaign)
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertTrue(report["campaign_issues"])

    def test_campaign_ids_are_included_in_run_ids(self) -> None:
        root = self.capture_fixture()
        campaign = read_json(root / "campaign_manifest.json")
        other = prepare_campaign(
            CampaignPlan.model_validate(campaign["plan_snapshot"]), "other_campaign", self.directory,
            self.server, AI_ROOT / "configs" / "collection_scenarios.json",
        )
        other_campaign = read_json(other / "campaign_manifest.json")
        self.assertNotEqual(campaign["runs"][0]["run_id"], other_campaign["runs"][0]["run_id"])
        self.assertTrue(campaign["runs"][0]["run_id"].startswith("test_campaign_"))

    def test_header_rejection_is_not_counted_as_missing_message(self) -> None:
        root = self.capture_fixture("invalid_frame_size")
        report = audit_campaign(root)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["totals"]["missing_messages"], 0)
        self.assertEqual(report["validated_label_distribution"]["session_summaries"]["1"], 1)
        self.assertFalse(report["runs"][0]["message_observable"])
        self.assertEqual(report["eligible_runs"], 0)

    def test_integration_test_run_is_not_a_training_candidate(self) -> None:
        report = audit_campaign(self.capture_fixture(purpose="integration_test"))
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["eligible_runs"], 0)

    def test_missing_run_artifact_is_quarantined(self) -> None:
        root = self.capture_fixture()
        (self.record_path(root).parent / "collection_summary.json").unlink()
        report = audit_campaign(root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["eligible_runs"], 0)
        self.assertTrue(report["runs"][0]["issues"])

    def test_duplicate_json_keys_and_non_finite_values_are_rejected(self) -> None:
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_json(text)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
