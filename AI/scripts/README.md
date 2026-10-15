# 스크립트

이 폴더는 데이터 확인, 전처리, 학습 실행처럼 반복해서 실행하는 Python 스크립트를 모아두는 곳입니다.

## prepare_operational_sequences.py

고정 메시지 데이터셋의 Train/Validation을 읽고 같은 campaign/run/session의 현재 메시지까지 최대 8개를 묶습니다. 짧은 창은 오른쪽 padding·유효 lengths·mask로 표현하며 원본 메시지 행 순서와 현재 정답을 보존합니다. scaler fit·모델 학습·예측·Test CSV 접근은 하지 않습니다.

```powershell
python .\scripts\prepare_operational_sequences.py
```

설정은 `configs/operational_sequence_dataset.json`, 결과는 `data/processed/operational_sequence/v1/`, 보고서는 `reports/operational_sequence_dataset.json/.md`입니다. NPZ·metadata를 저장한 뒤 재로딩해 모든 유효 timestep을 원본과 대조합니다. 설정 lock·입력/출력/code 해시를 보관하고 기존 원본을 바꾸지 않습니다.

기본 결과는 이미 생성했습니다. 재실행 시 기존 데이터셋과 보고서를 모두 보존하도록 두 출력 경로를 새로 지정합니다.

```powershell
python .\scripts\prepare_operational_sequences.py --output-dir .\data\processed\operational_sequence\replay_v1 --report-prefix .\reports\operational_sequence_replay
```

원본 번호 누락·중복, 감소하는 누적값, 다른 세션·split·미래 혼입과 잘못된 padding/정답은 구성을 중단합니다. 규칙과 파일 역할은 `docs/operational_sequence_dataset.md`, 다음 학습 설계는 `docs/operational_lstm_plan.md`에 있습니다. LSTM 학습 명령은 아직 구현하지 않았습니다.

## prepare_operational_dataset.py

운영 캠페인을 다시 검증해 메시지의 입력 7개·독립 정답·metadata를 분리하고 시간 순서의 run 단위 train·validation·test를 만듭니다. 모델이나 전처리기를 fit하지 않습니다.

```powershell
python .\scripts\prepare_operational_dataset.py
```

설정은 `configs/operational_dataset.json`, 기본 결과는 `data/processed/operational_message/v1/`, 보고서는 `reports/operational_dataset.json`과 `.md`입니다. 기존 결과를 덮어쓰지 않으므로 재실행에는 새 `--output-dir`를 사용합니다. 새 캠페인이나 여러 캠페인은 `--campaign-dir`로 지정합니다. 잘못된 원본·부족한 분할·식별자 누출은 구성을 중단하고 종료 코드 2를 반환합니다.

상세 정책과 CSV 행 대응 규칙은 `docs/operational_dataset.md`에 있습니다.

## collect_operational_augmentation.py

기존 보강 설계를 v2 생성기 설정으로 변환해 6개 캠페인의 역할·seed·기준 모델 해시를 송신 전에 고정합니다. 84개 run을 실제 로컬 C++/FastAPI로 수집·검증하고 캠페인 전체 단위로 Train 3개·Validation 1개·Test 2개를 배치합니다. 모델 학습·예측은 하지 않습니다.

```powershell
python .\scripts\collect_operational_augmentation.py
```

기본 데이터셋은 `data/processed/operational_message/v2_campaign_holdout/`, 보고서는 `reports/operational_augmentation_collection.json/.md`와 `reports/operational_dataset_v2.json/.md`입니다. 기존 데이터셋·보고서는 덮어쓰지 않습니다. 재수집에는 `--dataset-output`, `--report-prefix`, `--dataset-report-prefix`를 모두 새 경로로 지정합니다. `--prepare-only`는 기대값만 저장하고 송신하지 않습니다.

새 원본은 `data/raw/operational_augmentation/<batch_id>/`에 저장합니다. 실행 절차·실패 처리·송신 계측·재구성은 `docs/operational_augmentation_collection.md`를 확인합니다. 설계 JSON은 기존 `collect_operational_traffic.py --plan`이 아니라 이 새 명령에서 사용합니다.

## collect_operational_traffic.py

정상·통제된 이상 트래픽을 로컬 C++ 서버에 보내고, 시나리오별 새 run으로 실제 feature를 수집한 뒤 품질을 검사합니다. 두 서버와 클라이언트의 실행·종료를 자동으로 처리합니다.

```powershell
python .\scripts\collect_operational_traffic.py
```

기본 계획은 7개 시나리오 × 3회입니다. 원본은 `data/raw/operational_campaigns/<campaign_id>/`, 최신 검증 보고서는 `reports/operational_data_quality.json`과 `.md`에 저장합니다. 이미 사용 중인 4000/8000 포트는 건드리지 않으며 외부 주소로 연결하지 않습니다. `--repeats 1 --dataset-purpose integration_test`는 학습 후보에서 제외되는 빠른 검사입니다.

실행 전에 작성한 기대값, 실제 응답, JSONL 누락·중복, 독립 정답과 종료 통계를 함께 검증합니다. 상세 시나리오와 한계는 `docs/traffic_generation.md`에서 확인합니다.

## run_collection_api.py

