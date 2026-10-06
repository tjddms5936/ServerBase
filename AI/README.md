# NetworkDetectionAI Python AI 모듈

이 폴더는 C++ TCP 서버와 연동되는 Python 기반 AI 학습/추론 코드를 관리하는 공간입니다.

현재 구현된 범위:

- C++ 서버에서 전달되는 feature JSON 검증
- message/session payload를 모델 입력용 feature vector로 변환
- dummy model service 실행
- C++ `AiInferenceWorker`가 파싱할 수 있는 고정 응답 schema 반환
- UNSW-NB15 데이터셋 EDA 스크립트
- UNSW-NB15 baseline 학습용 전처리 스크립트
- Logistic Regression과 RandomForest baseline 학습 및 오분류 비교
- 같은 CPU 스레드 조건의 단일 요청/배치 추론 지연시간 비교
- 원본 train의 중복 feature 그룹 분리와 validation 기반 설정·threshold 선정
- C++ 수집 의미와 UNSW 입력 42개의 정합성 대조 및 운영 feature 명세
- FastAPI 입력 순서·타입 일치 검사와 모델 schema 호환성 검사
- 오류 집계·종료 snapshot 고정 및 v2 의미 버전 기반 FastAPI strict 입력 검증
- 독립 시나리오 정답과 run별 provenance를 보관하는 비동기 bounded JSONL 수집기

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
  data_collection/ 비동기 운영 feature 저장과 실행·정답 이력 관리
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

RandomForest baseline 학습 및 두 모델 비교:

```powershell
python .\training\train_random_forest.py
python .\evaluation\compare_baselines.py
```

결과는 `reports/random_forest_metrics.json`, `reports/baseline_comparison.json`, `reports/model_comparison.md`에 저장됩니다. 비교 보고서는 오탐 감소와 공격 누락 변화, 공격 유형별 Recall, 단일 요청 지연시간을 함께 보여줍니다.

이 baseline은 UNSW-NB15 기반 오프라인 실험입니다. 운영 모델을 FastAPI에 연결하기 전에는 C++ 수집 feature와 학습 feature의 의미 및 가용성을 맞춰야 합니다.

Validation 분리와 개선 실험:

```powershell
python .\training\run_validation_experiment.py
```

`configs/validation_experiment.json`의 고정 후보와 선택 규칙을 사용합니다. 공식 train을 중복 feature 그룹 단위로 약 80:20 분리하고 학습 부분에만 전처리기를 fit합니다. Validation에서 FPR 10% 이하인 조합의 Recall을 최대화하며, 공식 test는 이 실험에서 읽지 않습니다.

결과는 `reports/validation_experiment.json`, `reports/validation_experiment.md`, `artifacts/validation_experiment/`에 저장됩니다. 기존 baseline 보고서와 산출물은 별도 경로에 유지됩니다.

운영 feature 정합성 검사:

```powershell
python .\evaluation\audit_feature_alignment.py
```

`configs/operational_feature_schema.json`에 메시지 입력 7개와 종료 요약 입력 8개의 의미·단위·순서를 정의합니다. 현재 FastAPI의 필드와 transformer 순서를 검사하고 UNSW 모델의 원본 입력 schema와 대조합니다. 결과는 `reports/feature_alignment.json`, `reports/feature_alignment.md`에 저장됩니다.

현재 UNSW 모델과 운영 입력은 비호환입니다. 비슷한 이름으로 치환하거나 부족한 입력을 0으로 채워 연결하지 않습니다. 공개 데이터 baseline은 오프라인 연구 실험으로 유지하고, 운영 모델은 동일한 애플리케이션 feature로 수집한 별도 데이터로 학습합니다.

`--require-compatible` 옵션은 지정 이벤트의 schema가 비호환이면 종료 코드 2를 반환합니다. 기본 검사 대상은 메시지이며, 종료 요약은 `--event-type session_summary`로 지정합니다. schema 호환 판정은 실제 모델 성능이나 배포 가능성을 보장하지 않습니다.

현재 `/predict`는 공통 `inference_api/payload_validation.py` 규칙을 적용합니다. v2 `feature_contract_id`가 필요하며 비정상 수치, 중복 값 불일치, 잘못된 프레임 크기나 관측 상태는 HTTP 422로 거부합니다. C++ 서버를 다시 빌드하고 FastAPI도 재시작해야 합니다. 실제 inference는 계속 dummy 상태입니다.

입력/API 자동 테스트에는 `requirements-dev.txt`의 httpx가 필요합니다. 기존 `.deps` 환경에서도 아래처럼 준비합니다.

```powershell
python -m pip install --target .deps -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```

빌드된 C++ 서버와 실제 HTTP 연동 검사:

```powershell
python .\tests\run_runtime_e2e.py
```

이 테스트는 127.0.0.1의 4000/8000 포트가 비어 있어야 하며 직접 시작한 프로세스만 종료합니다. 정상 메시지와 잘못된 payload/ID/프레임 크기 8개 시나리오를 확인하고 `reports/runtime_hardening_e2e.json`을 생성합니다. 테스트용 API 조회 경로는 일반 inference 서버에는 추가되지 않습니다. 이 결과는 학습 데이터나 실제 탐지 성능이 아닙니다.

변경 상세는 `docs/runtime_hardening.md`, C++ 단위 테스트는 `Server/tests/`에서 확인합니다.

운영 feature JSONL 수집 API 실행:

```powershell
python .\scripts\run_collection_api.py --scenario-id normal_chat
```

기존 API와 C++ 서버를 먼저 종료하고 이 API가 시작된 다음 표시된 C++ 실행 파일과 정상 클라이언트를 실행합니다. 한 수집 실행에는 한 C++ 프로세스와 한 시나리오만 사용합니다. 다른 시나리오나 서버 재시작에는 새 run을 사용합니다. 기본 `run_fastapi.bat` 실행은 수집을 끈 상태로 유지합니다.

결과는 `data/raw/operational/<run_id>/features.jsonl`, `run_manifest.json`, `collection_summary.json`에 저장되며 Git에는 포함하지 않습니다. 정답은 사전에 정의한 `configs/collection_scenarios.json`에서 가져오고 dummy 응답이나 오류 수에서 만들지 않습니다. 큐 포화/파일 오류는 추론 응답과 분리하고 `GET /collection/status`에서 확인합니다. 종료할 때는 먼저 C++ 서버의 전송 worker를 종료한 뒤 API에서 `Ctrl+C`를 눌러 flush합니다.

실제 C++/HTTP/JSONL 연동 및 정상 종료 검사:

```powershell
python .\tests\run_collection_e2e.py
```

보고서는 `reports/data_collection_e2e.json`이며 임시 테스트 원본은 제거합니다. 실제 모델 추론이나 학습용 데이터 생성은 아닙니다. 실행·저장 형식·유실 통계·한계는 `docs/operational_data_collection.md`에서 확인합니다. 다음 작업은 정상/통제된 이상 트래픽 생성기와 여러 run의 수집 계획·데이터 품질 검증입니다.
