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

## raw/operational_campaigns

자동 트래픽 생성기의 여러 실행을 캠페인별로 보관하는 위치입니다. 사전 기대값·실행 설정·클라이언트 송수신 결과·프로세스 로그와 각 run의 실제 JSONL 원본, 독립 정답 manifest, 수집 종료 통계를 함께 보관합니다. 실행 후 원본을 자동 삭제하지 않으며 Git에는 올리지 않습니다.

품질 검사에서 잘못된·누락된·중복된 레코드와 정답 혼입을 검출하고, 메시지 관측 불가·연동 테스트 run을 메시지 학습 후보에서 제외합니다. 구조와 재검사 명령은 `docs/traffic_generation.md`에 있습니다.

## raw/operational_augmentation

독립 보강 캠페인 6개를 하나의 batch 아래 보관합니다. `assignment_manifest.json`에는 송신 전 고정한 역할·seed·구체적 생성기 계획·기준 모델 해시가, `prepared_manifest.json`에는 고정 파일 해시가 들어 있습니다. 각 캠페인에는 원본 JSONL·기대값·실제 송신 결과·프로세스 로그·품질 보고서가 있습니다. `batch_runtime.json`은 전체 실행 상태, `dataset_config.json`은 재구성할 때 사용하는 입력 경로·역할 registry입니다.

실패 원본은 삭제하거나 정상 run만 추려 승인하지 않습니다. 완료 batch와 준비만 실패한 폴더를 구분합니다. 실제 수집·재현 명령과 한계는 `docs/operational_augmentation_collection.md`를 확인합니다.

## processed/operational_message

검증된 운영 메시지의 고정 학습용 입력을 보관합니다. 기본 `v1/`에는 분할별 `X`, `y`, `metadata` CSV와 feature·label·분할·전체 데이터셋 manifest가 있습니다. 같은 행 위치가 같은 메시지이며 X에는 현재 원본 feature 7개만 들어갑니다. metadata나 종료 요약을 X에 합치지 않습니다.

원본을 다시 검증하고 run 전체를 시간 순서의 반복 묶음으로 train·validation·test에 배치합니다. 식별자와 같은 feature 값의 분할 간 중복을 따로 확인합니다. 아직 정규화·scaler 학습을 하지 않은 데이터이며 전처리 fit은 다음 학습 단계에서 train으로만 수행합니다.

저장 결과는 Git에서 제외하고 기존 경로를 덮어쓰지 않습니다. 실행·분할 정책·한계는 `docs/operational_dataset.md`에 있습니다.

새 `v2_campaign_holdout/`은 같은 7개 입력 의미를 유지하면서 캠페인 전체를 사전 역할대로 분리한 버전입니다. v1과 합치거나 덮어쓰지 않습니다. Train 3개·Validation 1개·Test 2개 캠페인의 모든 반복·run·세션·메시지가 해당 split에만 속하며 캠페인 식별자 중복도 검사합니다. 아직 scaler나 모델을 fit하지 않은 원본 수치입니다.

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
