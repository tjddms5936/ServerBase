from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI

from .config import settings
from .feature_transformer import build_feature_vector
from .model_service import model_service
from .schemas import HealthResponse, InferenceRequest, InferenceResponse


app = FastAPI(title=settings.app_name)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_version=settings.model_version,
    )


@app.post("/predict", response_model=InferenceResponse)
async def predict(payload: InferenceRequest) -> InferenceResponse:
    feature_vector = build_feature_vector(payload)

    print("[inference-api] received feature payload")
    print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print("[inference-api] feature vector")
    print(json.dumps({"names": feature_vector.names, "values": feature_vector.values}, indent=2))

    return model_service.predict(payload, feature_vector)
