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
- 정상·통제된 이상 트래픽 생성기, 다중 run 수집과 누락·중복·정답 분포 검증
- 운영 메시지 입력·독립 정답·metadata 분리와 run/session 비중복 고정 train·validation·test
- 운영 train 전용 StandardScaler/LR 직접 학습과 validation 평가·수렴·가중치 기록
- 동일 운영 train 전용 RF 초기 학습과 LR/RF validation 오분류 비교
- 제한된 운영 LR/RF 후보·threshold의 validation 선정과 사전 protocol·선정 정책 보관
- 선정 운영 모델의 Validation 오분류·prefix·입력 관측 한계 분석과 독립 캠페인 보강 설계
- 명시적 v2 송신 제어, 독립 캠페인 6개·84개 run 실제 수집·검증과 사전 캠페인 단위 분할
- 고정 기존 LR·threshold 0.7의 새 Validation 513건 평가·오분류·세션 prefix 진단
- 새 Train 1,538건 전용 보강 LR/RF 6개·threshold 42개 조합 선정과 동일 Validation 비교
- 동일 campaign/run/session의 과거·현재 최대 8개 창, 짧은 세션 padding·mask·정답·원본 추적과 LSTM 사전 설계
- Train 전용 scaler·LSTM 두 후보 직접 학습, 최적 epoch·14개 정책 선정·동일 Validation RF 비교와 실제 재학습 재현
- Validation 비용 기반 사전 후보 잠금·고정 Test 최종 비교·holdout 사용 기록과 배포 승인 보류

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
  data_preparation/ 검증된 운영 원본 추출과 학습 데이터셋·분할 구성
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

보고서는 `reports/data_collection_e2e.json`이며 임시 테스트 원본은 제거합니다. 실제 모델 추론이나 학습용 데이터 생성은 아닙니다. 실행·저장 형식·유실 통계·한계는 `docs/operational_data_collection.md`에서 확인합니다.

정상·통제된 이상 트래픽 자동 생성과 다중 run 수집:

```powershell
python .\scripts\collect_operational_traffic.py
```

기존 4000/8000 포트의 서버와 클라이언트를 먼저 종료합니다. 이 도구가 C++ 서버, 수집 API, 로컬 클라이언트를 직접 실행하고 종료합니다. 기본 계획은 정상 3종과 통제된 이상 4종을 각각 3번 실행하는 21개 run입니다. 외부 주소로 연결하거나 무제한 요청을 보내지 않습니다.

원본은 `data/raw/operational_campaigns/<campaign_id>/`에 보관하고, 최신 품질 결과는 `reports/operational_data_quality.json`과 `.md`에 저장합니다. 송신 전 기대값과 실제 JSONL을 대조해 누락·중복·정답 출처·종료 통계를 검사합니다. 헤더 거부 시나리오는 메시지 관측 불가로 구분하고, 연동 테스트와 불완전 run은 메시지 학습 후보에서 제외합니다.

시나리오·설정·저장 구조·재검사 방법은 `docs/traffic_generation.md`에 정리했습니다.

운영 메시지 데이터셋 구성과 고정 분할:

```powershell
python .\scripts\prepare_operational_dataset.py
```

`configs/operational_dataset.json`의 원본을 재검증해 메시지 입력 7개, label, metadata를 별도 CSV로 저장합니다. 실제 수집 시각 순서의 반복 묶음으로 run 전체를 나누고 같은 세션이 분할 사이에 섞이지 않도록 검사합니다. 현재 train 126건, validation 138건, test 142건이며 각 분할은 run 6개·세션 24개입니다.

결과는 `data/processed/operational_message/v1/`와 `reports/operational_dataset.json`, `.md`입니다. 기존 결과는 덮어쓰지 않으므로 재구성은 새 `--output-dir`로 실행합니다. 원본이 없는 checkout에서는 먼저 캠페인을 수집하고 `--campaign-dir`로 지정합니다.

데이터셋 구성 단계 자체에서는 scaler와 모델을 fit하지 않습니다. 데이터셋 설명은 `docs/operational_dataset.md`, 최신 진행 상태와 남은 큰 작업 묶음은 `docs/project_roadmap.md`에서 확인합니다.

운영 Logistic Regression 초기 기준 모델 학습:

```powershell
python .\training\train_operational_logistic_regression.py
```

고정 데이터셋의 train 126건만으로 StandardScaler와 LR을 학습하고 validation 138건으로 평가했습니다. 최대 반복 1000회 중 12회에서 수렴했고, validation 정상 오탐은 22건, 이상 누락은 11건입니다. test CSV는 해시 검사에도 읽지 않았고 threshold=0.5는 사전에 고정했습니다.

