# 선정 RF의 비용 검증과 Test 전 후보 잠금

## 이번 단계의 목적

앞 단계는 새 Train 7,976건으로 RF 입력 후보 4개를 직접 학습하고 새 Validation 2,737건에서 20개 정책을 비교한 작업이었다. 선정 결과는 `rf_raw7`, threshold `0.7`이다. 이번에는 이 정책을 바꾸지 않고 실제 추론 속도·프로세스 메모리·필수 파일 크기가 사전 예산 안에 드는지 확인한다.

비용을 통과해도 아직 배포 승인이 아니다. 모델과 판정 기준을 먼저 고정한 다음, 별도 단계에서 새로운 Test 5,407건으로 성능을 확인해야 한다. 이번 실행기는 Test를 열거나 접근 예약을 생성하지 않는다.

## 파일 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_recovery_cost.json` | 선정 모델·0.7 정책·입출력 경로·측정 반복 수·예상 데이터 크기를 고정한다. |
| `evaluation/run_operational_recovery_cost.py` | 완료된 학습 실험의 해시·선정 근거·원본 schema를 확인하고 후보 묶음, 비용 측정, 조건부 Test 전 잠금, 보고서를 만든다. |
| `evaluation/operational_cost_worker.py` | 기존 구현을 그대로 재사용한다. 독립 Python 프로세스에서 고정 Validation NPZ와 RF 한 개만 읽고 지연시간·메모리를 측정한다. |
| `evaluation/operational_final_models.py` | 기존 고정 추론 경로를 재사용한다. 파일 해시를 검증하고 RF의 열 순서·무전처리 추론 경로를 제공한다. |
| `tests/test_operational_recovery_cost.py` | Test·재학습 금지, 비용 실패, 경계값, 파일 변조, 환경 복원, 미승인 상태, 결과 보존을 검사한다. |
| `reports/operational_recovery_cost.json/.md` | 실제 비용 원본·요약·예산 판정·잠금 경로·한계를 저장한다. |

이전 학습 코드·보고서·모델을 수정하지 않는다. C++ 서버와 FastAPI도 이번 단계에서는 변경하지 않는다.

## 실행 흐름

1. 기존 결과가 있거나 원본 데이터·모델·수집 batch 내부에 출력하려 하면 실행을 거부한다.
2. 기존 Train/Validation 전용 로더로 입력 계약·정답·metadata 정렬·분할·SHA-256을 확인한다. Test CSV는 해시 확인에도 열지 않는다.
3. 학습 완료 manifest의 산출물·보고서와 과거 보호 파일, 학습 protocol 해시를 확인한다. 기록된 Validation 후보들의 선정 규칙도 다시 적용해 원래 선정 결과와 일치하는지 확인한다. 추가 모델을 예측하거나 새 threshold를 탐색하는 작업은 아니다.
4. 원본 RF·입력 schema·label map을 `candidate_bundle/`에 복사하고 선정 `decision_policy.json`을 따로 만든다. 원래 후보의 기본 정책 0.5는 그대로 둔다.
5. 복사한 모델의 설정·클래스 순서·입력 열·scaler 부재를 확인한다. 같은 Validation 전체의 확률·정답·행 식별자와 0.7 지표를 저장 예측에 정확히 대조한다.
6. `cost_protocol_lock.json`에 모델·정책·파일 해시·원본 입력 행렬·측정 설정·사전 승인 기준을 저장한 뒤 비용 측정을 시작한다.
7. CPU 계산 스레드 환경을 1개로 설정하고 서로 다른 Python 프로세스 3개를 순차 실행한다. 프로세스마다 동일한 Validation과 요청 목록을 쓴다.
8. 단일/배치 판단 일치, 스레드 설정, 모델 정책, 원본 시간 표본과 요약을 확인한다. 부모의 기존 스레드 환경은 성공·실패·중단 후 복원한다.
9. 비용·기존 Validation 성능 기준을 모두 통과한 경우에만 `candidate_lock.json`을 만든다. 모델·threshold·schema·Test 예상 해시·승인 규칙을 고정한다. 실제 Test 내용은 읽지 않는다.
10. 비용 통과 여부와 관계없이 완료된 측정은 보고서와 `cost_manifest.json`으로 기록한다. 실행 오류·timeout·중단은 `cost_failure.json`으로 보존하고 완료라고 기록하지 않는다.

## 비용 측정 개념

### 단일 요청 지연시간

모델에 feature 한 행을 주고 확률을 받은 뒤 `확률 >= 0.7`을 비교하는 데 걸리는 시간이다.

