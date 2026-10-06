from __future__ import annotations

from typing import Annotated, Literal, Self, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .payload_validation import OPERATIONAL_CONTRACT, validate_snapshot_relations


# 문자열이나 bool을 숫자로 자동 보정하지 않고 C++ 필드 범위에 맞춰 검증한다.
NonNegativeInt = Annotated[int, Field(ge=0, le=2**64 - 1, strict=True)]
NonNegativeFloat = Annotated[float, Field(ge=0, strict=True)]
FrameSize = Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]


class NetworkPayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    model_version: str
    feature_contract_ids: dict[str, str]


class MessageSessionInfo(NetworkPayloadModel):
    session_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    socket_handle: NonNegativeInt
    logic_worker_id: int = Field(ge=-1, le=2**31 - 1, strict=True)
    connection_duration_ms: NonNegativeFloat
    bytes_received_total: NonNegativeInt
    bytes_sent_total: NonNegativeInt
    error_count_total: NonNegativeInt


class MessageInfo(NetworkPayloadModel):
    request_index: int = Field(ge=1, le=2**64 - 1, strict=True)
    packet_id: int = Field(ge=0, le=65535, strict=True)
    packet_size: FrameSize
    payload_size: FrameSize
    message_interval_ms: NonNegativeFloat


class MessageFeatureValues(NetworkPayloadModel):
    packet_size: FrameSize
    payload_size: FrameSize
    connection_duration_ms: NonNegativeFloat
    message_interval_ms: NonNegativeFloat
    bytes_received_total: NonNegativeInt
    bytes_sent_total: NonNegativeInt
    error_count_total: NonNegativeInt


class MessageInferenceRequest(NetworkPayloadModel):
    schema_version: Literal["v1"]
    feature_contract_id: Literal["server_application_message_v2"]
    event_type: Literal["message"]
    queue_sequence_id: NonNegativeInt
    timestamp: str
    session: MessageSessionInfo
    message: MessageInfo
    features: MessageFeatureValues

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        # 필드별 검증이 끝난 뒤 반복 값과 시점별 관계를 함께 확인한다.
        validate_snapshot_relations(OPERATIONAL_CONTRACT, self.model_dump())
        return self


class SessionSummaryInfo(NetworkPayloadModel):
    session_id: int = Field(gt=0, le=2**64 - 1, strict=True)
    socket_handle: NonNegativeInt
    logic_worker_id: int = Field(ge=-1, le=2**31 - 1, strict=True)
    is_started: bool = Field(strict=True)
    is_closed: bool = Field(strict=True)
    connection_duration_ms: NonNegativeFloat
    bytes_received_total: NonNegativeInt
    bytes_sent_total: NonNegativeInt
    recv_event_count: NonNegativeInt
    send_event_count: NonNegativeInt
    request_count: NonNegativeInt
    error_count: NonNegativeInt
    avg_message_interval_ms: NonNegativeFloat
    last_error_code: int = Field(ge=0, le=2**32 - 1, strict=True)
    last_error_message: str


class SessionSummaryFeatureValues(NetworkPayloadModel):
    connection_duration_ms: NonNegativeFloat
    bytes_received_total: NonNegativeInt
    bytes_sent_total: NonNegativeInt
    recv_event_count: NonNegativeInt
    send_event_count: NonNegativeInt
    request_count: NonNegativeInt
    error_count: NonNegativeInt
    avg_message_interval_ms: NonNegativeFloat


class SessionSummaryInferenceRequest(NetworkPayloadModel):
    schema_version: Literal["v1"]
    feature_contract_id: Literal["server_application_session_summary_v2"]
    event_type: Literal["session_summary"]
    queue_sequence_id: NonNegativeInt
    timestamp: str
    session: SessionSummaryInfo
    features: SessionSummaryFeatureValues

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        validate_snapshot_relations(OPERATIONAL_CONTRACT, self.model_dump())
        return self


InferenceRequest = Annotated[
    Union[MessageInferenceRequest, SessionSummaryInferenceRequest],
    Field(discriminator="event_type"),
]


class InferenceResponse(BaseModel):
    request_id: str
    label: str
    is_attack: bool
    confidence: float = Field(ge=0, le=1)
    model_version: str
    received_event_type: str
