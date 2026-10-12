from __future__ import annotations

import hashlib
import random
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .config import StrictModel


GENERATOR_VERSION = "application_traffic_v1"
TCP_ADDRESS = ("127.0.0.1", 4000)
REPLY_TEXT = b"Hello my world"
SCENARIO_LABELS = {
    "normal_chat": 0, "normal_fragmented_chat": 0, "normal_batched_chat": 0,
    "controlled_burst_chat": 1, "malformed_chat_payload": 1,
    "malformed_length_chat": 1, "invalid_frame_size": 1,
}
ERROR_MESSAGES = {
    "malformed_chat_payload": "failed to deserialize CS_CHAT payload",
    "malformed_length_chat": "failed to deserialize CS_CHAT payload",
    "invalid_frame_size": "invalid packet size",
}


class TrafficProfile(StrictModel):
    scenario_id: Literal[
        "normal_chat", "normal_fragmented_chat", "normal_batched_chat", "controlled_burst_chat",
        "malformed_chat_payload", "malformed_length_chat", "invalid_frame_size",
    ]
    sessions: Annotated[int, Field(ge=1, le=16, strict=True)] = 4
    concurrency: Annotated[int, Field(ge=1, le=4, strict=True)] = 2
    messages_min: Annotated[int, Field(ge=1, le=32, strict=True)] = 4
    messages_max: Annotated[int, Field(ge=1, le=32, strict=True)] = 8
    text_sizes: Annotated[list[Annotated[int, Field(ge=1, le=1024, strict=True)]], Field(min_length=1, max_length=8)] = [16, 64, 256, 512]
    interval_min_ms: Annotated[int, Field(ge=0, le=100, strict=True)] = 10
    interval_max_ms: Annotated[int, Field(ge=0, le=100, strict=True)] = 30

    @model_validator(mode="after")
    def validate_ranges(self) -> TrafficProfile:
        if self.messages_min > self.messages_max or self.interval_min_ms > self.interval_max_ms:
            raise ValueError("최솟값은 최댓값보다 클 수 없습니다.")
        if self.concurrency > self.sessions:
            raise ValueError("동시 연결 수는 전체 세션 수보다 클 수 없습니다.")
        if self.scenario_id == "controlled_burst_chat" and (
            self.messages_min < 12 or self.interval_min_ms != 0 or self.interval_max_ms != 0
        ):
            raise ValueError("통제된 연속 요청은 최소 12개, 묶음 사이 지정 대기 0ms로 정의합니다.")
        return self


