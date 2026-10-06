# 데이터 폴더

이 폴더는 데이터 처리 단계에 따라 원본, 중간 결과, 최종 학습용 데이터를 나누어 관리합니다.

```text
data/
  raw/        다운로드한 원본 데이터 파일
  interim/    전처리 과정에서 생기는 임시 중간 파일
  processed/  학습 스크립트가 사용하는 정제된 train/test 파일
```

대용량 데이터 파일은 Git에 올리지 않습니다. 이 폴더에는 작은 설명 문서와 `.gitkeep` 파일만 추적합니다.

## raw/operational

C++에서 전송한 운영 feature를 비동기 JSONL 수집기로 저장하는 위치입니다. 다운로드한 UNSW 데이터와 섞지 않습니다.

각 `<run_id>/`에는 원본 message/session JSON을 담은 `features.jsonl`, 독립 정답·빌드·의미 버전의 `run_manifest.json`, 유실·저장·종료 통계의 `collection_summary.json`이 들어갑니다. 서버 재시작이나 시나리오 변경마다 새 run을 사용합니다. 정답은 모델의 예측이나 오류 수가 아니라 사전에 정의한 시나리오 manifest에서 가져옵니다.

실제 학습 전에는 불완전 run과 `integration_test` 실행을 제외하고 run/session 단위 분리·분포·정답의 한계를 검토해야 합니다. 실행 방법과 상세 규칙은 `docs/operational_data_collection.md`에 있습니다.

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
