from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import uvicorn

from inference_api.approved_bundle import DEFAULT_CONFIG
from inference_api.rf_main import create_rf_app


def main() -> int:
    parser = argparse.ArgumentParser(description="승인 RF를 검증해 로컬 FastAPI 실제 추론을 실행합니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", type=int, default=8000, metavar="PORT")
    parser.add_argument("--stdin-control", action="store_true", help="소유한 자동 검사 프로세스에서만 quit 입력으로 종료합니다.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port는 1~65535 범위여야 합니다.")
    app = create_rf_app(args.config.resolve())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1, limit_concurrency=32,
                                         timeout_graceful_shutdown=15, use_colors=False))
    if args.stdin_control:
        def wait_for_quit():
            # HTTP 종료 경로는 노출하지 않고 생성자가 소유한 표준 입력만 사용한다.
            if sys.stdin.readline().strip() == "quit":
                server.should_exit = True
        threading.Thread(target=wait_for_quit, daemon=True, name="rf-api-shutdown-control").start()
    server.run()
    return 0 if server.started else 2


if __name__ == "__main__":
    raise SystemExit(main())
