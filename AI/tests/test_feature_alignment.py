from __future__ import annotations

import copy
from contextlib import redirect_stdout
from io import StringIO
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

from pydantic import TypeAdapter

from evaluation import audit_feature_alignment as audit
from evaluation.audit_feature_alignment import (
    CONTRACT_FILE, assess_model_schema, read_descriptions, read_training_header,
    validate_feature_values, validate_payload, verify_contract,
)
from inference_api.feature_transformer import build_feature_vector
from inference_api.schemas import InferenceRequest


class FeatureAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))

    @staticmethod
    def message_payload() -> dict:
        features = {
            "packet_size": 24, "payload_size": 16, "connection_duration_ms": 200.0,
            "message_interval_ms": 15.0, "bytes_received_total": 80,
            "bytes_sent_total": 40, "error_count_total": 0,
        }
        return {
            "schema_version": "v1", "event_type": "message", "queue_sequence_id": 10,
            "feature_contract_id": "server_application_message_v2",
            "timestamp": "2026-10-06T00:00:00.000Z",
            "session": {
                "session_id": 1, "socket_handle": 100, "logic_worker_id": 0,
                **{name: features[name] for name in ["connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total"]},
            },
            "message": {
                "request_index": 3, "packet_id": 1,
                **{name: features[name] for name in ["packet_size", "payload_size", "message_interval_ms"]},
            },
            "features": features,
        }

    @staticmethod
    def summary_payload() -> dict:
        features = {
            "connection_duration_ms": 1000.0, "bytes_received_total": 80,
            "bytes_sent_total": 40, "recv_event_count": 2, "send_event_count": 1,
            "request_count": 3, "error_count": 1, "avg_message_interval_ms": 20.0,
        }
        return {
            "schema_version": "v1", "event_type": "session_summary", "queue_sequence_id": 11,
            "feature_contract_id": "server_application_session_summary_v2",
            "timestamp": "2026-10-06T00:00:01.000Z",
            "session": {
                "session_id": 1, "socket_handle": 100, "logic_worker_id": 0,
                "is_started": True, "is_closed": True,
                "last_error_code": 0, "last_error_message": "invalid packet size", **features,
            },
            "features": features,
        }

    def operational_model_schema(self, event_type: str = "message") -> dict:
        event = self.contract["events"][event_type]
        return {
            "raw_input_features": event["feature_order"].copy(),
            "feature_contract_id": event["feature_contract_id"],
            "wire_schema_version": self.contract["wire_schema_version"],
            "capture_layer": self.contract["capture_layer"],
            "observation_scope": event["observation_scope"], "event_type": event_type,
            "feature_units": {name: self.contract["features"][name]["unit"] for name in event["feature_order"]},
        }

    def test_contract_matches_api_and_classifies_42_inputs(self) -> None:
        raw_names = [name for group in self.contract["unsw_nb15_alignment_groups"] for name in group["features"]]
        alignment = verify_contract(self.contract, raw_names)
        self.assertEqual(len(alignment), 42)
        self.assertEqual(len(self.contract["events"]["message"]["feature_order"]), 7)
        self.assertEqual(len(self.contract["events"]["session_summary"]["feature_order"]), 8)

    def test_contract_rejects_api_order_drift_and_incomplete_mapping(self) -> None:
        raw_names = [name for group in self.contract["unsw_nb15_alignment_groups"] for name in group["features"]]
        changed = copy.deepcopy(self.contract)
        changed["events"]["message"]["feature_order"].reverse()
        with self.assertRaisesRegex(ValueError, "순서"):
            verify_contract(changed, raw_names)
        with self.assertRaisesRegex(ValueError, "모든 입력"):
            verify_contract(self.contract, raw_names + ["unexpected_feature"])

    def test_vectors_match_existing_api_transformer(self) -> None:
        for payload in [self.message_payload(), self.summary_payload()]:
            with self.subTest(event=payload["event_type"]):
                actual = validate_payload(self.contract, payload)
                vector = build_feature_vector(TypeAdapter(InferenceRequest).validate_python(payload))
                self.assertEqual(actual, vector.values)
                self.assertEqual(vector.names, self.contract["events"][payload["event_type"]]["feature_order"])

    def test_missing_and_extra_features_are_rejected_without_imputation(self) -> None:
        for change in ["missing", "extra"]:
            values = self.message_payload()["features"]
            if change == "missing":
                values.pop("error_count_total")
            else:
                values["session_id"] = 1
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "누락 또는 추가"):
                validate_feature_values(self.contract, "message", values)

    def test_invalid_numeric_values_are_rejected(self) -> None:
        for bad in [None, True, "100", -1, float("inf"), float("nan"), 10 ** 400]:
            values = self.message_payload()["features"]
            values["connection_duration_ms"] = bad
            with self.subTest(value=str(bad)), self.assertRaises(ValueError):
                validate_feature_values(self.contract, "message", values)
        values = self.message_payload()["features"]
        values["packet_size"] = 24.0
        with self.assertRaisesRegex(ValueError, "정수"):
            validate_feature_values(self.contract, "message", values)

    def test_duplicate_fields_and_frame_sizes_must_match(self) -> None:
        payload = self.message_payload()
        payload["message"]["packet_size"] += 1
        with self.assertRaisesRegex(ValueError, "중복 값"):
            validate_payload(self.contract, payload)
        payload = self.message_payload()
        payload["message"]["packet_size"] += 1
        payload["features"]["packet_size"] += 1
        with self.assertRaisesRegex(ValueError, "헤더"):
            validate_payload(self.contract, payload)

    def test_first_message_zero_interval_is_not_missing(self) -> None:
        payload = self.message_payload()
        payload["message"]["request_index"] = 1
        with self.assertRaisesRegex(ValueError, "첫 메시지"):
            validate_payload(self.contract, payload)
        payload["message"]["message_interval_ms"] = 0
        payload["features"]["message_interval_ms"] = 0
        self.assertEqual(validate_payload(self.contract, payload)[3], 0.0)

    def test_summary_is_close_time_only_and_short_session_mean_is_zero(self) -> None:
        payload = self.summary_payload()
        payload["session"]["is_closed"] = False
        with self.assertRaisesRegex(ValueError, "시작 및 종료"):
            validate_payload(self.contract, payload)
        payload = self.summary_payload()
        payload["session"]["request_count"] = 1
        payload["features"]["request_count"] = 1
        with self.assertRaisesRegex(ValueError, "평균 간격"):
            validate_payload(self.contract, payload)
        payload["session"]["avg_message_interval_ms"] = 0
        payload["features"]["avg_message_interval_ms"] = 0
        validate_payload(self.contract, payload)

    def test_timestamp_requires_utc(self) -> None:
        for timestamp in ["2026-10-06T00:00:00", "2026-10-06T09:00:00+09:00"]:
            payload = self.message_payload()
            payload["timestamp"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "UTC"):
                validate_payload(self.contract, payload)

    def test_same_feature_names_without_semantics_are_not_compatible(self) -> None:
        schema = {"raw_input_features": self.contract["events"]["message"]["feature_order"]}
        result = assess_model_schema(self.contract, "message", schema)
        self.assertFalse(result["schema_compatible"])
        self.assertTrue(any("feature_contract_id" in reason for reason in result["reasons"]))
        self.assertFalse(assess_model_schema(self.contract, "message", None)["schema_compatible"])

    def test_model_schema_requires_matching_order_units_scope_and_event(self) -> None:
        self.assertTrue(assess_model_schema(self.contract, "message", self.operational_model_schema())["schema_compatible"])
        self.assertTrue(assess_model_schema(self.contract, "session_summary", self.operational_model_schema("session_summary"))["schema_compatible"])
        for change in ["order", "units", "scope", "event"]:
            schema = self.operational_model_schema()
            if change == "order":
                schema["raw_input_features"].reverse()
            elif change == "units":
                schema["feature_units"]["connection_duration_ms"] = "second"
            elif change == "scope":
                schema["observation_scope"] = "full_flow"
            else:
                schema["event_type"] = "session_summary"
            with self.subTest(change=change):
                self.assertFalse(assess_model_schema(self.contract, "message", schema)["schema_compatible"])

    def test_description_aliases_and_legacy_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.csv"
            path.write_bytes("Name,Description\nsmeansz,Source’s mean size\nSintpkt,Interval\nct_src_ ltm,Count\nres_bdy_len,Body\n".encode("cp1252"))
            descriptions, encoding = read_descriptions(path)
            self.assertEqual(encoding, "cp1252")
            self.assertEqual(set(descriptions), {"smean", "sinpkt", "ct_src_ltm", "response_body_len"})

    def test_only_training_header_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.csv"
            path.write_text("id,dur,proto,attack_cat,label\ninvalid,row,that,is,not,parsed\n", encoding="utf-8")
            self.assertEqual(read_training_header(path), ["dur", "proto"])

    def test_compatibility_gate_targets_only_requested_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            names = [name for group in self.contract["unsw_nb15_alignment_groups"] for name in group["features"]]
            training.write_text(",".join(["id", *names, "attack_cat", "label"]) + "\n", encoding="utf-8")
            model_schema = root / "model_schema.json"
            for event_type in self.contract["events"]:
                model_schema.write_text(json.dumps(self.operational_model_schema(event_type)), encoding="utf-8")
                other_event = "session_summary" if event_type == "message" else "message"
                for target, expected_code in [(event_type, 0), (other_event, 2)]:
                    arguments = ["audit", "--model-schema", str(model_schema), "--event-type", target, "--require-compatible"]
                    with (
                        self.subTest(model_event=event_type, target_event=target),
                        patch.multiple(audit, TRAIN_FILE=training, REPORT_FILE=root / "report.json", MARKDOWN_FILE=root / "report.md"),
                        patch.object(sys, "argv", arguments), redirect_stdout(StringIO()),
                    ):
                        self.assertEqual(audit.main(), expected_code)
                        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
                        self.assertEqual(report["compatibility_gate_event_type"], target)
                        self.assertTrue(report["model_schema_comparison"][event_type]["schema_compatible"])
                        self.assertFalse(report["model_schema_comparison"][other_event]["schema_compatible"])


if __name__ == "__main__":
    unittest.main()
