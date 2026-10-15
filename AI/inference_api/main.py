from __future__ import annotations

import asyncio
import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable

from anyio import CapacityLimiter, WouldBlock, to_thread
from fastapi import FastAPI, HTTPException, Request
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
    application.state.inference_service = None
    collector = None
    try:
        factory = application.state.service_factory
        if factory is not None:
            # 승인 검증과 모델 로딩은 기동 시 한 번 수행한다. 실패하면 dummy로 대체하지 않는다.
            application.state.inference_service = await to_thread.run_sync(factory)
            application.state.inference_limiter = CapacityLimiter(application.state.inference_service.max_concurrent_predictions)
        config = config_from_environment()
        collector = AsyncJsonlCollector(config) if config is not None else None
        application.state.feature_collector = collector
        application.state.inference_ready = True
        yield
    finally:
        if collector is not None:
            # 큐 배출과 최종 flush를 기다리는 동안 이벤트 루프 자체는 막지 않는다.
            await asyncio.to_thread(collector.stop)
        application.state.feature_collector = None
        application.state.inference_service = None
        application.state.inference_ready = application.state.service_factory is None


async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # NaN/Infinity 자체가 오류 입력일 때도 응답 JSON 직렬화 실패로 500이 되지 않게 한다.
    details = jsonable_encoder(error.errors(), custom_encoder={
        float: lambda value: value if math.isfinite(value) else str(value),
        ValueError: str,
    })
    return JSONResponse(status_code=422, content={"detail": details})


async def health(request: Request) -> HealthResponse:
    if not request.app.state.inference_ready:
        raise HTTPException(status_code=503, detail={"code": "model_not_ready"})
    service = request.app.state.inference_service
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_version=service.model_version if service is not None else settings.model_version,
        feature_contract_ids={name: event["feature_contract_id"] for name, event in OPERATIONAL_CONTRACT["events"].items()},
        ready=True,
        **(service.health_details() if service is not None else {"model_kind": "dummy", "supported_event_types": ["message", "session_summary"]}),
    )


async def collection_status(request: Request) -> dict:
    collector = getattr(request.app.state, "feature_collector", None)
    return collector.status() if collector is not None else {"enabled": False, "state": "disabled"}


async def predict(payload: InferenceRequest, request: Request) -> InferenceResponse:
    if not request.app.state.inference_ready:
        raise HTTPException(status_code=503, detail={"code": "model_not_ready"})
    service = request.app.state.inference_service
    feature_vector = build_feature_vector(payload)
    collector = getattr(request.app.state, "feature_collector", None)
    if collector is not None:
        try:
            # HTTP 입력 검증 이후에만 큐에 넣고 저장 결과와 추론 결과는 독립적으로 처리한다.
            collector.try_collect(payload.model_dump(mode="json"))
        except Exception:
            logger.exception("수집 요청 실패, 추론은 계속합니다.")

    verbose = service.verbose_payload_logging if service is not None else collector is None or collector.config.verbose_payload_logging
    if verbose:
        print("[inference-api] received feature payload")
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        print("[inference-api] feature vector")
        print(json.dumps({"names": feature_vector.names, "values": feature_vector.values}, indent=2))

    if service is None:
        # 과거 수집·회귀 테스트 전용 dummy 경로는 기존 계약을 유지한다.
        return model_service.predict(payload, feature_vector)
    if payload.event_type != "message":
        # 요약을 가짜 정상 판단으로 반환해 C++의 마지막 메시지 결과를 덮어쓰지 않는다.
        raise HTTPException(status_code=422, detail={"code": "unsupported_event_type", "supported_event_types": ["message"]})
    limiter = request.app.state.inference_limiter
    try:
        limiter.acquire_nowait()
    except WouldBlock:
        raise HTTPException(status_code=503, detail={"code": "inference_busy"}, headers={"Retry-After": "1"})
    try:
        # 계산은 작업 스레드에서 수행하며 요청 취소 때도 계산이 끝날 때까지 실행 슬롯을 유지한다.
        return await to_thread.run_sync(service.predict, payload, feature_vector, abandon_on_cancel=False)
    except Exception:
        logger.exception("실제 RF 추론 실패, 정상 또는 dummy 판단으로 대체하지 않습니다.")
        raise HTTPException(status_code=503, detail={"code": "inference_failed"})
    finally:
        limiter.release()


def create_app(service_factory: Callable | None = None) -> FastAPI:
    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.state.service_factory = service_factory
    application.state.inference_service = None
    application.state.inference_ready = service_factory is None
    application.state.feature_collector = None
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_api_route("/health", health, methods=["GET"], response_model=HealthResponse, response_model_exclude_none=True)
    application.add_api_route("/collection/status", collection_status, methods=["GET"])
    application.add_api_route("/predict", predict, methods=["POST"], response_model=InferenceResponse, response_model_exclude_none=True)
    return application


# 기존 수집 명령은 dummy를 명시적으로 유지하고 실제 실행은 rf_main:app을 사용한다.
app = create_app()
