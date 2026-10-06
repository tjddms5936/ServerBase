# 스크립트

이 폴더는 데이터 확인, 전처리, 학습 실행처럼 반복해서 실행하는 Python 스크립트를 모아두는 곳입니다.

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
