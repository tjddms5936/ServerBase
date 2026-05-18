from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "NetworkDetectionAI Inference API"
    schema_version: str = "v1"
    model_version: str = "dummy-v0"


settings = Settings()
