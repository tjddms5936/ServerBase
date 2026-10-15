from __future__ import annotations

import math

import pandas as pd

from .approved_bundle import ServingConfig, VerifiedBundle, load_verified_bundle
from .feature_transformer import FeatureVector, MESSAGE_FEATURE_ORDER
from .schemas import InferenceRequest, InferenceResponse


class UnsupportedEventError(ValueError):
    pass


class ApprovedRfService:
    def __init__(self, config: ServingConfig) -> None:
        self.config = config
        self.bundle: VerifiedBundle = load_verified_bundle(config)
        self.model_version = config.model_version
        self.max_concurrent_predictions = config.max_concurrent_predictions
        self.verbose_payload_logging = config.verbose_payload_logging

    def health_details(self) -> dict:
        return {"model_kind": "random_forest", "supported_event_types": ["message"], "decision_threshold": 0.7,
                "decision_scope": "local_advisory", "model_sha256": self.bundle.manifest["files"]["model"]["sha256"],
                "automatic_blocking_allowed": False}

    def predict(self, payload: InferenceRequest, feature_vector: FeatureVector) -> InferenceResponse:
        if payload.event_type != "message":
            raise UnsupportedEventError("승인 RF는 message만 판단합니다. session_summary는 추론 대상이 아닙니다.")
        if (feature_vector.event_type != "message" or feature_vector.names != MESSAGE_FEATURE_ORDER or
                len(feature_vector.values) != 7 or any(not math.isfinite(value) or value < 0 for value in feature_vector.values)):
            raise ValueError("RF 원본 입력 순서·개수·수치가 다릅니다.")
        frame = pd.DataFrame([feature_vector.values], columns=MESSAGE_FEATURE_ORDER, dtype="float64")
        probability = self.bundle.pipeline.predict_proba(frame)
        if probability.shape != (1, 2) or not math.isfinite(float(probability[0, 1])) or not 0 <= probability[0, 1] <= 1:
            raise ValueError("RF 추론 확률이 유효하지 않습니다.")
        score = float(probability[0, 1])
        positive = score >= 0.7
        return InferenceResponse(request_id=str(payload.queue_sequence_id), label="controlled_anomaly" if positive else "normal",
            is_attack=positive, confidence=score if positive else 1.0 - score, model_version=self.model_version,
            received_event_type="message", anomaly_score=score, decision_threshold=0.7, decision_scope="local_advisory")
