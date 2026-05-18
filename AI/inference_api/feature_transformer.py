from __future__ import annotations

from dataclasses import dataclass

from .schemas import InferenceRequest


MESSAGE_FEATURE_ORDER = [
    "packet_size",
    "payload_size",
    "connection_duration_ms",
    "message_interval_ms",
    "bytes_received_total",
    "bytes_sent_total",
    "error_count_total",
]

SESSION_SUMMARY_FEATURE_ORDER = [
    "connection_duration_ms",
    "bytes_received_total",
    "bytes_sent_total",
    "recv_event_count",
    "send_event_count",
    "request_count",
    "error_count",
    "avg_message_interval_ms",
]


@dataclass(frozen=True)
class FeatureVector:
    event_type: str
    names: list[str]
    values: list[float]


def build_feature_vector(payload: InferenceRequest) -> FeatureVector:
    if payload.event_type == "message":
        feature_dict = payload.features.model_dump()
        names = MESSAGE_FEATURE_ORDER
    else:
        feature_dict = payload.features.model_dump()
        names = SESSION_SUMMARY_FEATURE_ORDER

    values = [float(feature_dict[name]) for name in names]
    return FeatureVector(event_type=payload.event_type, names=names, values=values)
