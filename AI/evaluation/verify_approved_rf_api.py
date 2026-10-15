from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import httpx
from pydantic import TypeAdapter

from inference_api.approved_bundle import DEFAULT_CONFIG, load_serving_config
from inference_api.feature_transformer import build_feature_vector, MESSAGE_FEATURE_ORDER
from inference_api.rf_service import ApprovedRfService
from inference_api.schemas import InferenceRequest


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def message_payload(sequence: int, values: list, request_index: int = 3) -> dict:
    features = dict(zip(MESSAGE_FEATURE_ORDER, values))
    return {"schema_version": "v1", "event_type": "message", "feature_contract_id": "server_application_message_v2",
        "queue_sequence_id": sequence, "timestamp": "2026-10-15T00:00:00Z",
        "session": {"session_id": sequence, "socket_handle": 100, "logic_worker_id": 0,
                    **{name: features[name] for name in ("connection_duration_ms", "bytes_received_total", "bytes_sent_total", "error_count_total")}},
        "message": {"request_index": request_index, "packet_id": 1,
                    **{name: features[name] for name in ("packet_size", "payload_size", "message_interval_ms")}}, "features": features}


def summary_payload() -> dict:
    features = {"connection_duration_ms": 200.0, "bytes_received_total": 80, "bytes_sent_total": 40,
                "recv_event_count": 2, "send_event_count": 1, "request_count": 3, "error_count": 0, "avg_message_interval_ms": 15.0}
    return {"schema_version": "v1", "event_type": "session_summary", "feature_contract_id": "server_application_session_summary_v2",
        "queue_sequence_id": 104, "timestamp": "2026-10-15T00:00:01Z", "features": features,
        "session": {"session_id": 101, "socket_handle": 100, "logic_worker_id": 0, "is_started": True, "is_closed": True,
                    "last_error_code": 0, "last_error_message": "", **features}}


