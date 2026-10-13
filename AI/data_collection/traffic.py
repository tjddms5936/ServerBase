from __future__ import annotations

import hashlib
import random
import socket
import struct
import time
import math
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
    batch_size: Annotated[int, Field(ge=1, le=8, strict=True)] | None = None
    fragment_size_bytes: Annotated[int, Field(ge=1, le=256, strict=True)] | None = None

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
    plan_version: Literal["v1", "v2"]
    seed: Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]
    repeats: Annotated[int, Field(ge=1, le=10, strict=True)]
    profiles: Annotated[list[TrafficProfile], Field(min_length=1, max_length=7)]
    repeat_profiles: list[list[TrafficProfile]] | None = None

    @model_validator(mode="after")
    def validate_scenarios(self) -> CampaignPlan:
        ids = [profile.scenario_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("수집 계획의 시나리오가 중복되었습니다.")
        groups = self.repeat_profiles if self.repeat_profiles is not None else [self.profiles]
        if self.repeat_profiles is not None and (len(groups) != self.repeats or groups[0] != self.profiles):
            raise ValueError("반복별 프로필 수와 첫 반복 프로필이 계획과 다릅니다.")
        for profiles in groups:
            names = [profile.scenario_id for profile in profiles]
            if len(names) != len(set(names)) or set(names) != set(ids):
                raise ValueError("모든 반복은 중복 없이 같은 시나리오 집합을 포함해야 합니다.")
            if self.plan_version == "v1" and any(profile.batch_size is not None or profile.fragment_size_bytes is not None for profile in profiles):
                raise ValueError("명시적 송신 제어에는 v2 계획이 필요합니다.")
            if self.plan_version == "v2" and any(profile.batch_size is None for profile in profiles):
                raise ValueError("v2는 모든 프로필의 묶음 크기를 명시해야 합니다.")
        if self.plan_version == "v1" and self.repeat_profiles is not None:
            raise ValueError("반복별 교차 배치는 v2 계획에서 사용합니다.")
        return self

    def profiles_for_repeat(self, repeat: int) -> list[TrafficProfile]:
        if not 0 <= repeat < self.repeats:
            raise ValueError("계획 범위 밖의 반복 번호입니다.")
        return self.repeat_profiles[repeat] if self.repeat_profiles is not None else self.profiles


def planned_runs(plan: CampaignPlan):
    for repeat in range(plan.repeats):
        for index, profile in enumerate(plan.profiles_for_repeat(repeat)):
            if plan.plan_version == "v1":
                seed = plan.seed + repeat * 100 + index
            else:
                # 실행 순서를 바꿔도 같은 캠페인·반복·시나리오의 난수 계열은 유지한다.
                encoded = f"{plan.seed}:{repeat}:{profile.scenario_id}".encode("ascii")
                seed = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") % (2**31)
            yield repeat, profile, seed


def transport_controls(profile: TrafficProfile) -> dict:
    return {
        "batch_size": profile.batch_size if profile.batch_size is not None else
                      (4 if profile.scenario_id in {"normal_batched_chat", "controlled_burst_chat"} else 1),
        "fragment_size_bytes": (profile.fragment_size_bytes or 31) if profile.scenario_id == "normal_fragmented_chat" else None,
        "fragment_wait_ms": 1 if profile.scenario_id == "normal_fragmented_chat" else 0,
    }


def expected_send_calls(session: dict, controls: dict) -> int:
    frames = session["frames"]
    return sum(math.ceil(sum(frame["wire_size"] for frame in frames[start:start + controls["batch_size"]]) /
                         controls["fragment_size_bytes"]) if controls["fragment_size_bytes"] else 1
               for start in range(0, len(frames), controls["batch_size"]))


def planned_send_groups(session: dict, controls: dict) -> list[dict]:
    frames = session["frames"]
    return [{"first_frame_index": start + 1, "frame_count": len(frames[start:start + controls["batch_size"]]),
             "wire_size": sum(frame["wire_size"] for frame in frames[start:start + controls["batch_size"]]),
             "wait_after_ms": frames[min(start + controls["batch_size"], len(frames)) - 1]["interval_after_ms"]
             if start + controls["batch_size"] < len(frames) else 0}
            for start in range(0, len(frames), controls["batch_size"])]


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
    explicit = profile.batch_size is not None or profile.fragment_size_bytes is not None
    if explicit:
        for session in sessions:
            session["transport_controls"] = transport_controls(profile)
            session["expected_sendall_calls"] = expected_send_calls(session, session["transport_controls"])
    return {
        "expectation_version": "v2" if explicit else "v1",
        "generator_version": "application_traffic_v2" if explicit else GENERATOR_VERSION,
        "run_id": run_id, "scenario_id": profile.scenario_id, "independent_label": label,
        "seed": seed, "profile": profile.model_dump(mode="json", exclude_none=True), "sessions": sessions,
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


def run_session(session: dict, scenario_id: str, profile: TrafficProfile | None = None) -> dict:
    profile = profile or TrafficProfile(scenario_id=scenario_id, **(
        {"messages_min": 12, "messages_max": 16, "interval_min_ms": 0, "interval_max_ms": 0}
        if scenario_id == "controlled_burst_chat" else {}))
    controls = transport_controls(profile)
    result = {
        "client_index": session["client_index"], "passed": False,
        "connected": False, "frames_sent": 0, "bytes_sent": 0, "responses_received": 0,
        "started_at_utc": datetime.now(UTC).isoformat(), "error": None,
        "transport_controls": controls, "sendall_calls": 0, "send_groups": [],
    }
    try:
        with connect_local() as connection:
            result["connected"] = True
            frames = session["frames"]
            batch_size = controls["batch_size"]
            previous_group_start = None
            for start in range(0, len(frames), batch_size):
                group = frames[start:start + batch_size]
                wire = b"".join(bytes.fromhex(frame["wire_hex"]) for frame in group)
                group_start = time.perf_counter()
                group_record = {
                    "first_frame_index": start + 1, "frame_count": len(group), "wire_size": len(wire),
                    "wait_after_ms": group[-1]["interval_after_ms"] if start + len(group) < len(frames) else 0,
                    "start_interval_ms": (group_start - previous_group_start) * 1000 if previous_group_start is not None else 0,
                }
                previous_group_start = group_start
                if scenario_id == "normal_fragmented_chat":
                    # 분할 송신은 정상 TCP 동작이다. 실제 수신 완료 경계는 OS가 결정한다.
                    fragment_size = controls["fragment_size_bytes"]
                    for offset in range(0, len(wire), fragment_size):
                        connection.sendall(wire[offset:offset + fragment_size])
                        result["sendall_calls"] += 1
                        time.sleep(0.001)
                else:
                    connection.sendall(wire)
                    result["sendall_calls"] += 1
                group_record["send_duration_ms"] = (time.perf_counter() - group_start) * 1000
                result["send_groups"].append(group_record)
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
            lambda session: run_session(session, profile.scenario_id, profile), expectations["sessions"],
        ))
    return {
        "outcome_version": expectations["expectation_version"], "run_id": expectations["run_id"], "scenario_id": profile.scenario_id,
        "passed": all(result["passed"] for result in results), "sessions": results,
        "connected_sessions": sum(result["connected"] for result in results),
        "frames_sent": sum(result["frames_sent"] for result in results),
        "responses_received": sum(result["responses_received"] for result in results),
    }
