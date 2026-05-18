from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    model_version: str


class MessageSessionInfo(BaseModel):
    session_id: int = Field(ge=0)
    socket_handle: int = Field(ge=0)
    logic_worker_id: int
    connection_duration_ms: float = Field(ge=0)
    bytes_received_total: int = Field(ge=0)
    bytes_sent_total: int = Field(ge=0)
    error_count_total: int = Field(ge=0)


class MessageInfo(BaseModel):
    request_index: int = Field(ge=0)
    packet_id: int
    packet_size: int = Field(ge=0)
    payload_size: int = Field(ge=0)
    message_interval_ms: float = Field(ge=0)


class MessageFeatureValues(BaseModel):
    packet_size: int = Field(ge=0)
    payload_size: int = Field(ge=0)
    connection_duration_ms: float = Field(ge=0)
    message_interval_ms: float = Field(ge=0)
    bytes_received_total: int = Field(ge=0)
    bytes_sent_total: int = Field(ge=0)
    error_count_total: int = Field(ge=0)


class MessageInferenceRequest(BaseModel):
    schema_version: Literal["v1"]
    event_type: Literal["message"]
    queue_sequence_id: int = Field(ge=0)
    timestamp: str
    session: MessageSessionInfo
    message: MessageInfo
    features: MessageFeatureValues


class SessionSummaryInfo(BaseModel):
    session_id: int = Field(ge=0)
    socket_handle: int = Field(ge=0)
    logic_worker_id: int
    is_started: bool
    is_closed: bool
    connection_duration_ms: float = Field(ge=0)
    bytes_received_total: int = Field(ge=0)
    bytes_sent_total: int = Field(ge=0)
    recv_event_count: int = Field(ge=0)
    send_event_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    avg_message_interval_ms: float = Field(ge=0)
    last_error_code: int = Field(ge=0)
    last_error_message: str


class SessionSummaryFeatureValues(BaseModel):
    connection_duration_ms: float = Field(ge=0)
    bytes_received_total: int = Field(ge=0)
    bytes_sent_total: int = Field(ge=0)
    recv_event_count: int = Field(ge=0)
    send_event_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    avg_message_interval_ms: float = Field(ge=0)


class SessionSummaryInferenceRequest(BaseModel):
    schema_version: Literal["v1"]
    event_type: Literal["session_summary"]
    queue_sequence_id: int = Field(ge=0)
    timestamp: str
    session: SessionSummaryInfo
    features: SessionSummaryFeatureValues


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
