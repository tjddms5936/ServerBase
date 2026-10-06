from __future__ import annotations

import copy
import hashlib
import json
import logging
import queue
import threading
import time
from datetime import UTC, datetime

from .config import AI_ROOT, CollectionConfig, load_manifest


logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AsyncJsonlCollector:
    def __init__(self, config: CollectionConfig) -> None:
        self.config = config
        manifest, scenario, manifest_hash = load_manifest(config.scenario_manifest, config.scenario_id)
        contract_path = AI_ROOT / "configs" / "operational_feature_schema.json"
        contract_bytes = contract_path.read_bytes()
        self.run_dir = config.output_dir.resolve() / config.run_id
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=config.queue_capacity)
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._accepting = True
        self._in_flight = 0
        self._stats = {
            "attempted": 0, "enqueued": 0, "written": 0, "flushed": 0,
            "dropped_full": 0, "dropped_closed": 0, "dropped_failed": 0,
            "failed_records": 0, "queue_high_watermark": 0,
            "shutdown_timed_out": False, "shutdown_complete": False,
            "write_error": None, "summary_error": None, "state": "running",
        }
        self._created_at = utc_now()
        # 같은 run을 덮어쓰거나 이어 쓰면 세션 ID가 섞이므로 새 디렉터리만 허용한다.
        self.run_dir.mkdir(parents=True, exist_ok=False)
        run_manifest = {
            "record_schema_version": "v1", "created_at_utc": self._created_at,
            "run_id": config.run_id, "scenario_id": config.scenario_id,
            "server_build_id": config.server_build_id, "dataset_purpose": config.dataset_purpose,
            "selected_scenario": scenario.model_dump(mode="json"),
            "label_source": manifest.label_source,
            "source_manifest_sha256": manifest_hash,
            "scenario_manifest_snapshot": manifest.model_dump(mode="json"),
            "operational_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
            "operational_contract_snapshot": json.loads(contract_bytes),
            "collection_config": config.model_dump(mode="json"),
            "scope": "한 C++ 프로세스 실행, 한 시나리오. 실제 침입 탐지 정답을 보장하지 않는다.",
        }
        (self.run_dir / "run_manifest.json").write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
        )
        self._stream = (self.run_dir / "features.jsonl").open("x", encoding="utf-8", newline="\n")
        self._worker = threading.Thread(target=self._run, name="feature-jsonl-writer", daemon=True)
        try:
            self._worker.start()
        except Exception:
            self._stream.close()
            raise

    def try_collect(self, payload: dict) -> bool:
        # 검증된 snapshot을 복사한다. 이후 호출자가 바꿔도 저장 대기 중인 값은 변하지 않는다.
        snapshot = copy.deepcopy(payload)
        received_at = utc_now()
        with self._lock:
            self._stats["attempted"] += 1
            if self._stats["write_error"] is not None:
                self._stats["dropped_failed"] += 1
                return False
            if not self._accepting:
                self._stats["dropped_closed"] += 1
                return False
            record = {
                "record_schema_version": "v1",
                "collection_sequence_id": self._stats["attempted"],
                "received_at_utc": received_at,
                "run_id": self.config.run_id, "scenario_id": self.config.scenario_id,
                "server_build_id": self.config.server_build_id,
                "session_id": snapshot["session"]["session_id"],
                "request_index": snapshot.get("message", {}).get("request_index"),
                "label_reference": "run_manifest.json:selected_scenario",
                "payload": snapshot,
            }
            try:
                # 파일 저장과 빈자리 대기는 하지 않는다. 짧은 메모리 큐 접근만 수행한다.
                self._queue.put_nowait(record)
            except queue.Full:
                self._stats["dropped_full"] += 1
                return False
            self._stats["enqueued"] += 1
            self._stats["queue_high_watermark"] = max(self._stats["queue_high_watermark"], self._queue.qsize())
            return True

    def status(self) -> dict:
        with self._lock:
            status = dict(self._stats)
            status.update({
                "enabled": True, "run_id": self.config.run_id, "scenario_id": self.config.scenario_id,
                "queue_capacity": self.config.queue_capacity, "pending": self._queue.qsize(),
                "in_flight": self._in_flight,
                "unfinished": self._stats["enqueued"] - self._stats["written"] - self._stats["failed_records"],
            })
            return status

    def stop(self) -> bool:
        with self._lock:
            self._accepting = False
            if self._stats["state"] == "running":
                self._stats["state"] = "stopping"
        self._stop_requested.set()
        self._worker.join(timeout=self.config.shutdown_timeout_s)
        if self._worker.is_alive():
            # 지연된 디스크 I/O 스레드는 안전하게 강제 종료할 수 없으므로 불완전 종료로 표시한다.
            with self._lock:
                self._stats["shutdown_timed_out"] = True
            logger.error("JSONL 수집 worker 종료 제한 시간을 초과했습니다: %s", self.config.run_id)
            return False
        status = self.status()
        return status["shutdown_complete"] and not status["shutdown_timed_out"] and status["summary_error"] is None

    def _write_record(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        if self._stream.write(line) != len(line):
            raise OSError("JSONL 한 줄을 완전히 기록하지 못했습니다.")

    def _flush(self) -> None:
        self._stream.flush()
        with self._lock:
            self._stats["flushed"] = self._stats["written"]

    def _fail(self, error: Exception) -> None:
        with self._lock:
            self._accepting = False
            if self._stats["write_error"] is None:
                self._stats["write_error"] = f"{type(error).__name__}: {error}"
            self._stats["state"] = "failed"
        # 실패 후 큐를 비워 유실량을 집계한다. 재시도나 HTTP 실패 전파는 하지 않는다.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                self._stats["failed_records"] += 1
            self._queue.task_done()
        logger.error("JSONL 저장 실패, 추론은 계속합니다: %s", error)

    def _run(self) -> None:
        next_flush = time.monotonic() + self.config.flush_interval_s
        try:
            while not (self._stop_requested.is_set() and self._queue.empty()):
                try:
                    record = self._queue.get(timeout=min(self.config.flush_interval_s, 0.1))
                except queue.Empty:
                    record = None
                if record is not None:
                    with self._lock:
                        self._in_flight = 1
                    try:
                        self._write_record(record)
                        with self._lock:
                            self._stats["written"] += 1
                    except Exception:
                        with self._lock:
                            self._stats["failed_records"] += 1
                        raise
                    finally:
                        with self._lock:
                            self._in_flight = 0
                        self._queue.task_done()
                if time.monotonic() >= next_flush:
                    self._flush()
                    next_flush = time.monotonic() + self.config.flush_interval_s
            self._flush()
        except Exception as error:
            self._fail(error)
        finally:
            try:
                self._stream.close()
            except Exception as error:
                self._fail(error)
            with self._lock:
                self._accepting = False
                if self._stats["write_error"] is None:
                    self._stats["state"] = "closed"
                    self._stats["shutdown_complete"] = True
            status = self.status()
            status["complete"] = (
                status["shutdown_complete"] and not status["shutdown_timed_out"]
                and status["flushed"] == status["enqueued"]
                and not any(status[key] for key in ("dropped_full", "dropped_closed", "dropped_failed", "failed_records"))
            )
            status["finished_at_utc"] = utc_now()
            try:
                (self.run_dir / "collection_summary.json").write_text(
                    json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
                )
            except Exception as error:
                with self._lock:
                    self._stats["summary_error"] = f"{type(error).__name__}: {error}"
                logger.error("수집 종료 통계 저장 실패: %s", error)