def verify(config_path: Path, directory: Path, report_prefix: Path) -> dict:
    outputs = [report_prefix.with_suffix(extension) for extension in (".json", ".md")]
    if directory.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 API 검사 결과는 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    config = load_serving_config(config_path)
    if any(path.resolve().is_relative_to(root.resolve()) for path in [directory, *outputs]
           for root in (config.evaluation_root, AI_ROOT / "data")):
        raise ValueError("승인 산출물·데이터셋 안에는 API 검사 결과를 저장하지 않습니다.")
    service = ApprovedRfService(config)
    sources = [config_path, Path(__file__), AI_ROOT / "scripts" / "run_inference_api.py", AI_ROOT / "requirements-inference.txt"]
    sources += [AI_ROOT / "inference_api" / name for name in ("approved_bundle.py", "rf_service.py", "rf_main.py", "main.py",
                                                              "schemas.py", "feature_transformer.py", "payload_validation.py")]
    protected = {**service.bundle.evidence, **{str(path.resolve()): file_hash(path) for path in sources}}
    directory.mkdir(parents=True, exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [sys.executable, "-X", "utf8", str(AI_ROOT / "scripts" / "run_inference_api.py"),
               "--config", str(config_path.resolve()), "--port", str(port), "--stdin-control"]
    environment = os.environ.copy()
    environment.pop("NDA_COLLECTION_CONFIG", None)
    environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")})
    checks, shutdown = [], False
    started = time.monotonic()
    try:
        with (directory / "server.log").open("x", encoding="utf-8") as log:
            child = subprocess.Popen(command, cwd=AI_ROOT, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        if child.poll() is not None:
                            raise RuntimeError("RF API가 준비 전에 종료됐습니다. server.log를 확인하세요.")
                        try:
                            response = client.get("/health")
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() >= deadline:
                            raise TimeoutError("RF API 준비 제한 시간을 초과했습니다.")
                        time.sleep(0.1)
                    health = response.json()
                    if (not health["ready"] or health["model_kind"] != "random_forest" or health["decision_threshold"] != 0.7 or
                            health["supported_event_types"] != ["message"] or health["model_version"] != config.model_version):
                        raise ValueError("실제 RF API의 준비 상태·모델 정보가 다릅니다.")
                    for name, payload in (
                        ("paced_snapshot", message_payload(101, [24, 16, 200.0, 15.0, 80, 40, 0])),
                        ("rapid_snapshot", message_payload(102, [24, 16, 1.0, 0.0, 480, 40, 0])),
                        ("first_snapshot", message_payload(103, [24, 16, 0.0, 0.0, 24, 0, 0], request_index=1)),
                    ):
                        parsed = TypeAdapter(InferenceRequest).validate_python(payload)
                        expected = service.predict(parsed, build_feature_vector(parsed)).model_dump(mode="json", exclude_none=True)
                        result = client.post("/predict", json=payload)
                        if result.status_code != 200 or result.json() != expected:
                            raise ValueError(f"직접 모델과 실제 HTTP 응답이 다릅니다: {name}")
                        checks.append({"name": name, "request": payload, "status_code": result.status_code, "response": result.json(), "direct_model_equal": True})
                    payload = summary_payload()
                    result = client.post("/predict", json=payload)
                    if result.status_code != 422 or result.json()["detail"]["code"] != "unsupported_event_type":
                        raise ValueError("요약 요청이 명시적으로 미지원 처리되지 않았습니다.")
                    checks.append({"name": "unsupported_summary", "status_code": result.status_code, "response": result.json()})
                    payload = message_payload(105, [24, 16, 200.0, 15.0, 80, 40, 0])
                    payload["features"]["packet_size"] = "24"
                    result = client.post("/predict", json=payload)
                    if result.status_code != 422:
                        raise ValueError("숫자 문자열 입력을 거부하지 않았습니다.")
                    checks.append({"name": "invalid_input", "status_code": result.status_code})
                    if client.get("/collection/status").json() != {"enabled": False, "state": "disabled"}:
                        raise ValueError("API 검사 중 원본 수집이 의도치 않게 활성화됐습니다.")
            finally:
                if child.poll() is None:
                    try:
                        child.communicate("quit\n", timeout=20)
                        shutdown = child.returncode == 0
                    except (subprocess.TimeoutExpired, OSError):
                        child.kill()
                        child.communicate()
                if child.stdin is not None:
                    child.stdin.close()
        if not shutdown:
            raise RuntimeError("소유한 API 프로세스가 정상 종료되지 않았습니다.")
        if any(file_hash(Path(path)) != expected for path, expected in protected.items()):
            raise ValueError("API 검사 중 승인 파일·구현 파일이 변경됐습니다.")
        report = {"status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(), "model_version": config.model_version,
            "health": health, "checks": checks, "graceful_shutdown": shutdown, "process_exit_code": child.returncode,
            "local_http_port": port, "elapsed_seconds": time.monotonic() - started, "protected_sha256": protected,
            "server_log_sha256": file_hash(directory / "server.log"), "dataset_files_read": False, "test_reselected": False,
            "model_training_performed": False, "cpp_tcp_e2e_tested": False, "remaining_major_stages": 2,
            "limitations": ["수작업 snapshot의 HTTP 기능 검사이며 새 데이터셋 성능이나 독립 Test 평가가 아니다.",
                            "C++ 송수신·큐·결과 저장·부하·장애는 다음 단계에서 별도로 검증한다.",
                            "승인은 로컬 참고용이며 자동 차단·실제 운영·malformed 탐지 보장을 뜻하지 않는다."]}
        outputs[0].parent.mkdir(parents=True, exist_ok=True)
        write_json(outputs[0], report)
        lines = ["# 승인 RF의 실제 FastAPI 연결 검사", "", "실제 Uvicorn을 로컬에서 실행해 직접 모델 계산과 HTTP 응답을 대조했다.",
                 "학습·공식 Test·수집 레코드는 열지 않았고 직접 만든 snapshot만 사용했다. 탐지 성능 평가가 아니다.", "",
                 "| 요청 | HTTP | label | 이상 점수 |", "| --- | ---: | --- | ---: |"]
        for check in checks:
            body = check.get("response", {})
            lines.append(f"| {check['name']} | {check['status_code']} | {body.get('label', '판단 없음')} | {body.get('anomaly_score', '해당 없음')} |")
        lines += ["", "세 메시지 응답은 직접 RF 계산과 정확히 일치했다. 요약에는 가짜 정상 판단을 반환하지 않았고 잘못된 입력도 422로 거부했다.",
                  "생성한 프로세스만 quit 입력으로 정상 종료하고 승인 파일·구현 해시를 재검증했다.",
                  "", "## 한계와 다음 작업", "", *report["limitations"], "",
                  "다음은 실제 C++ TCP 요청·결과 저장·오류·지연·큐 포화·종료·부하 검증이다. 남은 큰 단계는 2개다.", ""]
        with outputs[1].open("x", encoding="utf-8") as stream:
            stream.write("\n".join(lines))
        write_json(directory / "verification_manifest.json", {"status": "complete", "reports": {str(path): file_hash(path) for path in outputs},
            "server_log_sha256": report["server_log_sha256"], "graceful_shutdown": True})
        return report
    except BaseException as error:
        write_json(directory / "verification_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 RF FastAPI를 소유한 별도 프로세스에서 실행·검사·정상 종료합니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--artifact-dir", type=Path, default=AI_ROOT / "artifacts" / "inference_api" / "rf_v1" / "http_smoke_v1")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_rf_api")
    args = parser.parse_args()
    result = verify(args.config.resolve(), args.artifact_dir.resolve(), args.report_prefix.resolve())
    print(f"[RF API] 실제 HTTP 검사 {len(result['checks'])}개·정상 종료 완료. 남은 큰 단계 2개.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
