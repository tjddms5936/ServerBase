# 스크립트

이 폴더는 데이터 확인, 전처리, 학습 실행처럼 반복해서 실행하는 Python 스크립트를 모아두는 곳입니다.

## prepare_operational_dataset.py

운영 캠페인을 다시 검증해 메시지의 입력 7개·독립 정답·metadata를 분리하고 시간 순서의 run 단위 train·validation·test를 만듭니다. 모델이나 전처리기를 fit하지 않습니다.

```powershell
python .\scripts\prepare_operational_dataset.py
```

설정은 `configs/operational_dataset.json`, 기본 결과는 `data/processed/operational_message/v1/`, 보고서는 `reports/operational_dataset.json`과 `.md`입니다. 기존 결과를 덮어쓰지 않으므로 재실행에는 새 `--output-dir`를 사용합니다. 새 캠페인이나 여러 캠페인은 `--campaign-dir`로 지정합니다. 잘못된 원본·부족한 분할·식별자 누출은 구성을 중단하고 종료 코드 2를 반환합니다.

상세 정책과 CSV 행 대응 규칙은 `docs/operational_dataset.md`에 있습니다.

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
