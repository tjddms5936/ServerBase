from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = AI_ROOT.parent / "Server"
REPORT_PATH = AI_ROOT / "reports" / "runtime_hardening_e2e.json"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def get_json(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=2) as response:
        return json.load(response)


def read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError("응답 프레임이 완성되기 전에 연결이 닫혔습니다.")
        chunks.extend(chunk)
    return bytes(chunks)


def run_client(frame: bytes, normal: bool) -> None:
    deadline = time.monotonic() + 15
    while True:
        try:
            connection = socket.create_connection(("127.0.0.1", 4000), timeout=2)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    with connection:
        connection.settimeout(5)
        connection.sendall(frame)
        if normal:
            packet_id, size = struct.unpack("!ii", read_exact(connection, 8))
            check(packet_id == 1 and 8 <= size <= 65536, "정상 응답 헤더가 다릅니다.")
            payload = read_exact(connection, size - 8)
            text_size = struct.unpack("!H", payload[:2])[0]
            check(payload[2:2 + text_size] == b"Hello my world", "정상 응답 본문이 다릅니다.")
            connection.shutdown(socket.SHUT_WR)
        try:
            check(connection.recv(1) == b"", "종료 대신 추가 데이터가 왔습니다.")
        except ConnectionResetError:
            check(not normal, "정상 요청에서 연결이 비정상 종료됐습니다.")


