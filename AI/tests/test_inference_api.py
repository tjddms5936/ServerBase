from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from fastapi.testclient import TestClient

from inference_api.main import app
import test_feature_alignment as fixtures


class InferenceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def assert_rejected(self, payload: dict) -> None:
        # 비정상 JSON 수치도 원문으로 보내 API가 500 대신 422를 반환하는지 확인한다.
        with patch("inference_api.main.model_service.predict") as predict:
            response = self.client.post("/predict", content=json.dumps(payload), headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertTrue(response.json()["detail"])
            predict.assert_not_called()

    def test_health_advertises_contract_ids(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["feature_contract_ids"], {
            "message": "server_application_message_v2",
            "session_summary": "server_application_session_summary_v2",
        })

    def test_test_recording_route_is_not_in_normal_app(self) -> None:
        self.assertEqual(self.client.get("/__test__/records").status_code, 404)

    def test_valid_message_and_summary_keep_dummy_response(self) -> None:
        for payload in [fixtures.FeatureAlignmentTests.message_payload(), fixtures.FeatureAlignmentTests.summary_payload()]:
            with self.subTest(event=payload["event_type"]), redirect_stdout(StringIO()):
                response = self.client.post("/predict", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["request_id"], str(payload["queue_sequence_id"]))
                self.assertEqual(body["received_event_type"], payload["event_type"])
                self.assertEqual(body["label"], "normal")
                self.assertFalse(body["is_attack"])
                self.assertEqual(body["model_version"], "dummy-v0")

    def test_old_missing_or_wrong_contract_is_rejected(self) -> None:
        for contract_id in [None, "server_application_message_v1", "server_application_session_summary_v2"]:
            payload = fixtures.FeatureAlignmentTests.message_payload()
            if contract_id is None:
                payload.pop("feature_contract_id")
            else:
                payload["feature_contract_id"] = contract_id
            with self.subTest(contract=contract_id):
                self.assert_rejected(payload)

    def test_non_finite_negative_and_coerced_numbers_are_rejected(self) -> None:
        for field, value in [
            ("connection_duration_ms", float("nan")), ("connection_duration_ms", float("inf")),
            ("connection_duration_ms", -1), ("connection_duration_ms", "200"),
            ("packet_size", True), ("packet_size", 24.0), ("bytes_received_total", 2**64),
        ]:
            payload = fixtures.FeatureAlignmentTests.message_payload()
            payload["features"][field] = value
            with self.subTest(field=field, value=str(value)):
                self.assert_rejected(payload)

    def test_duplicate_values_and_frame_sizes_are_checked(self) -> None:
        payload = fixtures.FeatureAlignmentTests.message_payload()
        payload["features"]["bytes_received_total"] += 1
        self.assert_rejected(payload)
        payload = fixtures.FeatureAlignmentTests.message_payload()
        payload["features"]["packet_size"] += 1
        payload["message"]["packet_size"] += 1
        self.assert_rejected(payload)

    def test_extra_fields_are_not_silently_ignored(self) -> None:
        for section in [None, "features", "session", "message"]:
            payload = fixtures.FeatureAlignmentTests.message_payload()
            target = payload if section is None else payload[section]
            target["unexpected"] = 1
            with self.subTest(section=section):
                self.assert_rejected(payload)

    def test_first_interval_summary_flags_and_short_mean_are_checked(self) -> None:
        payload = fixtures.FeatureAlignmentTests.message_payload()
        payload["message"]["request_index"] = 1
        self.assert_rejected(payload)
        payload = fixtures.FeatureAlignmentTests.summary_payload()
        payload["session"]["is_closed"] = False
        self.assert_rejected(payload)
        payload = fixtures.FeatureAlignmentTests.summary_payload()
        payload["session"]["request_count"] = 0
        payload["features"]["request_count"] = 0
        self.assert_rejected(payload)

    def test_bad_timestamp_and_non_finite_metadata_are_rejected(self) -> None:
        for timestamp in ["2026-10-06T00:00:00", "not-a-time", "2026-10-06T09:00:00+09:00"]:
            payload = fixtures.FeatureAlignmentTests.message_payload()
            payload["timestamp"] = timestamp
            self.assert_rejected(payload)
        payload = fixtures.FeatureAlignmentTests.message_payload()
        payload["session"]["connection_duration_ms"] = float("inf")
        self.assert_rejected(payload)

    def test_malformed_json_returns_validation_error(self) -> None:
        response = self.client.post("/predict", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.json()["detail"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
