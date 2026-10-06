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
from datetime import UTC, datetime
from pathlib import Path

import run_runtime_e2e as runtime


AI_ROOT = runtime.AI_ROOT
REPORT_PATH = AI_ROOT / "reports" / "data_collection_e2e.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 C++ 서버의 독립 시나리오 수집과 정상 종료 flush를 검사합니다.")
    parser.add_argument("--server-exe", type=Path, default=runtime.SERVER_ROOT / "BIN" / "Debug" / "Server.exe")
    args = parser.parse_args()
    runtime.check(args.server_exe.is_file(), "Server.exe가 없습니다. 먼저 Debug x64로 빌드하세요.")
    for port in (4000, 8000):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    build_hash = hashlib.sha256(args.server_exe.read_bytes()).hexdigest()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(AI_ROOT / ".deps"), str(AI_ROOT)])
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    results = []
    text = b"collection-check"
    normal_body = struct.pack("!H", len(text)) + text + struct.pack("!i", 42)

    with tempfile.TemporaryDirectory(prefix="network_collection_e2e_") as temporary:
        root = Path(temporary)
        for scenario_id, label, body in [("normal_chat", 0, normal_body), ("malformed_chat_payload", 1, b"\x00")]:
            # 매 시나리오마다 두 프로세스를 다시 시작하고 새 run으로 세션 ID 재사용을 분리한다.
            run_id = f"e2e_{scenario_id}"
            config_path = root / f"{run_id}_config.json"
            config_path.write_text(json.dumps({
                "config_version": "v1", "run_id": run_id, "scenario_id": scenario_id,
                "server_build_id": f"sha256:{build_hash}", "dataset_purpose": "integration_test",
                "scenario_manifest": str(AI_ROOT / "configs" / "collection_scenarios.json"),
                "output_dir": str(root / "captures"), "queue_capacity": 32,
                "flush_interval_s": 0.05, "shutdown_timeout_s": 10.0,
            }), encoding="utf-8")
            environment["NDA_COLLECTION_CONFIG"] = str(config_path)
            api_process = server_process = None
            api_log = root / f"{run_id}_api.log"
            server_log = root / f"{run_id}_server.log"
            with api_log.open("wb") as api_output, server_log.open("wb") as server_output:
                try:
                    api_process = subprocess.Popen(
                        [sys.executable, str(AI_ROOT / "tests" / "collection_e2e_api.py")],
                        cwd=AI_ROOT, env=environment, stdout=api_output, stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE, creationflags=creationflags,
                    )
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            status = runtime.get_json("/collection/status")
                            runtime.check(status.get("run_id") == run_id, "수집 API의 실행 ID가 다릅니다.")
                            break
                        except OSError:
                            runtime.check(api_process.poll() is None and time.monotonic() < deadline, "수집 API 시작 실패")
                            time.sleep(0.1)
                    server_process = subprocess.Popen(
                        [str(args.server_exe.resolve())], cwd=runtime.SERVER_ROOT, env=environment,
                        stdout=server_output, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, creationflags=creationflags,
                    )
                    runtime.run_client(struct.pack("!ii", 0, 8 + len(body)) + body, normal=label == 0)
                    deadline = time.monotonic() + 15
                    while runtime.get_json("/collection/status")["flushed"] != 2:
                        runtime.check(server_process.poll() is None and time.monotonic() < deadline, "두 snapshot 수집/flush 실패")
                        time.sleep(0.05)
                    # 먼저 C++ 전송 경로를 종료한 뒤 API 큐를 배출해야 마지막 요약을 놓치지 않는다.
                    runtime.stop_owned_process(server_process, graceful=True)
                    runtime.check(server_process.returncode == 0, "C++ 정상 종료 실패")
                    runtime.stop_owned_process(api_process, graceful=True)
                    runtime.check(api_process.returncode == 0, "API 정상 종료 실패")
                    run_dir = root / "captures" / run_id
                    records = [json.loads(line) for line in (run_dir / "features.jsonl").read_text(encoding="utf-8").splitlines()]
                    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
                    summary = json.loads((run_dir / "collection_summary.json").read_text(encoding="utf-8"))
                    runtime.check(summary["complete"] and summary["flushed"] == 2, "수집 종료 통계 오류")
                    runtime.check(manifest["selected_scenario"]["label"] == label, "독립 정답 불일치")
                    runtime.check(manifest["dataset_purpose"] == "integration_test", "테스트 데이터 표시 누락")
                    runtime.check(manifest["server_build_id"] == f"sha256:{build_hash}", "빌드 식별자 불일치")
                    runtime.check([record["collection_sequence_id"] for record in records] == [1, 2], "수집 순번 오류")
                    for record in records:
                        runtime.check(record["run_id"] == run_id and record["scenario_id"] == scenario_id, "실행/시나리오 혼합")
                        runtime.check("label" not in record["payload"] and "prediction" not in record, "정답/예측의 feature 혼입")
                    messages = [record for record in records if record["payload"]["event_type"] == "message"]
                    summaries = [record for record in records if record["payload"]["event_type"] == "session_summary"]
                    runtime.check(len(messages) == len(summaries) == 1, "메시지/요약 구분 오류")
                    runtime.check(messages[0]["payload"]["features"]["error_count_total"] == 0, "미래 오류가 메시지에 반영됨")
                    runtime.check(summaries[0]["payload"]["features"]["error_count"] == label, "세션 오류 집계 오류")
                    runtime.check(messages[0]["session_id"] == summaries[0]["session_id"], "세션 식별자 불일치")
                    results.append({
                        "run_id": run_id, "scenario_id": scenario_id, "passed": True,
                        "independent_label": label, "summary": summary, "records": records,
                        "source_manifest_sha256": manifest["source_manifest_sha256"],
                        "operational_contract_sha256": manifest["operational_contract_sha256"],
                        "server_graceful_shutdown": True, "api_graceful_shutdown": True,
                    })
                    print(f"PASS: {scenario_id} / JSONL 2건 / 정상 종료 flush")
                except Exception:
                    api_output.flush()
                    server_output.flush()
                    print(api_log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
                    print(server_log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
                    raise
                finally:
                    runtime.stop_owned_process(server_process, graceful=True)
                    runtime.stop_owned_process(api_process, graceful=True)
                    for process in (server_process, api_process):
                        if process is not None and process.stdin:
                            process.stdin.close()
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(), "actual_model_inference": False,
        "training_dataset": False, "temporary_capture_removed": True,
        "server_executable_sha256": build_hash, "scenario_count": len(results), "runs": results,
        "code_evidence": [{
            "path": str(path.relative_to(AI_ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        } for path in [
            AI_ROOT / "data_collection" / "config.py", AI_ROOT / "data_collection" / "collector.py",
            AI_ROOT / "inference_api" / "main.py", AI_ROOT / "tests" / "run_collection_e2e.py",
        ]],
        "limitations": ["로컬의 통제된 프로토콜 테스트이며 실제 침입 탐지 성능이 아니다.", "파일 저장 및 flush를 확인했으며 전원 차단 시 디스크 영속성을 보장하지 않는다."],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"보고서: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
