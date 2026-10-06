from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.config import CollectionConfig, load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="단일 시나리오의 feature를 비동기 JSONL로 수집하는 API를 실행합니다.")
    parser.add_argument("--scenario-id", required=True, help="독립 manifest에 정의된 시나리오 ID")
    parser.add_argument("--run-id", help="생략하면 UTC 시각과 임의 ID로 새 실행 ID 생성")
    parser.add_argument("--server-exe", type=Path, default=AI_ROOT.parent / "Server" / "BIN" / "Debug" / "Server.exe")
    parser.add_argument("--scenario-manifest", type=Path, default=AI_ROOT / "configs" / "collection_scenarios.json")
    parser.add_argument("--output-dir", type=Path, default=AI_ROOT / "data" / "raw" / "operational")
    parser.add_argument("--dataset-purpose", choices=["controlled_experiment", "integration_test"], default="controlled_experiment")
    parser.add_argument("--queue-capacity", type=int, default=1024)
    args = parser.parse_args()
    if not args.server_exe.is_file():
        parser.error("빌드한 Server.exe 경로를 지정해야 합니다.")
    load_manifest(args.scenario_manifest, args.scenario_id)
    with args.server_exe.open("rb") as executable:
        build_hash = hashlib.file_digest(executable, "sha256").hexdigest()
    run_id = args.run_id or f"run_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid4().hex[:8]}"
    config = CollectionConfig(
        run_id=run_id, scenario_id=args.scenario_id, server_build_id=f"sha256:{build_hash}",
        scenario_manifest=args.scenario_manifest.resolve(), output_dir=args.output_dir.resolve(),
        dataset_purpose=args.dataset_purpose, queue_capacity=args.queue_capacity,
    )
    print(f"수집 실행: {run_id}, 시나리오: {args.scenario_id}")
    print(f"서버 실행 파일: {args.server_exe.resolve()}")
    print(f"저장 위치: {config.output_dir / run_id}")
    print("API 시작 후 위 C++ 서버를 실행하세요. 다른 시나리오나 서버 재시작에는 새 수집 실행이 필요합니다.")
    previous = os.environ.get("NDA_COLLECTION_CONFIG")
    try:
        # 일시적인 설정 파일은 종료 시 제거하고 실제 설정은 run manifest에 보존한다.
        with tempfile.TemporaryDirectory(prefix="network_collection_config_") as temporary:
            path = Path(temporary) / "collection.json"
            path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            os.environ["NDA_COLLECTION_CONFIG"] = str(path)
            import uvicorn

            uvicorn.run("inference_api.main:app", host="127.0.0.1", port=8000, workers=1)
    finally:
        if previous is None:
            os.environ.pop("NDA_COLLECTION_CONFIG", None)
        else:
            os.environ["NDA_COLLECTION_CONFIG"] = previous
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
