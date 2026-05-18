from __future__ import annotations

from .config import settings
from .feature_transformer import FeatureVector
from .schemas import InferenceRequest, InferenceResponse


class DummyModelService:
    def predict(self, payload: InferenceRequest, feature_vector: FeatureVector) -> InferenceResponse:
        return InferenceResponse(
            request_id=str(payload.queue_sequence_id),
            label="normal",
            is_attack=False,
            confidence=0.5,
            model_version=settings.model_version,
            received_event_type=feature_vector.event_type,
        )


model_service = DummyModelService()