모델 묶음은 `artifacts/operational_message/logistic_regression_v1/`, 결과는 `reports/operational_logistic_regression.json`과 `.md`입니다. 기존 출력이 있으면 새 `--artifact-dir`과 `--report-prefix`를 지정합니다. 설명과 실행 순서는 `docs/operational_logistic_regression.md`에 정리했습니다.

FastAPI는 여전히 dummy 추론을 사용합니다. 아래 RF 초기 학습·비교와 validation 선정까지 완료했으며, 최종 test 평가는 이후에 진행합니다.

운영 RandomForest 학습과 동일 validation 비교:

```powershell
python .\training\train_operational_random_forest.py
python .\evaluation\compare_operational_baselines.py
```

같은 train 126건으로 트리 100개·seed 42·threshold=0.5를 고정해 학습했습니다. RF는 scaler 없이 원본 입력 7개를 사용합니다. 저장된 두 모델의 입력·분할 해시와 schema·보고서·예측 기록을 검증하고 validation 138건을 재예측했습니다. test CSV는 읽지 않습니다.

RF는 FP 22→14건으로 오탐을 줄였지만 FN 11→29건으로 이상 누락이 늘었습니다. F1은 LR 76.26%, RF 61.95%로 현재 조건에서 RF의 전체 개선을 주장하지 않습니다. 학습 결과는 `reports/operational_random_forest.json`과 `.md`, 비교는 `reports/operational_baseline_comparison.json`과 `.md`입니다.

설명·파일 역할·재실행 방법은 `docs/operational_random_forest.md`에 정리했습니다. 최종 모델은 아직 선정하지 않았습니다.

운영 Validation 설정·threshold 선정:

```powershell
python .\training\run_operational_validation_experiment.py
```

LR C=0.1/1/10, RF 최소 leaf 표본 수=1/5/10과 threshold 0.3~0.9를 학습 전에 고정했습니다. 정상 FPR 10% 이하·이상 Recall 50% 이상 중 Recall 최대를 선정했습니다. 후보 학습은 Train만, 선정은 Validation만 사용하고 Test CSV는 해시 검사에도 열지 않았습니다.

42개 조합 중 제약 충족은 6개였고 LR C=1·threshold=0.7이 임시 baseline으로 선정됐습니다. FP는 초기 LR 22→3건으로 줄었지만 FN은 11→24건으로 늘었으며 malformed 두 시나리오는 모두 놓쳤습니다. 전체 성능 개선이나 최종 배포 가능성을 주장하지 않습니다.

실험은 `artifacts/operational_message/validation_selection_v1/`, 보고서는 `reports/operational_validation_experiment.json`과 `.md`입니다. `selection.json`의 모델 경로와 threshold를 함께 사용해야 하며 후보 기본 정책의 0.5와 구분합니다. 기존 결과는 덮어쓰지 않으므로 재실행은 새 `--artifact-dir`과 `--report-prefix`로 지정합니다.

개념·흐름·선정 근거는 `docs/operational_validation_experiment.md`에 정리했습니다. 로드맵 1단계는 완료했고 최종 마무리까지 6단계가 남았습니다.

선정 모델 오분류·관측 한계 분석:

```powershell
python .\evaluation\analyze_operational_errors.py
```

Validation FP 3건·FN 24건을 원본 행 위치·입력·확률·LR 점수 기여도와 함께 추적했습니다. burst 누락은 네 세션의 첫 네 메시지에, 정상 묶음 오탐은 한 세션의 6~8번째 메시지에 모였습니다. 너무 짧은 본문의 크기 단서와 같은 크기에서 잘못된 문자열 길이 필드가 입력에 없는 문제를 구분했습니다. Test와 원본 JSONL은 읽지 않았고 모델·threshold를 바꾸지 않았습니다.

보고서는 `reports/operational_error_analysis.json`, `.md`, `.csv`입니다. 기존 결과는 덮어쓰지 않으므로 재실행은 새 `--report-prefix`로 지정합니다. 설명은 `docs/operational_error_analysis.md`에 정리했습니다.

독립 보강 설계는 `configs/operational_augmentation_plan.json`, `docs/operational_data_augmentation.md`입니다. 이 JSON은 기존 `CampaignPlan` 명령에 직접 넣는 설정이 아니라 새 명령에서 구체적 v2 계획으로 변환하는 사전 설계입니다.

독립 캠페인 수집·분할:

