from __future__ import annotations

from inference_api import main as api_module
from inference_api.model_service import DummyModelService


# 테스트 프로세스에서만 수신 기록을 조회한다. 일반 main:app에는 이 경로가 없다.
app = api_module.app
records: list[dict] = []


class RecordingDummyService:
    def __init__(self) -> None:
        self.dummy = DummyModelService()

    def predict(self, payload, vector):
        response = self.dummy.predict(payload, vector)
        records.append({"payload": payload.model_dump(mode="json"), "response": response.model_dump(mode="json")})
        return response


api_module.model_service = RecordingDummyService()


@app.get("/__test__/records")
async def get_records() -> list[dict]:
    return records.copy()
