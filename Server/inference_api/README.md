# Dummy Inference API

FastAPI 기반 더미 추론 서버입니다. 현재 단계에서는 실제 인공지능 모델을 호출하지 않고, C++ 서버에서 전달한 JSON을 출력한 뒤 고정 응답을 반환합니다.

```powershell
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```
