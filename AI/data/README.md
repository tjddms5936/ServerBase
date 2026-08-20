# 데이터 폴더

이 폴더는 데이터 처리 단계에 따라 원본, 중간 결과, 최종 학습용 데이터를 나누어 관리합니다.

```text
data/
  raw/        다운로드한 원본 데이터 파일
  interim/    전처리 과정에서 생기는 임시 중간 파일
  processed/  학습 스크립트가 사용하는 정제된 train/test 파일
```

대용량 데이터 파일은 Git에 올리지 않습니다. 이 폴더에는 작은 설명 문서와 `.gitkeep` 파일만 추적합니다.

## raw/unsw_nb15

UNSW-NB15 원본 CSV 파일을 넣는 위치입니다.

필요 파일:

```text
UNSW_NB15_training-set.csv
UNSW_NB15_testing-set.csv
UNSW_NB15_features.csv
```

feature 설명 파일은 배포처에 따라 `UNSW-NB15_features.csv` 또는 `NUSW-NB15_features.csv` 이름으로 제공될 수 있습니다. 현재 스크립트는 세 가지 이름을 모두 인식합니다.

기본 EDA 실행:

```powershell
python .\scripts\inspect_unsw_nb15.py
```

## processed/unsw_nb15

`preprocess_unsw_nb15.py` 실행 결과가 저장되는 위치입니다.

주요 파일:

```text
X_train.csv
X_test.csv
y_train.csv
y_test.csv
attack_cat_train.csv
attack_cat_test.csv
```

`X_train.csv`, `X_test.csv`는 숫자형 표준화와 범주형 one-hot encoding이 끝난 모델 입력 데이터입니다. `y_train.csv`, `y_test.csv`는 정상/공격 이진 분류 label입니다.