- 전처리 포함 경로: 원본 배열 → 순서가 있는 DataFrame → RF 확률 → threshold 판단.
- 모델 단독 경로: 이미 만들어 둔 DataFrame → RF 확률 → threshold 판단.

RF는 scaler 없이 원본 숫자를 사용한다. 이름이 전처리 포함이라고 해서 정규화를 새로 수행하는 것은 아니다. 선택하지 않은 prefix 평균 후보와 달리 raw7에는 추가 Python 세션 이력이 필요하지 않다. 서버가 이미 계산한 누적 수신량·간격 snapshot을 사용한다는 의미이며 C++의 세션 상태까지 없다는 뜻은 아니다.

워밍업은 각 경로 20회, 실제 단일 요청은 seed 42로 고른 같은 Validation 행 300개다. 개별 프로세스의 300개 측정 중 p95를 계산하고, 세 프로세스 중 가장 높은 p95를 승인 예산과 비교한다. p95는 측정 표본 약 95%가 그 시간 이하라는 뜻이며 모든 요청의 최대 시간 상한이 아니다.

확률의 부동소수점 차이는 절대 오차 `1e-5` 이내만 허용한다. 작은 오차라도 threshold를 넘어 판단이 뒤집히면 실패다.

### 배치 처리량

전체 Validation 2,737건을 한 번에 예측하는 시간을 10회 측정한다. 배치 시간/2,737은 처리량을 이해하기 위한 평균 비용이다. HTTP 요청 한 건의 응답시간으로 사용하지 않는다.

### 메모리와 저장 비용

- RSS: 운영체제가 보고한 해당 측정 프로세스의 물리 메모리 사용량이다.
- 네이티브 peak working set: 프로세스 시작부터 측정 시점까지의 최고 사용량이다. 모델뿐 아니라 라이브러리·입력·측정 캐시도 포함한다.
- USS·private commit·모델 로딩 전후 RSS 차이: 원본 측정 기록에 참고 정보로 남기며 주 승인 기준과 구분한다.
- 주요 모델 배열 바이트: 트리 노드·값·클래스 배열의 합이다. Python 객체 전체 크기를 완전히 계산한 값은 아니다.
- 필수 파일 합계: 모델, 원본 feature schema, 모델 입력 schema, label map, 선정 정책의 5개 파일 크기다. 측정용 NPZ·보고서·잠금 기록은 이 합계에 넣지 않는다.

다른 모델의 프레임워크 메모리를 섞지 않도록 측정 worker는 Torch를 로딩하지 않는다. RF `n_jobs=1`과 계산 라이브러리 스레드 1개를 확인하지만 운영체제의 전체 스레드 수가 1개라고 주장하지 않는다.

## 사전 승인 기준

| 기준 | 사전 한도 |
| --- | --- |
| 전체 정상 FPR | 10% 이하 |
| 정상 묶음 FPR | 10% 이하 |
| 이상 Recall | 50% 이상 |
| 가장 높은 프로세스의 단일 요청 p95 | 25ms 이하 |
| 프로세스 전체 네이티브 peak RSS | 512MiB 이하 |
| 필수 후보 파일 합계 | 50MiB 이하 |

성능 기준은 수집 전 보정 계획에 고정한 기준을 그대로 읽는다. 이번에는 기존 Validation 지표의 일치만 확인하고, 다음 Test 단계에서도 같은 기준을 유지한다. 비용 기준을 넘거나 메모리를 측정하지 못하면 다른 후보로 대체하거나 기준을 완화하지 않는다.

## 실제 실행 결과

선정 원본 RF·0.7의 비용은 사전 예산을 통과했다. 상태는 `candidate_locked_pending_independent_test`이며 아직 배포 승인은 아니다.

| 항목 | 실제 측정값 | 사전 상한 |
| --- | ---: | ---: |
| 전처리 포함 단일 요청 중앙값 | 4.3371ms | 참고값 |
| 가장 높은 프로세스의 단일 요청 p95 | 4.7538ms | 25ms |
| 모델 단독 중앙값 | 4.2116ms | 참고값 |
| 프로세스 전체 네이티브 peak RSS | 154.90MiB | 512MiB |
| 필수 후보 파일 5개 합계 | 759.05KiB | 50MiB |
| 주요 모델 배열 합계 | 2,926.58KiB | 참고값 |
| 전체 Validation 배치 중앙값 | 12.5505ms | 참고값 |

동일 300개 요청으로 서로 다른 프로세스 3개를 실행했다. 단일·배치 판단은 같고 측정 프로세스는 Torch를 로딩하지 않았다. 기존 Validation FP 39건·FN 275건, 전체 FPR 1.98%·정상 묶음 FPR 5.16%·Recall 63.96%도 그대로 재현됐다.

