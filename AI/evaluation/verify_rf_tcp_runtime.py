from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = AI_ROOT.parent / "Server"
sys.path[:0] = [str(AI_ROOT / ".deps"), str(AI_ROOT)]

import httpx
import numpy as np
from pydantic import TypeAdapter

from inference_api.approved_bundle import load_serving_config
from inference_api.feature_transformer import build_feature_vector
from inference_api.rf_service import ApprovedRfService
from inference_api.schemas import InferenceRequest


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def frame(text: bytes = b"runtime-check") -> bytes:
    body = struct.pack("!H", len(text)) + text + struct.pack("!i", 42)
    return struct.pack("!ii", 0, 8 + len(body)) + body


def read_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        check(bool(chunk), "TCP 응답이 프레임 완성 전에 끝났습니다.")
        result.extend(chunk)
    return bytes(result)


def read_reply(connection: socket.socket) -> None:
    packet_id, size = struct.unpack("!ii", read_exact(connection, 8))
    check(packet_id == 1 and 10 <= size <= 65536, "TCP 응답 헤더 오류")
    body = read_exact(connection, size - 8)
    length = struct.unpack("!H", body[:2])[0]
    check(body[2:2 + length] == b"Hello my world", "TCP 응답 본문 오류")


@contextmanager
def connection():
    deadline = time.monotonic() + 10
    while True:
        try:
            client = socket.create_connection(("127.0.0.1", 4000), timeout=3)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        client.settimeout(5)
        yield client
    finally:
        try:
            client.shutdown(socket.SHUT_WR)
            while client.recv(4096):
                pass
        except OSError:
            pass
        client.close()


def stop(process: subprocess.Popen, timeout: int = 20) -> float:
    started = time.perf_counter()
    if process.poll() is None:
        try:
            process.communicate("quit\n", timeout=timeout)
        except BaseException:
            # 다른 프로세스는 건드리지 않고 이 검사에서 시작한 자식만 정리한다.
            process.kill()
            process.communicate()
            raise
    check(process.returncode == 0, f"소유한 프로세스 정상 종료 실패: {process.returncode}")
    return (time.perf_counter() - started) * 1000


def percentile(values: list[float]) -> dict:
    check(bool(values), "지연 측정값이 없습니다.")
    return {"count": len(values), "median_ms": float(np.median(values)),
            "p95_ms": float(np.percentile(values, 95)), "max_ms": max(values)}


