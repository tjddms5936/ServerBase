# 운영 메시지 데이터셋 구성과 고정 분할

## 이번 단계에서 한 일

기존 다중 run 캠페인을 다시 검증하고 **message 이벤트만** 추출해 모델 입력 7개, 독립 정답, 추적용 메타데이터로 분리했다. 모델 학습, scaler fit, threshold 선택은 하지 않았다. 공개 UNSW 데이터와 운영 원본을 합치거나 입력을 서로 치환하지 않는다.

| 파일 | 역할 |
| --- | --- |
| `data_preparation/__init__.py` | 데이터 준비 패키지 선언 |
| `data_preparation/operational_dataset.py` | 원본 재검증, 메시지 추출, 반복 묶음 분할, 누출 검사, CSV·manifest 생성 |
| `configs/operational_dataset.json` | 원본 캠페인, 출력 경로, 의미 버전과 고정 분할 정책 |
| `scripts/prepare_operational_dataset.py` | 설정을 읽고 실제 데이터셋과 보고서를 생성하는 진입점 |
| `tests/test_operational_dataset.py` | 원본 오류·분할 부족·식별자 누출·중복 feature·출력 실패 검사 |
| `reports/operational_dataset.json` | 분할 수·정답·시나리오 분포, 제외 사유, 원본·출력 해시 |
| `reports/operational_dataset.md` | 실제 데이터셋의 한글 요약 보고서 |

## 어떤 원본을 사용하는가

설정의 기본 원본은 이전 단계에서 보관한 `campaign_20261012T050123_b3017a53`이다. 최신 보고서 경로를 자동으로 따라가지 않고 설정에 입력을 고정한다. 새로 수집한 캠페인은 `--campaign-dir`로 명시한다. 여러 캠페인도 지정할 수 있다.

원본을 읽을 때 기존 품질 검증기 `audit_campaign`을 다시 호출한다. 이미 생성된 PASS 보고서만 믿고 건너뛰지 않는다. 현재 의미 명세의 해시·내용, 독립 정답, 원본 JSONL, 송수신 결과와 종료 통계를 확인한다.

- `integration_test` 캠페인은 제외한다.
- 불완전·오류 run은 사유를 남기고 제외한다. 제외 후 정답·시나리오 coverage가 부족하면 구성을 중단한다.
- 헤더 거부 run은 정상적인 메시지 관측 불가로 기록하지만 메시지 데이터셋에서는 제외한다.
- campaign/run 식별자나 계획 자체의 검증 실패는 조용히 제외하지 않고 전체 구성을 중단한다.
- 같은 입력 캠페인을 중복 지정하거나 서로 다른 경로의 식별자가 겹치면 중단한다.

원본과 의미 명세 SHA-256을 추출 전후에 대조해 구성 중 변경을 검출한다. 원본 파일을 수정하거나 실패 원본을 삭제하지 않는다. 해시는 내용 대조용이며 악의적 변경의 인증이나 서명은 아니다.

## 모델 입력과 정답

각 메시지의 `payload.features`에서 아래 순서대로만 값을 가져온다. 이름이 같은 session 요약 값으로 대체하지 않는다.

```text
packet_size
payload_size
connection_duration_ms
message_interval_ms
bytes_received_total
bytes_sent_total
error_count_total
```

정답은 원본의 독립 시나리오 manifest에서 검증한 정상 0 / 통제된 이상 1이다. dummy 응답, 오류 수 또는 종료 요약으로 다시 만들지 않는다. label은 메시지 자체의 실제 공격 여부가 아니라 사전에 정의한 run의 시나리오 의도다.

run/session ID, scenario_id, packet_id, timestamp, 오류 문구, 종료 요약은 모델 입력에서 제외한다. 추적·분포 분석·시계열 창 구성에 필요한 값은 별도 metadata CSV에 보관한다. 정수형 바이트·카운터는 내보낼 때 불필요하게 float로 바꾸지 않는다.

## 왜 행을 섞지 않는가

한 세션에서 여러 메시지가 발생하면 누적 바이트, 연결 시간과 메시지 간격이 서로 연관된다. 같은 세션의 일부 메시지가 train에, 나머지가 test에 들어가면 독립 평가로 해석하기 어렵다. 그래서 **run 전체를 하나의 분할에 넣어 세션도 함께 보호**한다.