def stop_owned_process(process: subprocess.Popen | None, graceful: bool) -> None:
    if process is None or process.poll() is not None:
        return
    if graceful and process.stdin:
        try:
            process.stdin.write(b"quit\n")
            process.stdin.flush()
            process.wait(timeout=15)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    # 정리 대상은 이 테스트가 직접 시작한 프로세스로 제한한다.
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 C++ 서버와 FastAPI의 v2 입력/오류 경로를 검증합니다.")
    parser.add_argument("--server-exe", type=Path, default=SERVER_ROOT / "BIN" / "Debug" / "Server.exe")
    args = parser.parse_args()
    check(args.server_exe.is_file(), "Server.exe가 없습니다. 먼저 Debug x64로 빌드하세요.")
    for port in [4000, 8000]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    text = b"runtime-check"
    body = struct.pack("!H", len(text)) + text + struct.pack("!i", 42)
    frame = lambda packet_id, payload: struct.pack("!ii", packet_id, 8 + len(payload)) + payload
    cases = [
        ("normal_chat", frame(0, body), 1, None),
        ("malformed_chat_payload", frame(0, b"\x00"), 1, "failed to deserialize CS_CHAT payload"),
        ("trailing_chat_payload", frame(0, body + b"x"), 1, "CS_CHAT payload has trailing bytes"),
        ("unknown_id_empty_payload", frame(999, b""), 1, "unknown packet id"),
        ("unknown_id_with_payload", frame(999, b"x"), 1, "unknown packet id"),
        ("invalid_frame_size", struct.pack("!ii", 0, 7), 0, "invalid packet size"),
        ("oversized_frame", struct.pack("!ii", 0, 2**31 - 1), 0, "packet size exceeds receive buffer capacity"),
        ("id_enum_truncation", frame(65536, b""), 0, "packet id out of range"),
    ]
    environment = os.environ.copy()
    # 여러 시나리오를 섞는 기존 검사는 수집 실행으로 잘못 분류하지 않는다.
    environment.pop("NDA_COLLECTION_CONFIG", None)
    environment["PYTHONPATH"] = os.pathsep.join([str(AI_ROOT / ".deps"), str(AI_ROOT), str(AI_ROOT / "tests")])
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    api_process = server_process = None
    results = []

    with tempfile.TemporaryDirectory(prefix="network_runtime_e2e_") as directory:
        api_log = Path(directory) / "api.log"
        server_log = Path(directory) / "server.log"
        with api_log.open("wb") as api_output, server_log.open("wb") as server_output:
            try:
                api_process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "e2e_api:app", "--host", "127.0.0.1", "--port", "8000"],
                    cwd=AI_ROOT, env=environment, stdout=api_output, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, creationflags=creationflags,
                )
                deadline = time.monotonic() + 15
                while True:
                    try:
                        health = get_json("/health")
                        check(health["feature_contract_ids"]["message"].endswith("_v2"), "API 의미 버전이 다릅니다.")
                        break
                    except (OSError, urllib.error.URLError):
                        check(api_process.poll() is None and time.monotonic() < deadline, "FastAPI 시작 실패")
                        time.sleep(0.1)
                server_process = subprocess.Popen(
                    [str(args.server_exe.resolve())], cwd=SERVER_ROOT, stdout=server_output,
                    stderr=subprocess.STDOUT, stdin=subprocess.PIPE, creationflags=creationflags,
                )
                for name, request, parsed_count, expected_error in cases:
                    before = len(get_json("/__test__/records"))
                    run_client(request, normal=expected_error is None)
                    deadline = time.monotonic() + 15
                    while True:
                        received = get_json("/__test__/records")[before:]
                        summaries = [record for record in received if record["payload"]["event_type"] == "session_summary"]
                        if summaries:
                            break
                        check(server_process.poll() is None and time.monotonic() < deadline, f"{name}: 종료 요약 전송 실패")
                        time.sleep(0.05)
                    messages = [record for record in received if record["payload"]["event_type"] == "message"]
                    check(len(summaries) == 1 and len(messages) == parsed_count, f"{name}: snapshot 수가 다릅니다.")
                    summary = summaries[0]["payload"]
                    check(summary["features"]["request_count"] == parsed_count, f"{name}: request_count 오류")
                    check(summary["features"]["error_count"] == int(expected_error is not None), f"{name}: 오류 누락 또는 중복")
                    check(summary["session"]["last_error_message"] == (expected_error or ""), f"{name}: 오류 설명 불일치")
                    check(summary["feature_contract_id"] == "server_application_session_summary_v2", f"{name}: summary 버전 오류")
                    for record in received:
                        payload, response = record["payload"], record["response"]
                        check(payload["session"]["session_id"] == summary["session"]["session_id"], f"{name}: 세션 혼합")
                        check(response["request_id"] == str(payload["queue_sequence_id"]), f"{name}: 응답 식별자 불일치")
                        check(not response["is_attack"] and response["model_version"] == "dummy-v0", "dummy 응답 변경")
                    for message in messages:
                        check(message["payload"]["features"]["error_count_total"] == 0, f"{name}: 미래 오류가 메시지에 반영됨")
                        check(message["payload"]["feature_contract_id"] == "server_application_message_v2", f"{name}: message 버전 오류")
                    results.append({"scenario": name, "passed": True, "records": received})
                    print(f"PASS: {name}")
                stop_owned_process(server_process, graceful=True)
                check(server_process.returncode == 0, "C++ 서버의 graceful shutdown 실패")
            except Exception:
                api_output.flush()
                server_output.flush()
                print(api_log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
                print(server_log.read_text(encoding="utf-8", errors="replace")[-6000:], file=sys.stderr)
                raise
            finally:
                stop_owned_process(server_process, graceful=True)
                stop_owned_process(api_process, graceful=False)
                if server_process and server_process.stdin:
                    server_process.stdin.close()

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(), "actual_model_inference": False,
        "training_dataset": False, "scenario_count": len(results), "scenarios": results,
        "server_executable_sha256": hashlib.sha256(args.server_exe.read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256((AI_ROOT / "configs" / "operational_feature_schema.json").read_bytes()).hexdigest(),
        "server_graceful_shutdown": True,
        "limitations": ["즉시 I/O 요청 실패는 이 연동 테스트에서 강제로 유발하지 않았다.", "종료 이후 이벤트 고정 및 동시성은 별도 C++ 단위 테스트로 확인한다."],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"보고서: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