```powershell
python .\scripts\collect_operational_augmentation.py
```

묶음 크기 1/4/8개, 정상 분할 조각 7/19/31바이트, 동시 연결 1/2/4개, 정상 대기 범위와 시나리오 순서를 사전 교차 배치했습니다. 모든 캠페인 역할·seed·기준 모델 해시를 첫 송신 전에 저장하고 실제 sendall 호출·JSONL·응답·종료를 대조했습니다.

완료 batch는 `aug_261013_e1cafdde`입니다. 캠페인 6개·run 84개·세션 336개에서 메시지 3,122건과 종료 요약 336건을 수집했고 84/84 run이 통과했습니다. 헤더 거부 12개 run은 메시지가 없어 별도 coverage로 기록하며 메시지 데이터셋에는 72개 run·288개 세션을 사용합니다.

| 분할 | 캠페인 | 메시지 | 정상 | 통제된 이상 |
| --- | ---: | ---: | ---: | ---: |
| Train | 3 | 1,538 | 1,049 | 489 |
| Validation | 1 | 513 | 349 | 164 |
| Test | 2 | 1,071 | 762 | 309 |

원본은 `data/raw/operational_augmentation/<batch_id>/`, 새 데이터셋은 `data/processed/operational_message/v2_campaign_holdout/`입니다. 이전 v1·선정 모델은 보존했습니다. 캠페인/run/session/row 분할 중복과 원본 누락·레코드 중복은 0이며 같은 feature 값의 분할 간 그룹은 2개로 별도 보고했습니다. 재구성한 12개 CSV/schema/split 파일의 해시도 일치했습니다.

보고서는 `reports/operational_augmentation_collection.json/.md`, `reports/operational_dataset_v2.json/.md`, 설명은 `docs/operational_augmentation_collection.md`입니다. 기본 결과가 이미 있으면 덮어쓰기 없이 종료하므로 재수집에는 새 데이터셋·두 보고서 경로를 지정합니다.

수집·분할 단계에서는 모델 fit·예측·Test 성능 평가를 하지 않았습니다. 이후 아래 고정 모델의 새 Validation 진단까지 완료했습니다. FastAPI는 계속 dummy 상태입니다.

기존 LR의 새 Validation 진단:

```powershell
python .\evaluation\audit_frozen_operational_baseline.py
```

기존 v1 Train 126건에서 학습한 scaler·LR과 threshold 0.7을 그대로 사용해 새 Validation 513건만 예측했습니다. 새 Train은 공통 로더의 형식·해시 검사에만 읽고 학습·예측에 넣지 않았습니다. 기존 v1 CSV·Test CSV·원본 JSONL·종료 summary는 읽지 않았으며 모델·threshold도 변경하지 않았습니다.

실제 결과는 TN 257·FP 92·FN 46·TP 118, Accuracy 73.10%·Precision 56.19%·Recall 71.95%·F1 63.10%·ROC-AUC 0.8342·정상 FPR 26.36%입니다. 기존 FPR 10% 이하 기준은 충족하지 못했습니다. 정상 일반/묶음에서 FP 41/51건이 발생했고 malformed 두 시나리오 16건은 모두 놓쳤습니다. burst 8개 세션 모두 한 번 이상 잡았어도 초반 메시지 30건은 놓쳤습니다.

기존 선정 Validation과 표본·설정·비율이 다르므로 점수 차이를 모델 개선 성과로 주장하지 않습니다. 진단은 `reports/operational_frozen_baseline_audit.json/.md`, 전체 예측은 `.predictions.csv`, 오분류는 `.errors.csv`, 설명은 `docs/operational_frozen_baseline_audit.md`입니다. 결과가 이미 있으면 재실행에는 새 `--report-prefix`가 필요합니다.

후속 보강 학습·비교까지 아래 단계에서 완료했습니다. 새 Test는 최종 평가까지 보류합니다.

새 Train 전용 보강 LR/RF 실험:

```powershell
python .\training\run_operational_augmentation_experiment.py
```

LR C=0.1/1/10, RF leaf=1/5/10과 threshold 0.3~0.9, FPR 10% 이하·Recall 50% 이상 중 Recall 최대 규칙을 학습 전에 고정했습니다. 후보 6개를 새 Train 1,538건으로 각각 한 번 학습하고 같은 Validation 513건에서 42개 조합을 비교했습니다. LR scaler는 새 Train만으로 fit했고 RF는 원본 입력을 사용했습니다. Validation은 이미 기존 진단에 사용했다는 사실을 공개하며 Test는 읽지 않았습니다.