class CampaignPlan(StrictModel):
    plan_version: Literal["v1"]
    seed: Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]
    repeats: Annotated[int, Field(ge=1, le=10, strict=True)]
    profiles: Annotated[list[TrafficProfile], Field(min_length=1, max_length=7)]

    @model_validator(mode="after")
    def validate_scenarios(self) -> CampaignPlan:
        ids = [profile.scenario_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("수집 계획의 시나리오가 중복되었습니다.")
        return self


def chat_frame(text: bytes, value: int = 42) -> bytes:
    if len(text) > 1024:
        raise ValueError("이 생성기의 문자열 크기 제한을 초과했습니다.")
    body = struct.pack("!H", len(text)) + text + struct.pack("!i", value)
    return struct.pack("!ii", 0, 8 + len(body)) + body


def build_expectations(profile: TrafficProfile, seed: int, run_id: str, label: int) -> dict:
    if label != SCENARIO_LABELS[profile.scenario_id]:
        raise ValueError("시나리오 manifest의 정답이 생성기의 통제된 시나리오 정의와 다릅니다.")
    sessions = []
    for index in range(profile.sessions):
        # 연결 스케줄과 무관하게 각 세션의 난수 시드를 분리한다.
        rng = random.Random(seed * 1009 + index)
        count = rng.randint(profile.messages_min, profile.messages_max)
        if profile.scenario_id in ERROR_MESSAGES:
            count = 1
        frames = []
        for _ in range(count):
            text_size = rng.choice(profile.text_sizes)
            text = bytes(rng.choice(b"abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(text_size))
            frame = chat_frame(text, rng.randint(-1000, 1000))
            if profile.scenario_id == "malformed_chat_payload":
                frame = struct.pack("!ii", 0, 9) + b"\x00"
            elif profile.scenario_id == "malformed_length_chat":
                # 전체 프레임 크기는 정상과 겹치지만 문자열 길이 필드만 본문보다 크게 만든다.
                frame = frame[:8] + struct.pack("!H", 60000) + frame[10:]
            elif profile.scenario_id == "invalid_frame_size":
                frame = struct.pack("!ii", 0, 7)
            frames.append({
                "wire_hex": frame.hex(), "wire_size": len(frame),
                "wire_sha256": hashlib.sha256(frame).hexdigest(),
                "interval_after_ms": rng.randint(profile.interval_min_ms, profile.interval_max_ms),
            })
        parsed = [] if profile.scenario_id == "invalid_frame_size" else [
            {"packet_id": 0, "packet_size": frame["wire_size"]} for frame in frames
        ]
        sessions.append({
            "client_index": index, "frames": frames, "expected_message_frames": parsed,
            "expected_responses": 0 if profile.scenario_id in ERROR_MESSAGES else len(frames),
            "expected_error_count": int(profile.scenario_id in ERROR_MESSAGES),
            "expected_last_error_message": ERROR_MESSAGES.get(profile.scenario_id, ""),
        })
    expected_messages = sum(len(session["expected_message_frames"]) for session in sessions)
    return {
        "expectation_version": "v1", "generator_version": GENERATOR_VERSION,
        "run_id": run_id, "scenario_id": profile.scenario_id, "independent_label": label,
        "seed": seed, "profile": profile.model_dump(mode="json"), "sessions": sessions,
        "expected_sessions": len(sessions), "expected_messages": expected_messages,
        "expected_summaries": len(sessions), "expected_records": expected_messages + len(sessions),
        "message_observable": profile.scenario_id != "invalid_frame_size",
    }


def read_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            raise RuntimeError("응답을 모두 받기 전에 연결이 종료되었습니다.")
        result.extend(part)
    return bytes(result)


def read_reply(connection: socket.socket) -> None:
    packet_id, size = struct.unpack("!ii", read_exact(connection, 8))
    if packet_id != 1 or not 10 <= size <= 2048:
        raise RuntimeError("서버 응답 헤더가 예상한 범위와 다릅니다.")
    body = read_exact(connection, size - 8)
    text_size = struct.unpack("!H", body[:2])[0]
    if text_size != len(REPLY_TEXT) or body[2:] != REPLY_TEXT:
        raise RuntimeError("서버 응답 본문 또는 길이가 다릅니다.")


def connect_local() -> socket.socket:
    deadline = time.monotonic() + 15
    while True:
        try:
            connection = socket.create_connection(TCP_ADDRESS, timeout=2)
            try:
                connection.settimeout(5)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                connection.close()
                raise
            return connection
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def run_session(session: dict, scenario_id: str) -> dict:
    result = {
        "client_index": session["client_index"], "passed": False,
        "connected": False, "frames_sent": 0, "bytes_sent": 0, "responses_received": 0,
        "started_at_utc": datetime.now(UTC).isoformat(), "error": None,
    }
    try:
        with connect_local() as connection:
            result["connected"] = True
            frames = session["frames"]
            batch_size = 4 if scenario_id in {"normal_batched_chat", "controlled_burst_chat"} else 1
            for start in range(0, len(frames), batch_size):
                group = frames[start:start + batch_size]
                wire = b"".join(bytes.fromhex(frame["wire_hex"]) for frame in group)
                if scenario_id == "normal_fragmented_chat":
                    # 분할 송신은 정상 TCP 동작이다. 실제 수신 완료 경계는 OS가 결정한다.
                    for offset in range(0, len(wire), 31):
                        connection.sendall(wire[offset:offset + 31])
                        time.sleep(0.001)
                else:
                    connection.sendall(wire)
                result["frames_sent"] += len(group)
                result["bytes_sent"] += len(wire)
                if session["expected_responses"]:
                    for _ in group:
                        read_reply(connection)
                        result["responses_received"] += 1
                if start + len(group) < len(frames):
                    time.sleep(group[-1]["interval_after_ms"] / 1000)
            if session["expected_responses"]:
                connection.shutdown(socket.SHUT_WR)
            try:
                if connection.recv(1) != b"":
                    raise RuntimeError("종료 시 예상하지 않은 추가 응답이 왔습니다.")
            except ConnectionResetError:
                if not session["expected_error_count"]:
                    raise
        if result["responses_received"] != session["expected_responses"]:
            raise RuntimeError("실제 응답 수가 사전 기대 수와 다릅니다.")
        result["passed"] = True
    except Exception as error:
        # 실패한 연결도 기록한다. 실패 사례를 빼고 수집 성공률을 높여 보이지 않는다.
        result["error"] = f"{type(error).__name__}: {error}"
    result["finished_at_utc"] = datetime.now(UTC).isoformat()
    return result


def generate_traffic(expectations: dict) -> dict:
    profile = TrafficProfile.model_validate(expectations["profile"])
    with ThreadPoolExecutor(max_workers=profile.concurrency) as workers:
        results = list(workers.map(
            lambda session: run_session(session, profile.scenario_id), expectations["sessions"],
        ))
    return {
        "outcome_version": "v1", "run_id": expectations["run_id"], "scenario_id": profile.scenario_id,
        "passed": all(result["passed"] for result in results), "sessions": results,
        "connected_sessions": sum(result["connected"] for result in results),
        "frames_sent": sum(result["frames_sent"] for result in results),
        "responses_received": sum(result["responses_received"] for result in results),
    }
