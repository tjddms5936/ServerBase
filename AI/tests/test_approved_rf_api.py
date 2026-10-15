from __future__ import annotations

import copy
import io
import json
import platform
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import inference_api.approved_bundle as loader
from inference_api.feature_transformer import build_feature_vector, MESSAGE_FEATURE_ORDER
from inference_api.main import create_app
from inference_api.rf_service import ApprovedRfService, UnsupportedEventError
from inference_api.schemas import InferenceRequest
import test_feature_alignment as fixtures


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class ApprovedRfTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evaluation_root = self.root / "evaluation"
        self.directory = self.evaluation_root / "deployment_bundle"
        self.directory.mkdir(parents=True)
        contract = loader.OPERATIONAL_CONTRACT
        event = contract["events"]["message"]
        self.raw_schema = {"dataset_id": "fixture", "feature_contract_id": event["feature_contract_id"],
            "raw_input_features": MESSAGE_FEATURE_ORDER, "wire_schema_version": contract["wire_schema_version"],
            "capture_layer": contract["capture_layer"], "observation_scope": event["observation_scope"], "event_type": "message",
            "feature_units": {name: contract["features"][name]["unit"] for name in MESSAGE_FEATURE_ORDER},
            "feature_dtypes": {name: contract["features"][name]["dtype"] for name in MESSAGE_FEATURE_ORDER},
            "missing_value_policy": "reject_without_zero_fill"}
        self.input_schema = {"raw_feature_contract_id": event["feature_contract_id"], "raw_input_features": MESSAGE_FEATURE_ORDER,
            "model_input_features": MESSAGE_FEATURE_ORDER, "input_transform_id": "raw7_v1",
            "preprocessing": "identity_no_scaler", "requires_ordered_session_prefix": False}
        self.policy = {"candidate_id": "rf_raw7", "threshold": 0.7, "positive_class": 1, "comparison": ">=",
            "input_transform_id": "raw7_v1", "test_confirmed": True, "scope": "local_advisory_integration_only", "automatic_blocking_allowed": False}
        self.labels = {"normal": 0, "controlled_anomaly": 1}
        # 작은 합성 모델·승인 파일만 임시 폴더에 만든다. 사용자 모델이나 데이터셋을 테스트에 사용하지 않는다.
        normal, anomaly = [24, 16, 200, 15, 80, 40, 0], [24, 16, 1, 0, 480, 40, 0]
        X = pd.DataFrame([normal, anomaly] * 20, columns=MESSAGE_FEATURE_ORDER, dtype="float64")
        pipeline = Pipeline([("classifier", RandomForestClassifier(n_estimators=100, min_samples_leaf=5, random_state=42, n_jobs=1))])
        pipeline.fit(X, np.array([0, 1] * 20))
        joblib.dump(pipeline, self.directory / "pipeline.joblib", compress=3)
        for name, value in (("feature_schema.json", self.raw_schema), ("model_input_schema.json", self.input_schema),
                            ("label_map.json", self.labels), ("decision_policy.json", self.policy)):
            write_json(self.directory / name, value)
        self.files = {role: {"path": name, "sha256": loader.sha256((self.directory / name).read_bytes()), "bytes": (self.directory / name).stat().st_size}
            for role, name in {"model": "pipeline.joblib", "feature_schema": "feature_schema.json", "model_input_schema": "model_input_schema.json",
                               "label_map": "label_map.json", "decision_policy": "decision_policy.json"}.items()}
        criteria = {"max_false_positive_rate": 0.1, "max_normal_batched_false_positive_rate": 0.1, "min_recall": 0.5,
            "max_single_request_p95_ms": 25.0, "max_process_peak_rss_mib": 512.0, "max_deployment_artifacts_mib": 50.0,
            "scope": "local_advisory_integration_only"}
        cost = {"single_request_worst_process_p95_ms": 1.0, "maximum_native_process_peak_rss_bytes": 100 * 1024**2}
        self.gate = {"status": "candidate_locked_before_test", "descriptor": {"candidate_id": "rf_raw7", "threshold": 0.7, "files": self.files},
            "criteria": criteria, "test_rule": "confirm_selected_policy_without_reselection", "dataset_id": "fixture",
            "expected_test_sha256": {name: str(index) * 64 for index, name in enumerate(("X_test.csv", "y_test.csv", "metadata_test.csv"))},
            "selected_model_id": "selected_recovery_rf", "cost_summary": cost}
        signature = loader.sha256(json.dumps({name: self.gate[name] for name in ("descriptor", "criteria", "test_rule", "expected_test_sha256")},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.gate["policy_signature"] = signature
        gate_path = self.root / "candidate_lock.json"
        write_json(gate_path, self.gate)
        verdict = {"status": loader.APPROVED, "reasons": [], "policy_signature": signature,
            "candidate_lock_path": str(gate_path), "candidate_lock_sha256": loader.sha256(gate_path.read_bytes()),
            "criteria": criteria, "test_input_sha256": self.gate["expected_test_sha256"], "cost_summary": cost,
            "metrics": {"false_positive_rate": 0.01, "recall": 0.75}, "by_scenario": {"normal_batched_chat": {"false_positive_rate": 0.05}}}
        verdict_path = self.evaluation_root / "test_verdict.json"
        write_json(verdict_path, verdict)
        write_json(self.evaluation_root / "evaluation_protocol_lock.json", {"test_files_read": False})
        self.manifest = {"manifest_version": "operational_rf_deployment_v1", "status": loader.APPROVED, "local_integration_approved": True,
            "production_ready": False, "automatic_blocking_allowed": False, "requires_complete_evaluation_manifest": True,
            "evaluation_manifest_path": str(self.evaluation_root / "evaluation_manifest.json"), "test_verdict_path": str(verdict_path),
            "kind": "random_forest", "candidate_id": "rf_raw7", "threshold": 0.7, "positive_class": 1, "comparison": ">=",
            "feature_contract_id": event["feature_contract_id"], "feature_order": MESSAGE_FEATURE_ORDER, "input_transform_id": "raw7_v1",
            "requires_ordered_session_prefix": False, "preprocessing": "identity_no_scaler", "dataset_id": "fixture",
            "files": self.files, "required_files_bytes": sum(value["bytes"] for value in self.files.values()),
            "candidate_lock_sha256": verdict["candidate_lock_sha256"], "policy_signature": signature,
            "model_id": "selected_recovery_rf", "test_verdict_sha256": loader.sha256(verdict_path.read_bytes()),
            "environment": {"python": platform.python_version(), "numpy": np.__version__, "scikit_learn": sklearn.__version__}}
        self.complete = {"status": "complete", "approval_status": loader.APPROVED, "test_evaluated": True,
            "model_training_performed": False, "candidate_lock_sha256": verdict["candidate_lock_sha256"], "policy_signature": signature}
        values = loader.load_serving_config().model_dump()
        values.update(evaluation_root=self.evaluation_root, candidate_lock_path=gate_path, model_version="fixture-rf-v1")
        self.config = loader.ServingConfig.model_validate(values)
        self.repin()

    def repin(self):
        write_json(self.directory / "deployment_manifest.json", self.manifest)
        self.complete["artifacts"] = {path.relative_to(self.evaluation_root).as_posix():
            {"sha256": loader.sha256(path.read_bytes()), "bytes": path.stat().st_size}
            for path in self.evaluation_root.rglob("*") if path.is_file() and path.name != "evaluation_manifest.json"}
        write_json(self.evaluation_root / "evaluation_manifest.json", self.complete)
        self.config = self.config.model_copy(update={
            "expected_evaluation_manifest_sha256": loader.sha256((self.evaluation_root / "evaluation_manifest.json").read_bytes()),
            "expected_deployment_manifest_sha256": loader.sha256((self.directory / "deployment_manifest.json").read_bytes())})

    def service(self):
        return ApprovedRfService(self.config)

    def client(self, service=None):
        return TestClient(create_app(lambda: service or self.service()))

    def test_verified_bundle_never_reads_dataset_or_trains(self):
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.suffix in {".csv", ".npz", ".jsonl"}:
                raise AssertionError("추론 기동의 데이터셋 접근 금지")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded), patch.object(Pipeline, "fit", side_effect=AssertionError("학습 금지")), \
                patch.object(RandomForestClassifier, "fit", side_effect=AssertionError("학습 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("학습 금지")):
            bundle = loader.load_verified_bundle(self.config)
        self.assertEqual(len(bundle.evidence), 10)
        self.assertEqual(bundle.manifest["threshold"], 0.7)

    def test_missing_complete_manifest_refuses_before_pickle_load(self):
        (self.evaluation_root / "evaluation_manifest.json").unlink()
        with patch.object(joblib, "load", side_effect=AssertionError("검증 전 로딩 금지")), self.assertRaises(FileNotFoundError):
            self.service()

    def test_tampered_model_or_missing_schema_refuses_before_pickle_load(self):
        with (self.directory / "pipeline.joblib").open("ab") as stream:
            stream.write(b"changed")
        with patch.object(joblib, "load", side_effect=AssertionError("변조 모델 로딩 금지")), self.assertRaisesRegex(ValueError, "SHA"):
            self.service()

    def test_unapproved_complete_state_or_production_claim_is_rejected(self):
        self.complete["approval_status"] = "withheld_after_test"
        self.repin()
        with self.assertRaises(ValueError):
            self.service()
        self.complete["approval_status"] = loader.APPROVED
        self.manifest["production_ready"] = True
        self.repin()
        with self.assertRaises(ValueError):
            self.service()

    def test_version_mismatch_refuses_before_deserialization(self):
        self.manifest["environment"]["scikit_learn"] = "0.0.0"
        self.repin()
        with patch.object(joblib, "load", side_effect=AssertionError("버전 검증 전 로딩 금지")), self.assertRaisesRegex(ValueError, "버전"):
            self.service()

    def test_manifest_path_traversal_and_extra_files_are_rejected(self):
        for name in ("../outside.json", "X_test.csv"):
            with self.subTest(name=name):
                self.complete["artifacts"][name] = {"sha256": "0" * 64, "bytes": 1}
                write_json(self.evaluation_root / "evaluation_manifest.json", self.complete)
                config = self.config.model_copy(update={"expected_evaluation_manifest_sha256": loader.sha256((self.evaluation_root / "evaluation_manifest.json").read_bytes())})
                with self.assertRaises(ValueError):
                    loader.load_verified_bundle(config)
                self.complete["artifacts"].pop(name)
        with self.assertRaises(ValueError):
            loader.inside(self.evaluation_root, "../outside")

    def test_units_order_transform_labels_and_threshold_are_strict(self):
        for section, key, value in (("raw", "feature_units", {}), ("raw", "raw_input_features", list(reversed(MESSAGE_FEATURE_ORDER))),
                                    ("input", "requires_ordered_session_prefix", True), ("input", "preprocessing", "standard_scaler"),
                                    ("policy", "threshold", 0.5), ("policy", "automatic_blocking_allowed", True)):
            documents = {"raw": copy.deepcopy(self.raw_schema), "input": copy.deepcopy(self.input_schema), "policy": copy.deepcopy(self.policy)}
            documents[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                loader.validate_schema(self.manifest, documents["raw"], documents["input"], self.labels, documents["policy"])
        with self.assertRaises(ValueError):
            loader.validate_schema(self.manifest, self.raw_schema, self.input_schema, {"normal": 1, "controlled_anomaly": 0}, self.policy)

    def test_invalid_pipeline_or_class_order_is_rejected(self):
        with patch.object(joblib, "load", return_value=object()), self.assertRaises(ValueError):
            self.service()
        original = joblib.load
        pipeline = original(self.directory / "pipeline.joblib")
        pipeline.named_steps["classifier"].classes_ = np.array([1, 0])
        with patch.object(joblib, "load", return_value=pipeline), self.assertRaises(ValueError):
            self.service()

    def test_health_advertises_actual_readiness_and_message_only(self):
        with self.client() as client:
            body = client.get("/health").json()
            self.assertTrue(body["ready"])
            self.assertEqual(body["model_version"], "fixture-rf-v1")
            self.assertEqual(body["model_kind"], "random_forest")
            self.assertEqual(body["supported_event_types"], ["message"])
            self.assertEqual(body["decision_threshold"], 0.7)
            self.assertFalse(body["automatic_blocking_allowed"])

    def test_no_lifespan_or_closed_app_never_falls_back_to_dummy(self):
        client = self.client()
        try:
            self.assertEqual(client.get("/health").status_code, 503)
            self.assertEqual(client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload()).status_code, 503)
        finally:
            client.close()
        app = create_app(self.service)
        with TestClient(app) as ready:
            self.assertEqual(ready.get("/health").status_code, 200)
        self.assertFalse(app.state.inference_ready)

    def test_startup_failure_is_not_hidden_by_dummy_service(self):
        (self.directory / "feature_schema.json").unlink()
        with self.assertRaises(FileNotFoundError), self.client():
            pass

    def test_real_response_matches_pipeline_and_cpp_fields(self):
        service = self.service()
        payload = fixtures.FeatureAlignmentTests.message_payload()
        parsed = TypeAdapter(InferenceRequest).validate_python(payload)
        vector = build_feature_vector(parsed)
        probability = float(service.bundle.pipeline.predict_proba(pd.DataFrame([vector.values], columns=vector.names))[0, 1])
        with self.client(service) as client:
            response = client.post("/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue({"request_id", "label", "is_attack", "confidence", "model_version", "received_event_type"} <= set(body))
        self.assertEqual(body["anomaly_score"], probability)
        self.assertEqual(body["is_attack"], probability >= 0.7)
        self.assertEqual(body["request_id"], str(payload["queue_sequence_id"]))
        self.assertEqual(body["decision_scope"], "local_advisory")

    def test_threshold_boundary_and_confidence_are_not_argmax_or_calibration(self):
        service = self.service()
        with self.client(service) as client:
            for score in (0.0, 0.699999, 0.7, 1.0):
                with patch.object(service.bundle.pipeline, "predict_proba", return_value=np.array([[1 - score, score]])):
                    body = client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload()).json()
                self.assertEqual(body["is_attack"], score >= 0.7)
                self.assertEqual(body["label"], "controlled_anomaly" if score >= 0.7 else "normal")
                self.assertEqual(body["confidence"], score if score >= 0.7 else 1 - score)

    def test_summary_is_explicitly_unsupported_and_never_predicts(self):
        service = self.service()
        with self.client(service) as client, patch.object(service.bundle.pipeline, "predict_proba", side_effect=AssertionError("요약 모델 입력 금지")):
            response = client.post("/predict", json=fixtures.FeatureAlignmentTests.summary_payload())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_event_type")
        self.assertNotIn("is_attack", response.json())
        parsed = TypeAdapter(InferenceRequest).validate_python(fixtures.FeatureAlignmentTests.summary_payload())
        with self.assertRaises(UnsupportedEventError):
            service.predict(parsed, build_feature_vector(parsed))

    def test_invalid_input_never_reaches_rf(self):
        service = self.service()
        with self.client(service) as client, patch.object(service.bundle.pipeline, "predict_proba", side_effect=AssertionError("검증 전 추론 금지")):
            for value in (None, "1", -1, True):
                payload = fixtures.FeatureAlignmentTests.message_payload()
                payload["features"]["packet_size"] = value
                self.assertEqual(client.post("/predict", json=payload).status_code, 422)
            self.assertEqual(client.post("/predict", content="{", headers={"Content-Type": "application/json"}).status_code, 422)

    def test_model_is_loaded_once_and_no_request_file_io(self):
        original = joblib.load
        with patch.object(joblib, "load", wraps=original) as load, self.client() as client:
            self.assertEqual(load.call_count, 1)
            with patch.object(Path, "open", side_effect=AssertionError("요청 중 파일 접근 금지")):
                for _ in range(3):
                    self.assertEqual(client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload()).status_code, 200)
            self.assertEqual(load.call_count, 1)

    def test_metadata_and_json_field_order_do_not_change_predictors(self):
        payload = fixtures.FeatureAlignmentTests.message_payload()
        other = copy.deepcopy(payload)
        other["session"]["session_id"], other["queue_sequence_id"] = 999, 999
        other["features"] = dict(reversed(list(other["features"].items())))
        with self.client() as client:
            first = client.post("/predict", json=payload).json()
            second = client.post("/predict", json=other).json()
        self.assertEqual(first["anomaly_score"], second["anomaly_score"])
        self.assertEqual(second["request_id"], "999")

    def test_rf_failure_returns_503_without_normal_or_dummy_fallback(self):
        service = self.service()
        with self.client(service) as client, patch.object(service.bundle.pipeline, "predict_proba", side_effect=RuntimeError("임시 추론 실패")), \
                patch("inference_api.main.model_service.predict", side_effect=AssertionError("dummy 대체 금지")), \
                self.assertLogs("inference_api.main", level="ERROR"):
            response = client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "inference_failed")
        self.assertNotIn("label", response.json())

    def test_nonfinite_or_wrong_shape_probabilities_are_not_returned(self):
        service = self.service()
        parsed = TypeAdapter(InferenceRequest).validate_python(fixtures.FeatureAlignmentTests.message_payload())
        for values in (np.array([[0, np.nan]]), np.array([[0, 1.1]]), np.array([[1.0]])):
            with patch.object(service.bundle.pipeline, "predict_proba", return_value=values), self.assertRaises(ValueError):
                service.predict(parsed, build_feature_vector(parsed))

    def test_busy_prediction_is_bounded_and_health_event_loop_is_responsive(self):
        service = self.service()
        entered, release = threading.Event(), threading.Event()
        original = service.predict
        def slow(payload, vector):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("테스트 추론 해제 실패")
            return original(payload, vector)
        with self.client(service) as client, patch.object(service, "predict", slow), ThreadPoolExecutor(max_workers=1) as worker:
            running = worker.submit(client.post, "/predict", json=fixtures.FeatureAlignmentTests.message_payload())
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(client.get("/health").status_code, 200)
                busy = client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload())
                self.assertEqual(busy.status_code, 503)
                self.assertEqual(busy.json()["detail"]["code"], "inference_busy")
            finally:
                release.set()
            self.assertEqual(running.result(timeout=5).status_code, 200)
            self.assertEqual(client.post("/predict", json=fixtures.FeatureAlignmentTests.message_payload()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