18개 조합이 제약을 충족했고 **RF leaf=5·threshold=0.3**이 선정됐습니다. 같은 Validation에서 기존 LR의 FP/FN 92/46건이 선정 RF에서는 30/9건, FPR 26.36%→8.60%, Recall 71.95%→94.51%, F1 63.10%→88.83%였습니다. 반면 같은 LR C=1·0.7을 재학습하면 FP 0·FN 134건이어서 데이터만 늘리면 모든 정책이 좋아진다고 주장하지 않습니다.

정상 묶음 FPR 16.15%는 여전히 높고, malformed 16건을 잡았어도 입력에 없는 본문 유효성을 학습한 증거는 아닙니다. 현재 제한된 생성기·Validation에서의 개발 결과이며 독립 Test·실제 침입 성능·최종 배포 검증은 아닙니다.

산출물은 `artifacts/operational_message/augmentation_v1/`, 선정 보고서는 `reports/operational_augmentation_validation.json/.md`, 비교는 `reports/operational_augmentation_comparison.json/.md`입니다. 기존 모델·진단·데이터는 보존했고 설정·동일 행 오분류 변화·prefix·재현·실행 방법은 `docs/operational_augmentation_experiment.md`에 정리했습니다. 기존 결과가 있으면 새 `--run-dir`, `--selection-report-prefix`, `--comparison-report-prefix`를 지정합니다.

이 보강 학습 당시 전체 테스트 203개가 통과했고 동일 설정·seed의 재현 학습도 지표·선정·비교 CSV 해시가 일치했습니다. 데이터 보강 단계는 완료했으며, 이어서 아래 시계열 입력 준비를 마쳤습니다. FastAPI는 아직 dummy 상태입니다.

운영 메시지 시계열 창 구성:

```powershell
python .\scripts\prepare_operational_sequences.py
```

같은 campaign/run/session에서 현재까지 받은 최근 최대 8개 메시지만 묶습니다. 오른쪽 padding·유효 lengths·mask를 함께 저장하며, 메시지 하나당 창 하나와 현재 메시지의 정답을 유지합니다. 원본 행 순서를 보존해 기존 RF와 같은 Validation 메시지에서 비교할 수 있습니다.

| 분할 | 입력 shape | 세션 | 길이 8 | 길이 1~7 | 길이 1 |
| --- | --- | ---: | ---: | ---: | ---: |
| Train | `(1538, 8, 7)` | 144 | 822 | 716 | 144 |
| Validation | `(513, 8, 7)` | 48 | 274 | 239 | 48 |

malformed의 Train 48건·Validation 16건도 길이 1로 유지했습니다. 저장 후 원본 값·위치·정답을 재검증했고, 다시 구성한 배열·metadata·schema 5개 파일의 해시가 일치했습니다. 새 테스트 20개를 포함해 전체 223개가 통과했습니다.

데이터는 `data/processed/operational_sequence/v1/`, 보고서는 `reports/operational_sequence_dataset.json/.md`입니다. 기존 결과는 덮어쓰지 않으므로 재실행에는 새 `--output-dir`와 `--report-prefix`를 모두 지정합니다. 설명은 `docs/operational_sequence_dataset.md`에 정리했습니다.

시계열 입력 준비 당시에는 scaler fit·LSTM 학습·모델 예측·Test 접근을 하지 않았습니다. `configs/operational_lstm_plan.json`과 `docs/operational_lstm_plan.md`는 해당 시점의 사전 설계 기록이며, 아래 실제 학습 구현에서 그대로 검증해 사용했습니다.

운영 LSTM 직접 학습과 Validation 비교:

```powershell
python -m pip install --target .deps_lstm -r requirements-lstm.txt
python -X utf8 .\training\run_operational_lstm_experiment.py
```

기존 `.deps`와 분리된 CPU용 PyTorch를 사용합니다. 원본 Train 1,538행으로만 scaler를 한 번 fit하고 유효 timestep만 변환했습니다. hidden 크기 16/32, 최대 30 epoch, patience 5와 손실 최소 checkpoint를 고정해 직접 학습했습니다. 두 후보 모두 30 epoch를 사용했고 최적 epoch는 각각 30/27입니다.

LSTM 후보·threshold 14개 조합 중 5개가 제약을 충족해 **h16·threshold 0.5**가 선정됐습니다. 같은 Validation 513건에서 기존 RF와 다음 차이가 있었습니다.