동일 캠페인의 같은 `repeat_index`에 속한 여러 시나리오 run을 하나의 반복 묶음으로 관리한다. 묶음은 실제 API 시작·수집 종료 UTC 시각으로 정렬한다. 서로 다른 묶음의 시간 범위가 겹치면 현재 정책에서 지원하지 않는 것으로 중단한다.

기본 설정은 마지막 1개 묶음을 test, 그 앞 1개를 validation, 남은 앞부분을 train으로 사용한다. 현재는 묶음이 3개뿐이므로 다음과 같다.

| 반복 묶음 | 역할 | run | 세션 | 메시지 | 정상 | 통제된 이상 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 첫 번째 | train | 6 | 24 | 126 | 64 | 62 |
| 두 번째 | validation | 6 | 24 | 138 | 74 | 64 |
| 마지막 | test | 6 | 24 | 142 | 76 | 66 |

일반적인 60:20:20 등의 행 비율보다 독립 실행을 보존하는 것을 우선했다. 전체는 18개 run·72개 세션·406개 메시지이며, 제외한 헤더 거부 run은 3개다. 각 분할에 정상·이상 정답과 현재 사용 가능한 6개 시나리오가 모두 있어야 한다.

여러 순차 캠페인을 지정하면 더 이른 묶음은 train에 포함되고 마지막 묶음이 validation/test가 된다. 캠페인 전체를 반드시 한 분할에 넣는 정책은 아니다. **별도 캠페인 일반화 평가**가 필요하면 후속 독립 캠페인의 묶음을 충분히 확보하고 별도 평가 정책을 명시해야 한다.

## 누출과 같은 feature 값 검사

분할 쌍마다 다음 집합의 교집합이 0인지 검사한다.

- run: `run_id`
- session: `(run_id, session_id)`
- 메시지: 캠페인·run·session·request 번호로 만든 `row_id`

C++ 세션 번호는 서버 재시작 후 재사용되므로 session_id 단독으로 중복 여부를 판단하지 않는다. 데이터셋 내부 메시지 ID 중복도 금지한다.

별도로 입력 7개 전체의 해시를 계산해 같은 feature 벡터가 여러 행 또는 여러 분할에 있는지 검사한다. 서로 다른 정답을 가진 같은 feature 그룹도 보고한다. 기본 `duplicate_feature_policy: report`는 사실을 기록하되 서로 다른 실행을 자동 삭제하거나 정답을 고치지 않는다. `error`로 지정하면 분할 간 같은 feature 그룹이 있을 때 내보내기 전에 중단한다.

이번 실제 원본에서는 식별자 중복, 전체 동일 feature 그룹, 분할 간 동일 feature 그룹이 모두 0개다. 이것이 유사한 행동·같은 생성기 편향·시나리오 수준 연관성이 없다는 뜻은 아니다.

## 저장 형식과 행 대응

```text
data/processed/operational_message/v1/
  X_train.csv / y_train.csv / metadata_train.csv
  X_validation.csv / y_validation.csv / metadata_validation.csv
  X_test.csv / y_test.csv / metadata_test.csv
  feature_schema.json
  label_map.json
  split_manifest.json
  dataset_manifest.json
```

`X`에는 입력 7개만, `y`에는 label만 저장한다. 같은 분할의 X/y/metadata에서 **같은 행 위치는 같은 메시지**다. run 시작 시각으로 정렬한 뒤 각 run 내부에서는 session_id·request_index 순서를 보존한다. label 파일만 독립적으로 섞으면 안 된다.

metadata는 추적용 row_id, 캠페인·run·시나리오, 반복 번호, 세션·메시지 번호, 큐 순번, 두 UTC 시각, 원본 JSONL 행 번호, split/block ID 및 feature 해시를 보관한다. 모델을 fit할 때 metadata CSV를 X에 합치지 않는다.

feature_schema는 정확한 입력 순서·단위·타입·의미 버전과 **전처리 미학습 상태**를 저장한다. split_manifest는 고정 run 배치와 교집합·중복 검사를 보관한다. dataset_manifest는 전체 설정, 원본·생성 코드·출력 파일 해시와 데이터셋 통계를 보관한다.

결과 디렉터리가 이미 있으면 덮어쓰지 않는다. 실패한 출력은 완료 manifest가 없을 수 있으므로 학습에 사용하지 않고 새 경로로 다시 구성한다. 결과는 기존 processed ignore 규칙으로 Git에서 제외한다.