새 테스트 15개를 포함해 전체 319개 테스트가 통과했다. 실제 실행에도 Test·수집 레코드 접근, 모델·scaler 재학습, 네트워크 송신 금지 검사를 걸었다. 완료 후 산출물 12개·보고서 2개·보호 파일 96개의 해시, 비용 요약의 원본 시간 표본, Validation NPZ `(2737, 7)`, 동일 요청 목록, 후보 잠금과 정책 서명을 다시 대조했다. 새로운 holdout 접근 예약이나 승인 배포 묶음은 생성하지 않았다. 이 검증은 비용 재측정이 아니며 가장 빠른 반복만 고른 것도 아니다.

## 저장 구조와 상태

```text
artifacts/operational_message/recovery_v1/cost_v1/
  candidate_bundle/
    pipeline.joblib
    feature_schema.json
    model_input_schema.json
    label_map.json
    decision_policy.json
  cost_protocol_lock.json
  cost/selected_recovery_rf/
    validation_input.npz
    job.json
    process_0.json
    process_1.json
    process_2.json
  candidate_lock.json        비용 통과 때만 생성
  cost_manifest.json         정상 완료 때만 생성
```

`candidate_locked_pending_independent_test`는 비용 검증을 마치고 독립 Test를 기다리는 상태다. `withheld_before_test_due_to_cost`는 비용 기록은 완료했지만 Test 접근 전에 후보 확인을 보류한 상태다.

`candidate_bundle/`은 **미승인 평가 묶음**이다. 승인된 `deployment_bundle/`이 아니며 FastAPI가 자동으로 읽지 않는다. 원본 학습 `selection.json`의 `cost_verified=false`는 당시 학습 단계의 역사적 기록으로 보존한다. 현재 비용 상태는 새 보고서를 확인한다. 이후 평가기는 비용 완료 manifest와 잠금 해시도 검증해야 하며 잠금 파일 하나만으로 승인을 주장하면 안 된다.

## 실행 방법

AI 폴더에서 실행한다.

```powershell
python -m pip install --target .deps -r requirements-evaluation.txt
python -X utf8 .\evaluation\run_operational_recovery_cost.py
```

결과가 이미 있으면 기본 명령은 덮어쓰기를 거부한다. 비용 재측정이 필요하면 별도 출력 경로를 쓰고 이전 측정도 함께 보존·공개한다. 가장 좋은 반복만 골라 승인했다고 주장하지 않는다. 다음 Test 확인에는 이번에 생성한 원본 잠금 정책을 사용한다.

```powershell
python -X utf8 .\evaluation\run_operational_recovery_cost.py --artifact-dir .\artifacts\operational_message\recovery_v1\cost_replay --report-prefix .\reports\operational_recovery_cost_replay
```

## 검증 범위와 한계

이번 비용은 JSON 변환·HTTP 통신·FastAPI 요청 검증·TCP 송수신·큐 대기·동시성·과부하를 포함하지 않는다. FastAPI 프로세스 전체 메모리 측정도 아니다. 단일 장비의 오프라인 실행 결과이며 실시간 시스템의 지연시간을 보장하지 않는다.

기존 Test는 이전 RF 실패 진단에 이미 사용했다. 새 Test는 수집·품질·분할 과정에서 확인했지만 이번에는 모델로 예측하지 않았다. 예상 Test 해시는 dataset manifest에서 복사할 뿐 CSV를 다시 열어 해시를 계산하지 않는다.

현재 선정 모델은 Validation FP 39건과 FN 275건이다. 오탐 감소 대신 누락이 늘었고 잘못된 길이 본문 36건 중 33건을 놓쳤다. 비용 통과로 이 관측 한계가 사라지지 않는다.

## 다음 작업과 남은 단계

다음은 **잠근 RF·0.7의 새 Test 5,407건 독립 승인 확인**이다. 먼저 holdout 접근 예약을 남기고 고정 후보 한 개만 평가한다. 전체·정상 묶음 FPR과 Recall이 기준을 넘으면 승인 보류하며 다른 모델이나 threshold로 바꾸지 않는다.

남은 큰 작업 묶음은 4개다. 보정의 독립 승인 확인, 실제 모델 FastAPI 연결, TCP 종단 통합·장애·부하 검증, 재현·보고서·GitHub 포트폴리오 마무리다. 보정 승인이 완료되면 3개가 남는다. 이번처럼 한 묶음 안의 세부 커밋을 완료할 때마다 큰 묶음 숫자가 줄어드는 것은 아니다.
