from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request


app = FastAPI(title="NetworkDetectionAI Dummy Inference API")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/predict")
async def predict(request: Request) -> dict[str, Any]:
    payload = await request.json()
    print("[dummy-inference] received feature payload")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    return {
        "request_id": str(payload.get("queue_sequence_id", "unknown")),
        "label": "normal",
        "is_attack": False,
        "confidence": 0.5,
        "model_version": "dummy-v0",
        "received_event_type": payload.get("event_type", "unknown"),
    }