## 실행 방법

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\prepare_operational_dataset.py
```

기본 v1이 이미 만들어져 있으면 위 명령은 덮어쓰기 방지로 종료 코드 2를 반환한다. 같은 원본으로 별도 검증 결과를 만들려면 새 출력 경로를 지정한다. 새로운 데이터셋 ID·정책도 필요하면 설정 파일을 변경해 별도 버전으로 관리한다.

```powershell
python .\scripts\prepare_operational_dataset.py --output-dir .\data\processed\operational_message\rebuild_check
python .\scripts\prepare_operational_dataset.py --campaign-dir "새_캠페인_절대_경로" --output-dir .\data\processed\operational_message\new_collection
python -m unittest discover -s tests -p "test_*.py"
```

보고서는 `reports/operational_dataset.json`과 `.md`이며 `--report-prefix`로 바꿀 수 있다. 기본 원본이 없는 새 checkout에서는 먼저 수집 도구로 캠페인을 만들고 경로를 명시해야 한다.

## 전처리와 모델 학습은 다음 단계

이번 CSV는 정규화하기 전의 검증된 원본 feature다. scaler·imputer·모델을 fit하지 않았다. 다음 단계에서 train의 X만으로 전처리기를 fit하고 validation/test에는 동일 객체의 transform만 사용한다. 잘못된 값이나 누락을 0으로 채우지 않는다.

train의 `error_count_total`은 전부 0이었다. 메시지 snapshot이 본문 검증 이전에 생성되므로 종료 뒤 발견된 오류를 이 입력에 가져오지 않는다. `packet_size`와 `payload_size`의 차이도 헤더 8바이트로 고정돼 있으나 의미 계약을 유지하려고 이번 단계에서 입력을 자동 삭제하지 않았다.

test의 분할·정답 수를 확인한 것은 데이터 품질 검사다. 그 결과로 모델 설정·feature 선택·threshold를 조정하지 않는다. 모델 선택은 validation에서 진행하고 test 성능은 최종 비교에서 평가한다.

시계열 모델은 이 분할을 완료한 후 각 split의 동일 run/session 안에서 창을 만들어야 한다. 원본 전체에서 창을 만든 뒤 행 단위로 섞으면 분할 경계와 세션 경계를 넘는 누출이 생길 수 있다.

## 새 캠페인 holdout 버전

기존 v1의 반복 단위 정책은 그대로 보존한다. 새 `v2_campaign_holdout/`에는 송신 전에 역할을 고정한 캠페인 전체를 배치한다. 설정의 `config_version=v2`, `split_policy=preassigned_campaign_holdout`, `campaign_registry`가 필요하다. registry의 역할·seed·원본 집합·계획·고정 시각을 검사하고 같은 캠페인의 모든 반복·run·세션·메시지를 한 split에 둔다.

캠페인 하나의 품질 검사가 실패하면 통과한 run만 골라 승인하지 않고 구성을 중단한다. 헤더 거부는 정상 품질 통과지만 메시지 관측 불가로 제외한다. 캠페인/run/session/row 중복과 같은 값의 feature 중복을 따로 확인한다.

실제 6개 캠페인에서 새 Train 1,538건, Validation 513건, Test 1,071건을 구성했다. 입력 7개와 의미 계약은 동일하며 scaler fit·모델 예측·Test 성능 평가는 하지 않았다. 구성·재현은 `docs/operational_augmentation_collection.md`, 결과는 `reports/operational_dataset_v2.md`를 참조한다.

## 다음 작업

운영 LR/RF 학습·비교·Validation 선정, 오분류 분석, 독립 캠페인 보강 수집·분할, 고정 기존 모델 진단과 새 Train 전용 보강 학습·비교까지 완료했다. 다음은 이 고정 split의 동일 run/session 내부 시계열 창을 구성하고 LSTM을 설계하는 것이다. 진단은 `docs/operational_frozen_baseline_audit.md`, 보강 실험은 `docs/operational_augmentation_experiment.md`에 정리했다.

초기 데이터 규모와 본문 관측 한계를 함께 기록하며, 개선 모델이 반드시 더 좋아질 것이라고 가정하지 않는다. 전체 남은 작업은 `docs/project_roadmap.md`에 정리했다.
