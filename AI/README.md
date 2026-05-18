# NetworkDetectionAI Python AI

This folder contains the Python inference server for the C++ TCP server.

Current scope:

- Validate feature JSON from the C++ server
- Convert message/session payloads into ordered feature vectors
- Run a dummy model service
- Return a fixed response schema to the C++ `AiInferenceWorker`

Run:

```powershell
.\run_fastapi.bat
```

Endpoint:

```text
POST http://127.0.0.1:8000/predict
```
