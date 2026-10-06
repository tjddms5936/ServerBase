from __future__ import annotations

import asyncio
import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from data_collection.collector import AsyncJsonlCollector
from data_collection.config import config_from_environment

from .config import settings
from .feature_transformer import build_feature_vector
from .model_service import model_service
from .payload_validation import OPERATIONAL_CONTRACT
from .schemas import HealthResponse, InferenceRequest, InferenceResponse


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.feature_collector = None
    config = config_from_environment()
    collector = AsyncJsonlCollector(config) if config is not None else None
    application.state.feature_collector = collector
    try:
        yield
    finally:
        if collector is not None:
            # 큐 배출과 최종 flush를 기다리는 동안 이벤트 루프 자체는 막지 않는다.
            await asyncio.to_thread(collector.stop)
        application.state.feature_collector = None


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # NaN/Infinity 자체가 오류 입력일 때도 응답 JSON 직렬화 실패로 500이 되지 않게 한다.
    details = jsonable_encoder(error.errors(), custom_encoder={
        float: lambda value: value if math.isfinite(value) else str(value),
        ValueError: str,
    })
    return JSONResponse(status_code=422, content={"detail": details})


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_version=settings.model_version,
        feature_contract_ids={name: event["feature_contract_id"] for name, event in OPERATIONAL_CONTRACT["events"].items()},
    )


@app.get("/collection/status")
async def collection_status(request: Request) -> dict:
    collector = getattr(request.app.state, "feature_collector", None)
    return collector.status() if collector is not None else {"enabled": False, "state": "disabled"}


@app.post("/predict", response_model=InferenceResponse)
async def predict(payload: InferenceRequest, request: Request) -> InferenceResponse:
    feature_vector = build_feature_vector(payload)
    collector = getattr(request.app.state, "feature_collector", None)
    if collector is not None:
        try:
            # HTTP 입력 검증 이후에만 큐에 넣고 저장 결과와 추론 결과는 독립적으로 처리한다.
            collector.try_collect(payload.model_dump(mode="json"))
        except Exception:
            logger.exception("수집 요청 실패, 추론은 계속합니다.")

    if collector is None or collector.config.verbose_payload_logging:
        print("[inference-api] received feature payload")
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        print("[inference-api] feature vector")
        print(json.dumps({"names": feature_vector.names, "values": feature_vector.values}, indent=2))

    return model_service.predict(payload, feature_vector)
