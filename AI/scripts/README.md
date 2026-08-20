# 스크립트

이 폴더는 데이터 확인, 전처리, 학습 실행처럼 반복해서 실행하는 Python 스크립트를 모아두는 곳입니다.

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