| 모델 | FP | FN | FPR | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 선정 RF leaf=5·0.3 | 30 | 9 | 8.60% | 94.51% | 88.83% |
| 선정 LSTM h16·0.5 | 13 | 50 | 3.72% | 69.51% | 78.35% |

**LSTM은 오탐을 줄였지만 이상 누락이 늘어 전체 개선이라고 주장하지 않습니다.** malformed 16건을 모두 놓쳤고 burst FN도 9→34건으로 늘었습니다. LSTM 후보 내 선정과 RF 교체·최종 배포 승인을 구분합니다. 기존 RF는 변경하지 않았고 FastAPI도 여전히 dummy입니다.

모델은 `artifacts/operational_sequence/lstm_v1/`, 보고서는 `reports/operational_lstm_validation.json/.md/.predictions.csv`, 재현 증거는 `reports/operational_lstm_reproducibility.json`입니다. 기존 결과는 덮어쓰지 않으므로 재실행에는 새 `--artifact-dir`와 `--report-prefix`를 지정합니다. 상세 흐름·역할·한계는 `docs/operational_lstm_experiment.md`에 정리했습니다.

LSTM 단계 당시 새 테스트 22개를 포함해 전체 245개가 통과했습니다. 저장 checkpoint·scaler 재로딩이 일치했고 실제 재학습의 epoch 손실·가중치·14개 지표·예측 CSV 해시도 같았습니다. 해당 단계에서는 Test를 읽지 않았으며, 이후 아래 최종 평가까지 진행했습니다.

운영 모델 최종 성능·비용 평가:

```powershell
python -m pip install --target .deps -r requirements-evaluation.txt
python -X utf8 .\evaluation\run_operational_final_evaluation.py
```

기존 선정 LR C=0.1·0.5, RF leaf=5·0.3, LSTM h16·0.5를 고정했습니다. 모델별 별도 프로세스 3회에서 같은 Validation 요청 300개·워밍업 20회·배치 10회·CPU 계산 스레드 1로 측정하고, Validation과 비용으로 RF를 잠근 뒤 Test 1,071건을 처음 평가했습니다. 모델·scaler를 fit하거나 Test에서 후보·threshold를 고르지 않았습니다.

| 고정 모델 | Test FP | Test FN | Test FPR | Test Recall | Test F1 | 단일 중앙값 ms | 프로세스 peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LR | 113 | 131 | 14.83% | 57.61% | 59.33% | 0.670 | 150.11 |
| RF | 122 | 9 | 16.01% | 97.09% | 82.08% | 4.675 | 152.61 |
| LSTM | 60 | 104 | 7.87% | 66.34% | 71.43% | 0.947 | 380.93 |

비용은 Validation 기반 전처리·모델·threshold 포함 오프라인 경로입니다. HTTP·JSON·큐·history 관리·feature 검증은 제외하며 RSS는 라이브러리·입력·측정 캐시까지 포함하는 프로세스 전체입니다. 모델 파일 크기나 단일 forward의 증분 메모리와 다릅니다.

**RF의 Test FPR 16.01%가 사전 상한 10%를 넘어서 배포 승인을 보류했습니다.** 정상 묶음 오탐이 103/270건으로 집중됐습니다. Test를 보고 LSTM으로 바꾸지 않았고 승인 묶음도 생성하지 않았습니다. C++ 서버와 FastAPI dummy는 그대로 유지했습니다.

완료 산출물은 `artifacts/operational_final_evaluation/v2/`, 보고서는 `reports/operational_final_evaluation.json/.md/.predictions.csv`입니다. `v1/`은 Test 전 float32 수치 점검 실패 기록으로 보존했습니다. 수치 허용오차를 명시한 수정·재실행은 Test 공개 전에 수행했으며 모델·threshold·승인 기준은 변경하지 않았습니다.

설명은 `docs/operational_final_evaluation.md`, 이번 단계 커밋 제안은 `docs/operational_final_evaluation_commit.md`입니다. 기존 결과를 덮어쓰지 않으며 같은 holdout의 변경 정책 재평가는 사용 기록으로 거부합니다. 현재 Test는 이미 사용된 자료입니다.

전체 테스트 262개가 통과했고 고정 모델 재로딩·같은 Test 재예측 지표·확률·CSV 판단과 산출물 19개의 해시도 검증했습니다. 최종 평가·승인 보류 판단은 완료했습니다. **기본 남은 3개에 오탐 보정·새 독립 검증 1개를 추가해 실제 남은 큰 묶음은 4개입니다.** 다음은 바로 FastAPI 연결이 아니라 정상 묶음 오탐 보정과 새로운 독립 평가 설계입니다.
