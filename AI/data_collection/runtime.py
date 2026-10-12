from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
import json
from pathlib import Path

from .config import AI_ROOT


def ensure_ports_free() -> None:
    for port in (4000, 8000):
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(f"{port} 포트가 사용 중입니다. 기존 프로세스를 종료한 뒤 다시 실행하세요.") from error


def child_environment(config_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(AI_ROOT / ".deps"), str(AI_ROOT)])
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NDA_COLLECTION_CONFIG"] = str(config_path)
    return environment


def get_collection_status() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8000/collection/status", timeout=2) as response:
        return json.load(response)


def wait_for_capture(process: subprocess.Popen, run_id: str, expected_records: int = 0) -> dict:
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"수집 API가 종료되었습니다: {process.returncode}")
        try:
            status = get_collection_status()
            if status.get("run_id") != run_id:
                raise RuntimeError("현재 포트의 API가 이 수집 실행의 프로세스가 아닙니다.")
            if status["write_error"] or status["dropped_full"]:
                raise RuntimeError("수집기에 저장 오류 또는 큐 유실이 발생했습니다.")
            if status["flushed"] >= expected_records:
                return status
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{run_id}: 기대한 JSONL 수집/flush 대기 시간 초과")
        time.sleep(0.05)


def stop_owned_process(process: subprocess.Popen | None) -> dict:
    if process is None:
        return {"started": False, "graceful": False, "forced": False, "exit_code": None}
    forced = False
    requested = False
    if process.poll() is None:
        try:
            process.stdin.write(b"quit\n")
            process.stdin.flush()
            requested = True
            process.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            # 강제 정리는 이 호출자가 시작한 Popen 객체로만 제한한다.
            forced = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if process.stdin:
        process.stdin.close()
    return {"started": True, "graceful": requested and not forced and process.returncode == 0,
            "forced": forced, "exit_code": process.returncode}
