# NetworkDetectionAI Python AI 모듈

이 폴더는 C++ TCP 서버와 연동되는 Python 기반 AI 학습/추론 코드를 관리하는 공간입니다.

현재 구현된 범위:

- C++ 서버에서 전달되는 feature JSON 검증
- message/session payload를 모델 입력용 feature vector로 변환
- dummy model service 실행
- C++ `AiInferenceWorker`가 파싱할 수 있는 고정 응답 schema 반환
- UNSW-NB15 데이터셋 EDA 스크립트
- UNSW-NB15 baseline 학습용 전처리 스크립트

학습 로드맵:

- 1차 데이터셋: UNSW-NB15
- 2차 확장 데이터셋: CIC-IDS2017
- 첫 기준 모델: Logistic Regression, RandomForest
- 이후 개선 모델: LSTM 기반 sequence model

폴더 구조와 역할:

```text
AI/
  configs/        데이터셋, feature schema, 모델 설정 파일
  data/           원본/중간/전처리 완료 데이터 저장
  docs/           데이터셋 선정, 실험 설계, 아키텍처 문서
  notebooks/      EDA와 실험 확인용 노트북
  scripts/        데이터 확인과 반복 작업 자동화 스크립트
  training/       모델 학습 스크립트
  evaluation/     모델 평가와 latency benchmark 스크립트
  artifacts/      학습된 모델, scaler, label map, feature schema
  reports/        실험 지표, 그래프, 모델 비교 보고서
  inference_api/  FastAPI 기반 실시간 inference 서버
```

FastAPI 실행:

```powershell
.\run_fastapi.bat
```

추론 엔드포인트:

```text
POST http://127.0.0.1:8000/predict
```

UNSW-NB15 원본 데이터 확인:

```powershell
python .\scripts\inspect_unsw_nb15.py
```

UNSW-NB15 전처리:

```powershell
python .\scripts\preprocess_unsw_nb15.py
```

전처리 결과:

```text
data/processed/unsw_nb15/
artifacts/preprocessor_unsw_nb15.joblib
artifacts/feature_schema_unsw_nb15.json
reports/preprocess_summary_unsw_nb15.json
```

Logistic Regression baseline 학습:

```powershell
python .\training\train_logistic_regression.py
```

Logistic Regression 오분류 분석:

```powershell
python .\evaluation\analyze_logistic_errors.py
```
