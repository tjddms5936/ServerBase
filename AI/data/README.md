# 데이터 폴더

이 폴더는 데이터 처리 단계에 따라 원본, 중간 결과, 최종 학습용 데이터를 나누어 관리합니다.

```text
data/
  raw/        다운로드한 원본 데이터 파일
  interim/    전처리 과정에서 생기는 임시 중간 파일
  processed/  학습 스크립트가 사용하는 정제된 데이터와 고정 분할
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

## raw/operational_recovery

정상 묶음 오탐 보정의 새 캠페인 6개를 보관합니다. `assignment_manifest.json`은 송신 전 역할·실제 id·출력 위치·모델/코드/빌드 해시, `prepared_manifest.json`은 모든 run 설정·기대값의 해시를 저장합니다. 각 캠페인의 실제 JSONL·독립 정답·송수신 결과·종료 통계·로그·품질 보고서를 보존합니다.

`rec_261014_3878ea96` batch는 378/378개 run·1,512개 세션·16,120개 메시지·1,512개 종료 요약의 수집을 완료했습니다. `completion_manifest.json`으로 보호 입력·원본·출력 해시를 확인합니다. 실패하거나 중단한 batch는 같은 폴더에서 재송신하지 않으며 원본을 자동 삭제하지 않습니다.

## processed/operational_message/recovery_v1

기존 원본 v2 메시지 feature 7개를 유지한 새 보정 데이터셋입니다. 캠페인 3/1/2 역할에 따라 Train 7,976건·Validation 2,737건·Test 5,407건을 저장했습니다. X/y/metadata의 같은 행은 같은 메시지이며 원본으로 재대조했습니다. 파생 입력·scaler·모델은 아직 학습하지 않았습니다.

헤더 거부 54개 run은 종료 요약으로 품질 검사하되 메시지 모델에는 넣지 않습니다. split 간 식별자 중복은 0건이고 동일 feature 값의 반복은 별도 보고했습니다. 새 Test의 품질·분할·해시는 확인했지만 모델 예측·threshold 선정·feature 범위 탐색에는 사용하지 않았습니다. 이전에 노출된 v2 Test와 구분합니다.

원본과 처리 데이터는 Git에서 제외합니다. 실제 수집·검증·재수집·한계는 `docs/operational_recovery_collection.md`를 확인합니다.

## processed/operational_sequence

고정 메시지 데이터셋에서 같은 campaign/run/session의 과거·현재 최대 8개를 묶은 시계열 입력을 보관합니다. 기본 `v1/`에는 Train/Validation NPZ, 원본 행 추적 metadata CSV, 창 구성 전 설정 lock, sequence schema와 검증 완료 manifest가 있습니다. Test 창은 아직 만들지 않습니다.

Train `(1538, 8, 7)`, Validation `(513, 8, 7)`이며 하나의 현재 메시지당 창 하나와 원본 정답을 유지합니다. NPZ의 `X`는 아직 정규화하지 않은 원본 수치이고, `lengths`·`mask`는 오른쪽 padding을 제외하는 구조 정보입니다. `source_positions`는 각 유효 timestep을 원본 CSV 행으로 추적합니다. metadata와 정답을 추가 학습 feature로 넣지 않습니다.

짧은 세션과 최초 메시지도 유지합니다. 다음 LSTM scaler는 겹친 창을 펼치지 않고 원본 Train 메시지 1,538행으로만 fit해야 합니다. 저장 데이터는 Git에서 제외하며 실행·검증·재현·한계는 `docs/operational_sequence_dataset.md`에 정리했습니다.

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