독립 시나리오 manifest와 C++ 실행 파일 SHA-256을 이용해 새 run을 구성하고, JSONL 수집이 켜진 단일 worker FastAPI를 실행합니다. 이 스크립트가 C++ 서버나 트래픽 발생기를 대신 실행하지는 않습니다.

```powershell
python .\scripts\run_collection_api.py --scenario-id normal_chat
```

API 시작 후 표시된 C++ 실행 파일을 시작합니다. 기본 8000/4000 포트가 비어 있어야 합니다. 새 run ID는 자동 생성하며 `--run-id`로 지정할 수도 있습니다. 같은 run 디렉터리는 재사용하지 않습니다. 기본 빌드가 아니면 `--server-exe`로 실제 실행 파일을 지정합니다.

결과는 `data/raw/operational/<run_id>/`에 저장됩니다. 종료 순서와 유실·정답의 해석은 `docs/operational_data_collection.md`에서 확인합니다.

## inspect_unsw_nb15.py

`UNSW-NB15` 원본 CSV 파일이 올바른 위치에 있는지 확인하고, row 수, column 수, label 분포, 결측치 같은 기본 EDA 결과를 출력합니다.

실행:

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\inspect_unsw_nb15.py
```

결과 파일:

```text
AI/reports/dataset_profile_unsw_nb15.json
```

## preprocess_unsw_nb15.py

`UNSW-NB15` 원본 train/test CSV를 baseline 모델 학습에 바로 사용할 수 있는 형태로 변환합니다.

처리 내용:

- `id`, `label`, `attack_cat`은 입력 feature에서 제외합니다.
- `label`은 이진 분류 target으로 분리합니다.
- `attack_cat`은 추후 공격 유형 분석용 target으로 따로 저장합니다.
- 숫자형 feature는 결측치를 중앙값으로 채운 뒤 표준화합니다.
- 범주형 feature는 결측치를 `unknown`으로 채운 뒤 one-hot encoding합니다.
- 전처리기는 train 데이터로만 학습하고, test 데이터에는 같은 전처리기를 적용합니다.

실행:

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\preprocess_unsw_nb15.py
```

빠른 테스트용 샘플 실행:

```powershell
python .\scripts\preprocess_unsw_nb15.py --sample-size 1000
```

결과 파일:

```text
AI/data/processed/unsw_nb15/X_train.csv
AI/data/processed/unsw_nb15/X_test.csv
AI/data/processed/unsw_nb15/y_train.csv
AI/data/processed/unsw_nb15/y_test.csv
AI/data/processed/unsw_nb15/attack_cat_train.csv
AI/data/processed/unsw_nb15/attack_cat_test.csv
AI/artifacts/preprocessor_unsw_nb15.joblib
AI/artifacts/feature_schema_unsw_nb15.json
AI/artifacts/label_map_unsw_nb15.json
AI/reports/preprocess_summary_unsw_nb15.json
```

원본 feature 설명 파일은 배포처에 따라 파일명이 다를 수 있으므로, 스크립트는 아래 이름을 모두 허용합니다.

```text
UNSW_NB15_features.csv
UNSW-NB15_features.csv
NUSW-NB15_features.csv
```

## collect_operational_recovery.py

고정한 정상 묶음 오탐 보정 계획을 실행합니다. 실제 송신 전에 캠페인 6개의 역할·seed·출력 경로·기준 RF·코드·빌드와 378개 run의 기대값/설정을 잠급니다. 기존 수집용 FastAPI와 C++ 서버를 run마다 실행·종료하고 6개 모두 품질 검사를 통과한 경우에만 캠페인 전체를 Train/Validation/Test 3/1/2 역할로 분리합니다.

```powershell
python -X utf8 .\scripts\collect_operational_recovery.py
```

포트 4000·8000은 비어 있어야 합니다. 사용자가 실행한 기존 서버를 강제로 종료하지 않습니다. `--prepare-only`는 송신 없이 기대값과 잠금 파일만 생성하고, `--prepared-batch`는 아직 수집을 시도하지 않은 준비 batch의 잠근 출력 경로로 실행합니다. 기존 데이터셋·보고서와 시도한 batch를 덮어쓰거나 재송신하지 않습니다.

완료 자료는 `data/processed/operational_message/recovery_v1/`와 `reports/operational_recovery_collection.json/.md`, `reports/operational_recovery_dataset.json/.md`입니다. 실제 수집은 메시지 16,120건이며 모델 학습·예측·새 Test 성능 평가는 하지 않았습니다. 재수집 시 새 출력 경로와 필요한 이전 산출물은 `docs/operational_recovery_collection.md`에서 확인합니다.
## 실제 승인 RF API 실행

```powershell
python -X utf8 .\scripts\run_inference_api.py
```

승인 설정의 완료 기록·묶음·모델·schema·정책을 검증해 127.0.0.1:8000에서 실제 RF 추론을 실행합니다. 기동 실패를 dummy로 대체하지 않습니다. `--config`와 `--port`를 지정할 수 있으며 자동 검사만 `--stdin-control`을 사용해 소유한 프로세스에 quit을 전달합니다. 일반 실행은 Ctrl+C로 종료합니다.

마우스 실행은 AI 폴더의 `run_fastapi.bat`을 사용합니다. 기존 수집 실행기는 별도 dummy 경로를 유지합니다. 실제 HTTP 검사 명령과 범위는 `docs/operational_rf_api.md`에 있습니다.
