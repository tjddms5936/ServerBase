from __future__ import annotations

import json
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from evaluation.verify_rf_tcp_runtime import frame, percentile, read_reply, write_json
from inference_api.rf_main import create_rf_app
from rf_runtime_api import create_test_app


def fake_app():
    # 장애 주입기만 단위 검사하며 실제 모델 학습·승인 파일·데이터셋을 사용하지 않는다.
    app = FastAPI()

    @app.post("/predict")
    async def predict(request: Request):
        payload = await request.json()
        return {"request_id": str(payload["queue_sequence_id"]), "model_version": "approved",
                "decision_threshold": 0.7, "is_attack": False}

    return app


class RuntimeHelpersTests(unittest.TestCase):
    def test_generator_frame(self):
        message = frame(b"abc")
        self.assertEqual(struct.unpack("!ii", message[:8]), (0, 17))
        self.assertEqual(struct.unpack("!H", message[8:10])[0], 3)
        self.assertEqual(message[10:13], b"abc")

    def test_valid_reply_and_truncated_reply(self):
        left, right = socket.socketpair()
        with left, right:
            body = struct.pack("!H", 14) + b"Hello my world" + struct.pack("!i", 42)
            right.sendall(struct.pack("!ii", 1, len(body) + 8) + body)
            read_reply(left)
        left, right = socket.socketpair()
        with left, right:
            right.sendall(b"x")
            right.shutdown(socket.SHUT_WR)
            with self.assertRaises(RuntimeError):
                read_reply(left)

    def test_new_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            write_json(path, {"한글": True})
            with self.assertRaises(FileExistsError):
                write_json(path, {})
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["한글"])

    def test_latency_statistics(self):
        self.assertEqual(percentile([1, 3, 5])["median_ms"], 3)
        with self.assertRaises(RuntimeError):
            percentile([])

    def test_production_has_no_fault_control(self):
        self.assertFalse(any("__test__" in route.path for route in create_rf_app().routes))


class TestOnlyFaultApiTests(unittest.TestCase):
    def client(self):
        with patch("rf_runtime_api.create_rf_app", side_effect=fake_app):
            return TestClient(create_test_app())

    def test_normal_and_record(self):
        with self.client() as client:
            response = client.post("/predict", json={"queue_sequence_id": 7})
            self.assertEqual(response.json()["request_id"], "7")
            status = client.get("/__test__/records").json()
            self.assertEqual(status["total"], 1)
            self.assertEqual(status["active"], 0)

    def test_response_fault_modes(self):
        with self.client() as client:
            for mode in ("busy", "bad_json", "oversized", "wrong_id", "wrong_model", "wrong_policy"):
                with self.subTest(mode=mode):
                    client.post("/__test__/control", json={"mode": mode})
                    response = client.post("/predict", json={"queue_sequence_id": 7})
                    if mode == "busy":
                        self.assertEqual(response.status_code, 503)
                    elif mode == "bad_json":
                        with self.assertRaises(ValueError):
                            response.json()
                    elif mode == "oversized":
                        self.assertEqual(len(response.content), 70000)
                    else:
                        key, value = {"wrong_id": ("request_id", "not-the-request"),
                                      "wrong_model": ("model_version", "unapproved"),
                                      "wrong_policy": ("decision_threshold", 0.5)}[mode]
                        self.assertEqual(response.json()[key], value)

    def test_invalid_control(self):
        with self.client() as client:
            self.assertEqual(client.post("/__test__/control", json={"mode": "unknown"}).status_code, 400)
            self.assertEqual(client.post("/__test__/control", json={"mode": "delay", "delay_ms": 3001}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