class RuntimeCheck:
    def __init__(self, client: httpx.Client, exe: Path, directory: Path, environment: dict):
        self.client, self.exe, self.directory, self.environment = client, exe, directory, environment
        self.results: list[dict] = []

    def control(self, mode: str = "normal", delay_ms: int = 0) -> None:
        self.client.post("/__test__/control", json={"mode": mode, "delay_ms": delay_ms}).raise_for_status()

    def records(self) -> list[dict]:
        return self.client.get("/__test__/records").json()["records"]

    def wait(self, predicate, description: str, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while True:
            result = predicate()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(description)
            time.sleep(0.03)

    @contextmanager
    def server(self, name: str, capacity: int = 1024, timeout: int = 1000, drain: int = 2000, rf: bool = True):
        report_path = self.directory / f"{name}.server.json"
        port = self.client.base_url.port
        command = [str(self.exe), "--ai-port", str(port), "--ai-report", str(report_path),
                   "--ai-queue-capacity", str(capacity), "--ai-timeout-ms", str(timeout), "--ai-drain-ms", str(drain)]
        if rf:
            command.append("--ai-rf")
        with (self.directory / f"{name}.server.log").open("x", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=SERVER_ROOT, stdin=subprocess.PIPE, stdout=log,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=self.environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            data = {"name": name, "command": command}
            try:
                yield data
            finally:
                data["process_stop_ms"] = stop(process)
        data["server"] = json.loads(report_path.read_text(encoding="utf-8"))
        self.results.append(data)

    def success(self, service: ApprovedRfService) -> None:
        self.control()
        before = len(self.records())
        rtts = []
        with self.server("actual_rf_messages") as result:
            for fragmented, batched, count in ((False, False, 6), (False, True, 20), (True, False, 2)):
                with connection() as client:
                    if batched:
                        started = time.perf_counter()
                        client.sendall(frame() * count)
                        for _ in range(count):
                            read_reply(client)
                        rtts.append((time.perf_counter() - started) * 1000)
                    else:
                        for _ in range(count):
                            started = time.perf_counter()
                            request = frame()
                            if fragmented:
                                client.sendall(request[:3])
                                time.sleep(0.005)
                                client.sendall(request[3:])
                            else:
                                client.sendall(request)
                            read_reply(client)
                            rtts.append((time.perf_counter() - started) * 1000)
                            time.sleep(0.02)
            records = self.wait(lambda: self.records()[before:] if len(self.records()) >= before + 28 else None, "실제 RF 응답 28개 대기")
            # 마지막 HTTP 전송 완료와 C++ 저장 사이의 짧은 구간은 정상 종료의 큐 배출로 마무리한다.
        report = result["server"]
        check(report["stored"] == 28 and report["sent"] == 28 and report["skipped_summary"] == 3,
              "실제 RF의 저장·요약 수 불일치")
        check(report["failed"] == report["parse_failed"] == report["queue_dropped"] == 0, "정상 연동에서 유실·오류 발생")
        by_session = {}
        for record in records:
            check(record["http_status"] == 200 and record["payload"]["event_type"] == "message", "RF 실제 응답 오류")
            parsed = TypeAdapter(InferenceRequest).validate_python(record["payload"])
            expected = service.predict(parsed, build_feature_vector(parsed)).model_dump(mode="json", exclude_none=True)
            check(record["response"] == expected, "C++가 만든 입력의 직접 RF 계산 불일치")
            by_session[str(record["payload"]["session"]["session_id"])] = record
        for session in report["sessions"]:
            record = by_session[session["session_id"]]
            check(session["covers_latest_message"] and session["closed"], "최신 메시지 저장 또는 세션 종료 실패")
            check(session["response"] == record["response"], "세션에 저장한 응답 불일치")
            check(session["queue_sequence_id"] == record["response"]["request_id"], "응답 식별자 연결 실패")
        result.update(message_count=28, direct_rf_equal=True, tcp_transaction_latency=percentile(rtts),
            api_compute_path_latency=percentile([r["api_elapsed_ms"] for r in records]),
            worker_last_result_http_latency=percentile([s["http_round_trip_ms"] for s in report["sessions"]]))

    def faults(self) -> None:
        # 같은 연결의 첫 성공을 남기고 두 번째 요청만 실패시켜 과거 결과와 최신 판단을 구분한다.
        for mode in ("busy", "bad_json", "wrong_id", "wrong_model", "wrong_policy", "oversized", "delay"):
            self.control()
            before = len(self.records())
            with self.server(f"fault_{mode}", timeout=200) as result:
                with connection() as client:
                    client.sendall(frame())
                    read_reply(client)
                    self.wait(lambda: len(self.records()) >= before + 1, "첫 성공 응답 대기")
                    time.sleep(0.05)
                    self.control(mode, 2500 if mode == "delay" else 0)
                    started = time.perf_counter()
                    client.sendall(frame())
                    read_reply(client)
                    result["fault_tcp_reply_ms"] = (time.perf_counter() - started) * 1000
                    self.wait(lambda: len(self.records()) >= before + 2, "주입한 오류 응답 대기")
            report = result["server"]
            check(report["stored"] == 1 and report["skipped_summary"] == 1, f"{mode}: 실패 응답이 성공을 덮어씀")
            check(report["failed"] + report["parse_failed"] == 1, f"{mode}: 실패 카운터 불일치")
            session = report["sessions"][0]
            check(session["has_successful_result"] and not session["covers_latest_message"] and
                  session["last_success_request_index"] == 1 and session["request_count"] == 2, f"{mode}: 과거 결과를 최신으로 표시함")
            result["old_success_preserved_not_fresh"] = True
        self.control()

    def unavailable(self) -> None:
        # API 목적지만 사용하지 않는 포트로 지정한다. 사용자 서버는 중지하지 않는다.
        actual = self.client
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)
        try:
            with self.server("api_unavailable", timeout=200, drain=300) as result:
                with connection() as client:
                    client.sendall(frame() * 3)
                    for _ in range(3):
                        read_reply(client)
                time.sleep(0.4)
            report = result["server"]
            check(report["stored"] == 0 and report["failed"] > 0, "API 접속 실패가 판단으로 저장됨")
            check(all(not s["has_successful_result"] for s in report["sessions"]), "미판단 세션이 정상으로 저장됨")
        finally:
            self.client.close()
            self.client = actual

    def saturation_and_shutdown(self) -> None:
        self.control("delay", 700)
        with self.server("bounded_queue_shutdown", capacity=4, timeout=1500, drain=100) as result:
            with connection() as client:
                started = time.perf_counter()
                client.sendall(frame() * 100)
                for _ in range(100):
                    read_reply(client)
                result["tcp_100_replies_ms"] = (time.perf_counter() - started) * 1000
                self.wait(lambda: self.client.get("/__test__/records").json()["active"] > 0, "HTTP 진행 중 종료 조건 대기")
        report = result["server"]
        check(report["queue_dropped"] > 0 and report["shutdown_discarded"] > 0 and report["queue_size"] == 0,
              "큐 포화·종료 폐기·배출 검증 실패")
        check(result["process_stop_ms"] < 10000, "큐 개수에 비례한 장시간 종료 발생")
        self.control()
        self.wait(lambda: self.client.get("/__test__/records").json()["active"] == 0, "지연 요청 정리 대기")

    def concurrent_load(self) -> None:
        self.control()
        before = len(self.records())
        rtts = []

        def send_session(_):
            values = []
            with connection() as client:
                for _ in range(15):
                    started = time.perf_counter()
                    client.sendall(frame())
                    read_reply(client)
                    values.append((time.perf_counter() - started) * 1000)
                    time.sleep(0.01)
            return values

        with self.server("concurrent_load") as result:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for values in pool.map(send_session, range(8)):
                    rtts.extend(values)
            self.wait(lambda: len(self.records()) >= before + 120, "동시 8세션의 RF 응답 대기")
        report = result["server"]
        check(report["stored"] == 120 and report["skipped_summary"] == 8 and report["failed"] == report["parse_failed"] == report["queue_dropped"] == 0,
              "8세션 부하의 누락·중복·전송 실패")
        check(len(report["sessions"]) == 8 and all(s["covers_latest_message"] for s in report["sessions"]), "부하 세션 결과 혼합")
        result.update(tcp_transaction_latency=percentile(rtts), message_count=120, concurrent_clients=8)

    def summaries(self) -> None:
        self.control()
        with self.server("compatibility_summary", rf=False) as result:
            with connection() as client:
                client.sendall(frame())
                read_reply(client)
            time.sleep(0.3)
        check(result["server"]["stored"] == 1 and result["server"]["skipped_summary"] == 1 and result["server"]["failed"] == 0,
              "호환 모드의 422 요약 미지원이 전송 장애로 집계됨")


def verify(exe: Path, directory: Path, prefix: Path) -> dict:
    outputs = [prefix.with_suffix(suffix) for suffix in (".json", ".md")]
    check(exe.is_file(), "먼저 C++ Server를 Debug x64로 빌드하세요.")
    check(not directory.exists() and not any(p.exists() for p in outputs), "기존 결과를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
    config = load_serving_config()
    for output in [directory, *outputs]:
        check(not any(output.resolve().is_relative_to(root.resolve()) for root in (AI_ROOT / "data", config.evaluation_root)), "원본·승인 경로에는 기록할 수 없습니다.")
    service = ApprovedRfService(config)
    sources = [Path(__file__), AI_ROOT / "tests" / "rf_runtime_api.py", exe,
               SERVER_ROOT / "Server" / "main.cpp", SERVER_ROOT / "run_server.bat",
               SERVER_ROOT / "FeatureExtraction" / "AiRuntimeSupport.h"]
    sources += [SERVER_ROOT / "FeatureExtraction" / name for name in ("AiInferenceWorker.h", "AiInferenceWorker.cpp", "InferenceResult.h",
                                                                    "FeatureCollector.cpp", "AsyncFeatureQueue.cpp")]
    sources += [AI_ROOT / "inference_api" / name for name in ("main.py", "rf_main.py", "rf_service.py", "approved_bundle.py", "schemas.py")]
    sources += [SERVER_ROOT / "Server" / "Server.vcxproj", SERVER_ROOT / "ServerBase" / "ServerBase.vcxproj",
                SERVER_ROOT / "tests" / "AiRuntimeTests.cpp", SERVER_ROOT / "tests" / "AiRuntimeTests.vcxproj",
                AI_ROOT / "tests" / "test_rf_tcp_runtime.py", AI_ROOT / "configs" / "operational_rf_serving.json",
                AI_ROOT / "requirements-inference.txt"]
    protected = {**service.bundle.evidence, **{str(p.resolve()): digest(p) for p in sources}}
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", 4000))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    directory.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.pop("NDA_COLLECTION_CONFIG", None)
    environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")})
    environment["PYTHONIOENCODING"] = "utf-8"
    started = time.perf_counter()
    try:
        with (directory / "api.log").open("x", encoding="utf-8") as log:
            api = subprocess.Popen([sys.executable, "-X", "utf8", str(AI_ROOT / "tests" / "rf_runtime_api.py"), "--port", str(port)],
                cwd=AI_ROOT, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
                    checks = RuntimeCheck(client, exe, directory, environment)

                    def ready():
                        check(api.poll() is None, "RF 검사 API 기동 실패")
                        try:
                            response = client.get("/health")
                            return response.json() if response.status_code == 200 else None
                        except httpx.TransportError:
                            return None

                    health = checks.wait(ready, "실제 RF API 준비 대기", timeout=30)
                    check(health["model_kind"] == "random_forest" and health["decision_threshold"] == 0.7, "dummy 또는 다른 정책 실행")
                    checks.success(service)
                    print("PASS: 실제 TCP feature 28개·RF 직접 계산·세션 결과 저장", flush=True)
                    checks.faults()
                    print("PASS: 7개 장애·잘못된 응답·마지막 성공의 비최신 상태", flush=True)
                    checks.unavailable()
                    checks.saturation_and_shutdown()
                    checks.concurrent_load()
                    checks.summaries()
                    records = checks.records()
                    check(client.get("/collection/status").json() == {"enabled": False, "state": "disabled"}, "의도치 않은 학습 데이터 수집")
            finally:
                api_stop_ms = stop(api)
        check(all(digest(Path(path)) == value for path, value in protected.items()), "검사 중 승인·소스·실행 파일 변경")
        write_json(directory / "http_records.json", {"records": records})
        report = {"status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(), "health": health,
                  "scenario_count": len(checks.results), "scenarios": checks.results, "api_graceful_shutdown": True,
                  "api_stop_ms": api_stop_ms, "elapsed_seconds": time.perf_counter() - started,
                  "protected_sha256": protected, "training_performed": False, "dataset_files_read": False,
                  "test_reselected": False, "automatic_blocking": False, "remaining_major_stages": 1,
                  "limitations": ["로컬 기능·유한 부하 검사이며 새로운 정확도 평가·운영 SLA·장시간 안정성 증명이 아니다.",
                                  "성공 경로는 실제 승인 RF이고 장애 응답·지연은 검사 전용 앱에서 주입했다.",
                                  "TCP 왕복에는 RF 완료를 기다리는 시간이 포함되지 않는다. API·worker HTTP 지연과 분리한다.",
                                  "종료 예산은 대기 큐 배출 기준이다. 진행 중 WinHTTP의 단계별 timeout까지 추가로 기다릴 수 있다.",
                                  "세션·결과 저장소의 보존 상한·TTL, 실행 중 관리자 조회 API, 자동 재시도는 구현하지 않았다.",
                                  "종료 시 열린 클라이언트 연결의 모두 닫힘은 별도 보장이 아니다. 검사는 연결을 닫고 worker 진행·큐 적체 상태에서 종료했다."]}
        outputs[0].parent.mkdir(parents=True, exist_ok=True)
        write_json(outputs[0], report)
        lines = ["# 실제 RF와 C++ TCP 종단 검증", "", "기존 모델·threshold·Test를 바꾸지 않고 로컬 실제 서버 경로를 검사했다.", "",
                 "| 시나리오 | 저장 | 실패 | 파싱 거부 | 요약 제외 | 큐 유실 | 종료 폐기 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for result in checks.results:
            r = result["server"]
            lines.append(f"| {result['name']} | {r['stored']} | {r['failed']} | {r['parse_failed']} | {r['skipped_summary']} | {r['queue_dropped']} | {r['shutdown_discarded']} |")
        lines += ["", "## 범위와 한계", "", *report["limitations"], "", "## 다음 작업", "",
                  "남은 큰 단계는 1개다. 재현 절차·실험 보고서·GitHub README·면접 설명을 최종 정리한다.", ""]
        with outputs[1].open("x", encoding="utf-8") as stream:
            stream.write("\n".join(lines))
        files = list(directory.glob("*.json")) + list(directory.glob("*.log")) + outputs
        write_json(directory / "verification_manifest.json", {"status": "complete", "sha256": {str(p): digest(p) for p in files}})
        return report
    except BaseException as error:
        write_json(directory / "verification_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 RF와 C++ TCP 종단·장애·부하·종료를 로컬에서 검증합니다.")
    parser.add_argument("--server-exe", type=Path, default=SERVER_ROOT / "BIN" / "Debug" / "Server.exe")
    parser.add_argument("--artifact-dir", type=Path, default=AI_ROOT / "artifacts" / "inference_api" / "rf_v1" / "tcp_runtime_v2")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_rf_tcp_runtime_v2")
    args = parser.parse_args()
    result = verify(args.server_exe.resolve(), args.artifact_dir.resolve(), args.report_prefix.resolve())
    print(f"[RF TCP] 종단 시나리오 {result['scenario_count']}개 완료. 남은 큰 단계 1개.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
