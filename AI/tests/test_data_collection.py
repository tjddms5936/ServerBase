from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from fastapi.testclient import TestClient
from pydantic import ValidationError

from data_collection.collector import AsyncJsonlCollector
from data_collection.config import CollectionConfig, load_collection_config, load_manifest
from inference_api.main import app
import test_feature_alignment as fixtures


class DataCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="feature_collection_test_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.collectors: list[AsyncJsonlCollector] = []
        self.addCleanup(self.stop_collectors)

    def stop_collectors(self) -> None:
        for collector in self.collectors:
            collector.stop()

    def config(self, **overrides) -> CollectionConfig:
        values = {
            "run_id": "test_run", "scenario_id": "normal_chat",
            "server_build_id": "sha256:" + "a" * 64,
            "scenario_manifest": AI_ROOT / "configs" / "collection_scenarios.json",
            "dataset_purpose": "integration_test", "output_dir": self.root,
            "queue_capacity": 1024, "flush_interval_s": 0.02,
        }
        values.update(overrides)
        return CollectionConfig(**values)

    def create_collector(self, **overrides) -> AsyncJsonlCollector:
        collector = AsyncJsonlCollector(self.config(**overrides))
        self.collectors.append(collector)
        return collector

    def records(self, collector: AsyncJsonlCollector) -> list[dict]:
        return [json.loads(line) for line in (collector.run_dir / "features.jsonl").read_text(encoding="utf-8").splitlines()]

    def wait_until(self, predicate) -> None:
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "worker 대기 시간 초과")
            time.sleep(0.005)

    def test_message_summary_and_independent_label_manifest(self) -> None:
        collector = self.create_collector(scenario_id="malformed_chat_payload")
        payloads = [fixtures.FeatureAlignmentTests.message_payload(), fixtures.FeatureAlignmentTests.summary_payload()]
        for payload in payloads:
            self.assertTrue(collector.try_collect(payload))
        self.assertTrue(collector.stop())
        records = self.records(collector)
        self.assertEqual([record["payload"] for record in records], payloads)
        self.assertEqual([record["collection_sequence_id"] for record in records], [1, 2])
        self.assertEqual([record["request_index"] for record in records], [3, None])
        manifest = json.loads((collector.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["selected_scenario"]["label"], 1)
        self.assertEqual(manifest["label_source"], "independent_scenario_definition")
        # 메시지의 오류 수가 0이어도 정답은 사전에 정의한 시나리오에서 가져온다.
        self.assertEqual(records[0]["payload"]["features"]["error_count_total"], 0)
        self.assertNotIn("label", records[0]["payload"])
        self.assertNotIn("prediction", records[0])
        summary = json.loads((collector.run_dir / "collection_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["enqueued"], summary["flushed"])
        self.assertEqual(summary["unfinished"], 0)

    def test_snapshot_is_detached_from_callers_mutation(self) -> None:
        collector = self.create_collector()
        payload = fixtures.FeatureAlignmentTests.message_payload()
        collector.try_collect(payload)
        payload["features"]["packet_size"] = 999
        collector.stop()
        self.assertEqual(self.records(collector)[0]["payload"]["features"]["packet_size"], 24)

    def test_periodic_flush_before_shutdown(self) -> None:
        collector = self.create_collector()
        collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
        self.wait_until(lambda: collector.status()["flushed"] == 1)
        self.assertEqual(len(self.records(collector)), 1)
        self.assertEqual(collector.status()["state"], "running")

    def test_bounded_queue_drops_without_waiting_for_writer(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = AsyncJsonlCollector._write_record

        def slow_write(collector, record):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("테스트 writer 해제 실패")
            original(collector, record)

        with patch.object(AsyncJsonlCollector, "_write_record", slow_write):
            collector = self.create_collector(queue_capacity=1)
            try:
                self.assertTrue(collector.try_collect(fixtures.FeatureAlignmentTests.message_payload()))
                self.assertTrue(entered.wait(5))
                self.assertTrue(collector.try_collect(fixtures.FeatureAlignmentTests.message_payload()))
                self.assertFalse(collector.try_collect(fixtures.FeatureAlignmentTests.message_payload()))
                self.assertFalse(release.is_set())
                self.assertEqual(collector.status()["dropped_full"], 1)
                self.assertEqual(collector.status()["pending"], 1)
            finally:
                release.set()
                collector.stop()
        self.assertEqual(len(self.records(collector)), 2)
        summary = json.loads((collector.run_dir / "collection_summary.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["complete"])

    def test_concurrent_producers_keep_unique_collection_ids(self) -> None:
        collector = self.create_collector()
        payload = fixtures.FeatureAlignmentTests.message_payload()
        with ThreadPoolExecutor(max_workers=8) as workers:
            accepted = list(workers.map(lambda _: collector.try_collect(payload), range(400)))
        self.assertTrue(all(accepted))
        collector.stop()
        records = self.records(collector)
        self.assertEqual([record["collection_sequence_id"] for record in records], list(range(1, 401)))
        self.assertEqual(collector.status()["attempted"], 400)
        self.assertEqual(collector.status()["written"], 400)

    def test_closed_collector_rejects_and_stop_is_idempotent(self) -> None:
        collector = self.create_collector()
        self.assertTrue(collector.stop())
        self.assertFalse(collector.try_collect(fixtures.FeatureAlignmentTests.message_payload()))
        self.assertTrue(collector.stop())
        self.assertEqual(collector.status()["dropped_closed"], 1)

    def test_io_failure_is_latched_and_accounts_for_records(self) -> None:
        with patch.object(AsyncJsonlCollector, "_write_record", side_effect=OSError("테스트 디스크 오류")):
            collector = self.create_collector()
            collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
            self.wait_until(lambda: collector.status()["state"] == "failed")
            self.assertFalse(collector.try_collect(fixtures.FeatureAlignmentTests.message_payload()))
            self.assertFalse(collector.stop())
        stats = collector.status()
        self.assertEqual(stats["failed_records"], 1)
        self.assertEqual(stats["dropped_failed"], 1)
        self.assertEqual(stats["unfinished"], 0)
        self.assertIn("테스트 디스크 오류", stats["write_error"])

    def test_writer_failure_counts_waiting_records_as_lost(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def failing_write(collector, record):
            entered.set()
            release.wait(5)
            raise OSError("테스트 대기 큐 저장 실패")

        with patch.object(AsyncJsonlCollector, "_write_record", failing_write):
            collector = self.create_collector(queue_capacity=2)
            try:
                collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
                self.assertTrue(entered.wait(5))
                collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
                collector.try_collect(fixtures.FeatureAlignmentTests.summary_payload())
            finally:
                release.set()
                collector.stop()
        stats = collector.status()
        self.assertEqual(stats["enqueued"], 3)
        self.assertEqual(stats["failed_records"], 3)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["unfinished"], 0)

    def test_flush_failure_does_not_claim_written_data_was_flushed(self) -> None:
        with patch.object(AsyncJsonlCollector, "_flush", side_effect=OSError("테스트 flush 실패")):
            collector = self.create_collector()
            collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
            self.assertFalse(collector.stop())
        stats = collector.status()
        self.assertEqual(stats["written"], 1)
        self.assertEqual(stats["flushed"], 0)
        summary = json.loads((collector.run_dir / "collection_summary.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["complete"])

    def test_summary_write_failure_is_reported_separately(self) -> None:
        collector = self.create_collector()
        with patch.object(Path, "write_text", side_effect=OSError("테스트 summary 실패")):
            self.assertFalse(collector.stop())
        self.assertIsNone(collector.status()["write_error"])
        self.assertIn("테스트 summary 실패", collector.status()["summary_error"])

    def test_slow_writer_timeout_is_not_claimed_complete(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = AsyncJsonlCollector._write_record

        def slow_write(collector, record):
            entered.set()
            release.wait(5)
            original(collector, record)

        with patch.object(AsyncJsonlCollector, "_write_record", slow_write):
            collector = self.create_collector(shutdown_timeout_s=0.01)
            try:
                collector.try_collect(fixtures.FeatureAlignmentTests.message_payload())
                self.assertTrue(entered.wait(5))
                self.assertFalse(collector.stop())
                self.assertTrue(collector.status()["shutdown_timed_out"])
            finally:
                release.set()
                self.wait_until(lambda: not collector._worker.is_alive())
        summary = json.loads((collector.run_dir / "collection_summary.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["complete"])

    def test_duplicate_run_and_unsafe_ids_are_rejected(self) -> None:
        collector = self.create_collector()
        collector.stop()
        with self.assertRaises(FileExistsError):
            self.create_collector()
        for run_id in ["../escape", "a/b", "a\\b", "", "a" * 97]:
            with self.subTest(run_id=run_id), self.assertRaises(ValidationError):
                self.config(run_id=run_id)
        with self.assertRaises(ValueError):
            self.create_collector(run_id="other", scenario_id="undefined")
        self.assertFalse((self.root / "other").exists())

    def test_manifest_is_validated_and_snapshotted_once(self) -> None:
        manifest = json.loads((AI_ROOT / "configs" / "collection_scenarios.json").read_text(encoding="utf-8"))
        path = self.root / "scenarios.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        collector = self.create_collector(scenario_manifest=path)
        manifest["scenarios"][0]["label"] = 1
        path.write_text(json.dumps(manifest), encoding="utf-8")
        collector.stop()
        saved = json.loads((collector.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["selected_scenario"]["label"], 0)
        with self.assertRaises(ValidationError):
            load_manifest(path, "normal_chat")

    def test_manifest_rejects_duplicate_ids_and_boolean_labels(self) -> None:
        manifest = json.loads((AI_ROOT / "configs" / "collection_scenarios.json").read_text(encoding="utf-8"))
        path = self.root / "scenarios.json"
        manifest["scenarios"][1]["scenario_id"] = manifest["scenarios"][0]["scenario_id"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_manifest(path, "normal_chat")
        manifest["scenarios"] = [manifest["scenarios"][0]]
        manifest["scenarios"][0]["label"] = False
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_manifest(path, "normal_chat")

    def test_config_paths_are_relative_to_config_not_working_directory(self) -> None:
        values = self.config().model_dump(mode="json")
        values.update(scenario_manifest="scenarios.json", output_dir="captures")
        path = self.root / "capture.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        config = load_collection_config(path)
        self.assertEqual(config.output_dir, self.root / "captures")
        self.assertEqual(config.scenario_manifest, self.root / "scenarios.json")

    def test_api_lifespan_collects_only_valid_payloads_and_flushes(self) -> None:
        with patch("inference_api.main.config_from_environment", return_value=self.config()):
            with TestClient(app) as client:
                response = client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["model_version"], "dummy-v0")
                self.assertFalse(response.json()["is_attack"])
                bad = fixtures.FeatureAlignmentTests.message_payload()
                bad["feature_contract_id"] = "old-version"
                self.assertEqual(client.post("/predict", json=bad).status_code, 422)
                stats = client.get("/collection/status").json()
                self.assertEqual(stats["attempted"], 1)
                self.assertTrue(stats["enabled"])
        summary = json.loads((self.root / "test_run" / "collection_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["flushed"], 1)
        self.assertIsNone(app.state.feature_collector)

    def test_api_writer_failure_does_not_change_dummy_response(self) -> None:
        with patch("inference_api.main.config_from_environment", return_value=self.config()), \
                patch.object(AsyncJsonlCollector, "_write_record", side_effect=OSError("테스트 쓰기 실패")):
            with TestClient(app) as client:
                self.assertEqual(client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload()).status_code, 200)
                self.wait_until(lambda: client.get("/collection/status").json()["state"] == "failed")
                response = client.post("/predict", json=fixtures.FeatureAlignmentTests.summary_payload())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["model_version"], "dummy-v0")
                self.assertEqual(client.get("/collection/status").json()["dropped_failed"], 1)

    def test_api_full_queue_does_not_wait_or_change_response(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = AsyncJsonlCollector._write_record

        def slow_write(collector, record):
            entered.set()
            release.wait(5)
            original(collector, record)

        with patch("inference_api.main.config_from_environment", return_value=self.config(queue_capacity=1)), \
                patch.object(AsyncJsonlCollector, "_write_record", slow_write):
            with TestClient(app) as client:
                try:
                    payload = fixtures.FeatureAlignmentTests.message_payload()
                    self.assertEqual(client.post("/predict", json=payload).status_code, 200)
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(client.post("/predict", json=payload).status_code, 200)
                    response = client.post("/predict", json=payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["model_version"], "dummy-v0")
                    self.assertEqual(client.get("/collection/status").json()["dropped_full"], 1)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()

    def test_default_api_does_not_create_collection(self) -> None:
        with patch("inference_api.main.config_from_environment", return_value=None):
            with TestClient(app) as client:
                self.assertEqual(client.get("/collection/status").json(), {"enabled": False, "state": "disabled"})
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
