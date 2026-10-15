from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(AI_ROOT / ".deps"), str(AI_ROOT)]

import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from inference_api.rf_main import create_rf_app


def create_test_app():
    # 이 앱은 소유한 로컬 종단 검사 전용이다. 운영 앱에 제어·기록 경로를 추가하지 않는다.
    app = create_rf_app()
    records: list[dict] = []
    state = {"mode": "normal", "delay_ms": 0, "active": 0}
    modes = {"normal", "delay", "busy", "bad_json", "wrong_id", "wrong_model", "wrong_policy", "oversized"}

    @app.post("/__test__/control")
    async def control(request: Request):
        payload = await request.json()
        if payload.get("mode") not in modes or not 0 <= payload.get("delay_ms", 0) <= 3000:
            raise HTTPException(400, "허용하지 않는 장애 설정입니다.")
        state.update(mode=payload["mode"], delay_ms=payload.get("delay_ms", 0))
        return dict(state)

    @app.get("/__test__/records")
    async def get_records(after: int = 0):
        return {"records": records[after:], "total": len(records), "active": state["active"]}

    @app.middleware("http")
    async def instrument(request: Request, call_next):
        if request.url.path != "/predict":
            return await call_next(request)
        if len(records) >= 10000:
            return JSONResponse({"detail": "검사 기록 상한 초과"}, status_code=503)
        payload = await request.json()
        mode, delay = state["mode"], state["delay_ms"]
        started = time.perf_counter()
        state["active"] += 1
        try:
            if mode == "delay":
                await asyncio.sleep(delay / 1000)
            if mode == "busy":
                response = JSONResponse({"detail": {"code": "inference_busy"}}, status_code=503)
                body = response.body
            elif mode == "bad_json":
                body = b'{"request_id":'
                response = Response(body, media_type="application/json")
            elif mode == "oversized":
                body = b"x" * 70000
                response = Response(body, media_type="application/json")
            else:
                response = await call_next(request)
                body = b"".join([chunk async for chunk in response.body_iterator])
                if response.status_code == 200 and mode in {"wrong_id", "wrong_model", "wrong_policy"}:
                    changed = json.loads(body)
                    if mode == "wrong_id":
                        changed["request_id"] = "not-the-request"
                    elif mode == "wrong_model":
                        changed["model_version"] = "unapproved"
                    else:
                        changed["decision_threshold"] = 0.5
                    body = json.dumps(changed).encode()
                response = Response(body, status_code=response.status_code, media_type="application/json")
            try:
                decoded = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                decoded = None
            records.append({"payload": payload, "response": decoded, "http_status": response.status_code,
                            "mode": mode, "api_elapsed_ms": (time.perf_counter() - started) * 1000})
            return response
        finally:
            state["active"] -= 1

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 RF를 사용하는 로컬 종단 검사 전용 API입니다.")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config(create_test_app(), host="127.0.0.1", port=args.port,
        workers=1, use_colors=False, log_level="warning", timeout_graceful_shutdown=10))

    def control_stdin():
        for line in sys.stdin:
            if line.strip() == "quit":
                server.should_exit = True
                return

    threading.Thread(target=control_stdin, daemon=True).start()
    server.run()
    return 0 if server.started else 2


if __name__ == "__main__":
    raise SystemExit(main())
