# 고정 RF·0.7의 독립 Test 승인 확인

## 이번 단계의 목적

새 Train으로 직접 학습하고 Validation에서 선정한 원본 7개 입력 RF·threshold `0.7`은 이전 단계에서 실행 비용을 통과했다. 이번에는 모델·입력·판정 기준을 바꾸지 않고 별도 캠페인의 Test 5,407건으로 로컬 연동 승인 여부를 확인한다.

이 단계는 더 좋은 모델을 찾는 실험이 아니다. **이미 고른 모델이 미리 정한 조건을 만족하는지 확인하는 시험**이다. Test를 보고 모델이나 threshold를 다시 고르면 해당 Test를 독립 최종 성능이라고 주장할 수 없다.

## 승인 범위

통과는 로컬 advisory 용도의 FastAPI 연동을 허용한다는 의미다. 실제 외부 침입 탐지·서비스 운영·자동 차단·실시간 응답 보장은 아니다. FastAPI의 실제 모델 로딩과 C++ 종단 통합은 후속 단계다.

실패하면 승인 묶음을 만들지 않는다. 다른 후보를 Test에서 비교하거나 threshold를 바꿔 같은 Test를 다시 최종 시험으로 사용하지 않는다.

## 파일 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_recovery_test.json` | 비용 완료 manifest와 후보 잠금의 예상 SHA-256, 데이터셋·결과·공용 holdout 기록 경로를 지정한다. 모델은 rf_raw7·0.7로 제한한다. |
| `evaluation/run_operational_recovery_test.py` | 비용 완료 검증, 고정 모델 재확인, 접근 예약, Test 평가, 승인 판정, 조건부 배포 묶음, 보고서를 만든다. |
| `evaluation/operational_final_data.py` | 기존 구현을 수정 없이 사용한다. Test 접근 예약·변경 정책 재사용 거부·원본 Test 로딩·식별자 비중복을 검증한다. |
| `evaluation/operational_final_models.py` | 기존 고정 추론 경로를 재사용한다. 잠근 파일 해시를 검증하고 RF 한 개만 로딩한다. |
| `tests/test_operational_recovery_test.py` | Test 접근 순서, 원본 변조, 실패·중단, 정책 보존, 승인 경계, 저장·재실행을 검사한다. 실제 사용자 Test는 사용하지 않는다. |
| `reports/operational_recovery_test.json/.md` | 실제 전체·시나리오·캠페인·첫 메시지 결과, 비용 기준, 승인 여부, 해시와 한계를 기록한다. |
| `reports/operational_recovery_test.predictions.csv` | Test 전체의 원본 식별자·feature·정답·확률·판단·0.7 threshold·오분류 종류를 보관한다. |

기존 학습·비용 코드와 모델을 수정하지 않는다. C++ 서버·FastAPI dummy도 이번 단계에서는 변경하지 않는다.

## 실제 실행 순서

1. 기존 결과가 있거나 데이터셋·비용·holdout 기록·원본 수집 batch 내부에 출력하려 하면 거부한다.
2. Train/Validation 전용 로더로 고정 데이터셋의 계약·입력·정답·metadata 정렬과 해시를 확인한다. 이때 Test CSV는 열지 않는다.
3. 설정에 고정한 비용 완료 manifest·후보 잠금 SHA-256을 확인한다. 완료 기록 없이 잠금 파일만 있다고 Test를 평가하지 않는다.
4. 비용 산출물·보고서·과거 보호 파일을 검증한다. 모델·0.7 정책·입력 schema·선정 지표·사전 기준·정책 서명이 서로 일치해야 한다.
5. 이전 비용 기록의 반복 수·원본 시간 표본·요약을 대조하고 저장 Validation NPZ가 실제 Validation `(2737, 7)`과 같은지 확인한다. 비용을 새로 측정하지 않는다.
6. 고정 RF 한 개만 로딩하고 같은 Validation의 확률 기반 지표를 재현한다. 후보 비교·새 선정·fit은 하지 않는다.
7. 새 평가 설정·실행기·테스트 코드·보호 해시를 `evaluation_protocol_lock.json`에 기록한다.
8. 공용 `holdout_usage/`에 Test 접근을 예약한다. 다른 정책이 같은 Test를 이미 사용했다면 거부한다. 예약도 노출 이력으로 취급한다.
9. 그 뒤에만 실제 Test CSV 3개를 읽는다. X/y/metadata 해시·행 수·열 순서·정답·feature fingerprint·사전 run 배치·campaign/run/session/행 비중복을 검사한다.
10. Test 세션의 request_index가 1부터 연속인지, 첫 파싱 간격이 0인지, 누적 시간·바이트·오류 수가 감소하지 않는지 확인한다. 새로운 모델 feature는 만들지 않는다.
11. RF 확률을 계산하고 `확률 >= 0.7`로 판단한다. 모든 메시지와 원래 정답을 유지한다.
12. 전체 지표·시나리오·첫 메시지/후속 메시지·캠페인별 진단·burst의 첫 양성 위치를 기록한다.
13. 사전 성능 기준과 잠근 비용을 대조해 `test_verdict.json`에 승인 또는 보류를 기록한다.
14. 통과할 때만 별도 `deployment_bundle/`을 만든다. 원본 후보 묶음과 학습·비용 기록은 보존한다.
15. 예측 CSV를 다시 읽어 행 식별자·정답·확률·판단·threshold 일치를 확인하고 보고서·완료 manifest를 저장한다.

## 사전 기준

| 확인 항목 | 기준 | 근거 |
| --- | --- | --- |
| Test 전체 정상 FPR | 10% 이하 | 수집 전 보정 계획 |
| Test 정상 묶음 FPR | 10% 이하 | 수집 전 보정 계획 |
| Test 이상 Recall | 50% 이상 | 수집 전 보정 계획 |
| 전처리 포함 단일 요청 p95 | 25ms 이하 | 이미 잠근 Validation 비용 |
| 프로세스 전체 peak RSS | 512MiB 이하 | 이미 잠근 Validation 비용 |
| 필수 파일 합계 | 50MiB 이하 | 잠근 비용 및 실제 승인 파일 크기 |

캠페인별·개별 malformed·첫 메시지 성능은 별도로 공개하지만, Test를 본 뒤 새로운 통과 기준을 추가하거나 기존 기준을 완화하지 않는다. 기준 통과와 특정 시나리오의 한계가 공존할 수 있다.

## 지표의 의미

- FP: 정상 메시지를 이상으로 판단한 건수다.
- FN: 통제된 이상 메시지를 정상으로 판단한 건수다.
- FPR: 정상 메시지 중 FP의 비율이다. 전체와 정상 묶음 시나리오를 구분한다.
- Recall: 통제된 이상 메시지 중 실제로 이상이라고 판단한 비율이다.
- Precision: 이상이라고 판단한 메시지 중 정답도 이상인 비율이다.
- F1: Precision과 Recall의 조화평균이다. 이번 승인에서 Recall·FPR 대신 사후 주 기준으로 바꾸지 않는다.
- ROC-AUC: 여러 threshold에서의 점수 분리 능력을 요약한다. 높더라도 고정 0.7의 누락이 적다는 뜻은 아니다.

burst 첫 양성 위치는 세션의 어느 메시지에서 처음 이상으로 판단했는지 보여 준다. 첫 양성이 나왔다고 그 앞의 FN을 정답으로 바꾸지 않는다. 첫 관측부터 양성까지의 시간은 서버 snapshot의 파싱 시간 차이며 송신부터 응답까지의 종단 지연시간이 아니다.

## 데이터 범위와 coverage

평가 대상은 별도 Test 캠페인 2개·메시지 관측 run 108개·세션 432개·메시지 5,407건이다. 정상 3,964건과 통제된 이상 1,443건이다.

메시지 snapshot이 없는 헤더 거부 run은 dataset manifest의 제외 기록에서 따로 확인한다. TCP 코어가 프로토콜 헤더를 거부한 결과를 AI가 탐지한 것처럼 모델 Recall 분모에 넣지 않는다. 종료 summary·시나리오 정답·campaign/run/session 식별자를 모델 입력으로 사용하지 않는다.

같은 세션의 여러 메시지는 서로 관련돼 있다. 메시지 5,407건이 완전히 독립적인 5,407개 시행이라는 뜻은 아니다. 실제 독립 캠페인 수가 2개이고 생성기·장비가 같다는 한계가 남는다.

## 실제 평가 결과

고정 RF·0.7이 사전 기준을 통과해 `approved_for_local_advisory_integration`으로 완료됐다. Test를 열기 전 비용 완료·잠금·설정·평가 코드를 확인했고 접근 예약은 이번이 최초였다. 실제 실행에서 RF를 한 번 로딩해 Validation 2,737건 재현과 Test 5,407건 예측만 수행했다. 다른 모델·threshold는 비교하지 않았다.

| 항목 | 실제 결과 | 사전 기준 |
| --- | ---: | ---: |
| 전체 정상 FPR | 68/3,964 = 1.72% | 10% 이하 |
| 정상 묶음 FPR | 67/1,293 = 5.18% | 10% 이하 |
| 이상 Recall | 912/1,443 = 63.20% | 50% 이상 |
| Precision | 93.06% | 참고 지표 |
| F1 | 75.28% | 참고 지표 |
| Accuracy | 88.92% | 참고 지표 |
| ROC-AUC | 0.966644 | 참고 지표 |

혼동 행렬은 TN 3,896·FP 68·FN 531·TP 912다. 기존 Validation의 FPR 1.98%·정상 묶음 FPR 5.16%·Recall 63.96%와 비슷한 경향이지만 두 split의 메시지는 같은 표본이 아니므로 건수 차이를 같은 행의 개선 효과로 해석하지 않는다. 이전 v2 Test의 RF와도 데이터·정책이 달라 직접 인과 비교하지 않는다.

### 남아 있는 탐지 한계

| 시나리오 | 메시지 | FP | FN | 관련 비율 |
| --- | ---: | ---: | ---: | --- |
| 통제된 무대기 연속 전송 | 1,299 | 0 | 460 | Recall 64.59% |
| 잘못된 payload | 72 | 0 | 0 | Recall 100.00% |
| 잘못된 본문 길이 | 72 | 0 | 71 | Recall 1.39% |
| 정상 묶음 전송 | 1,293 | 67 | 0 | FPR 5.18% |
| 정상 일반 전송 | 1,321 | 1 | 0 | FPR 0.08% |
| 정상 조각 전송 | 1,350 | 0 | 0 | FPR 0.00% |

특히 **잘못된 길이 메시지 72건 중 71건을 놓쳤다.** 전체 Recall 통과를 모든 이상 유형의 탐지 성공이라고 설명하면 안 된다. raw7에는 본문 내부 길이 값·검증 결과가 없으며 AI 행동 탐지는 기존 프로토콜 검증을 대체하지 않는다.

첫 메시지의 이상 Recall도 80/216 = 37.04%로 후속 메시지 832/1,227 = 67.81%보다 낮다. 첫 메시지 432건과 원래 정답을 제외하거나 바꾸지 않았다. 두 Test 캠페인의 Recall은 각각 64.99%·61.33%, 전체 FPR은 1.27%·2.15%다. 이 진단을 이유로 Test용 기준을 사후 변경하지 않았다.

헤더 거부 run 18개는 메시지 평가에서 제외했다. 전체 이상 1,443건 중 FN 531건이 남으므로 자동 차단이나 실제 보안 운영에 충분한 모델이라고 주장하지 않는다.

### 완료 검증

새 테스트 18개를 포함해 전체 337개 테스트가 통과했다. 완료 후 산출물 8개·보고서 3개·보호 파일 107개의 해시를 재확인했다. Test 원본·metadata·정답·전체 지표·CSV·승인 묶음 모델의 확률과 판단도 같은 고정 정책으로 재현됐다. 이 확인은 저장 결과의 재현 검사이며 새 독립 평가로 추가 집계하지 않는다.

승인 묶음의 필수 파일 5개는 777,289바이트, 승인 manifest를 포함한 전체 디렉터리는 779,857바이트다. 원본 후보 정책은 그대로 두고 복사 묶음의 정책 설명만 Test 확인 상태로 명시했으므로 이전 미승인 묶음의 777,263바이트와 크기가 조금 다르다. 모델·schema·label map의 해시는 원본과 동일하다.

## 산출물과 상태

```text
artifacts/operational_message/recovery_v1/test_v1/
  evaluation_protocol_lock.json
  test_verdict.json
  deployment_bundle/                 성능 통과 때만 생성
    pipeline.joblib
    feature_schema.json
    model_input_schema.json
    label_map.json
    decision_policy.json
    deployment_manifest.json
  evaluation_manifest.json           정상 완료 때만 생성
```

- `approved_for_local_advisory_integration`: 고정 정책이 사전 성능·비용 기준을 통과했다. 로컬 실제 추론 연동을 준비할 수 있다.
- `withheld_after_test`: 평가는 완료했지만 성능 기준을 통과하지 못했다. 승인 묶음은 없다.
- `evaluation_failure.json`: 파일 변조·쓰기 실패·중단 등으로 실행을 완료하지 못했다. 성능 실패와 실행 실패를 구분한다.

승인 묶음은 복사한 모델·schema·label map의 해시를 원본과 대조한다. 정책에는 같은 threshold 0.7과 로컬 한정·자동 차단 금지를 명시한다. 승인 manifest는 판정 파일·후보 잠금·정책 서명·라이브러리 버전·실제 필수 파일 크기를 연결한다.

**완료 평가 manifest가 없는 묶음은 사용하지 않는다.** 파일 저장이 뒤에서 실패하면 승인 파일 일부가 남을 수 있으므로 `requires_complete_evaluation_manifest=true`와 연결된 `evaluation_manifest.json`의 존재·완료 상태·묶음 해시를 후속 FastAPI 로더에서 함께 검증해야 한다.

## Test 사용 이력

기존 v2 Test와 새 recovery Test의 해시를 구분하지만 같은 공용 holdout 기록 폴더를 사용한다. 기록을 삭제해 노출된 Test를 새 Test처럼 취급하지 않는다.

첫 접근은 `first_locked_policy_confirmation`, 같은 고정 정책의 재실행은 `same_policy_recheck_of_exposed_test`로 기록한다. 모델·threshold·기준을 바꾼 재사용은 거부한다. 예약 후 오류·중단이 발생해도 사용 기록을 지우지 않는다.

예측·지표·해시의 재현 검사는 같은 정책의 결과 확인이다. 이를 새로운 독립 실험이나 추가 일반화 증거로 세지 않는다.

## 실행 방법

AI 폴더에서 실행한다.

```powershell
python -X utf8 .\evaluation\run_operational_recovery_test.py
```

이미 결과가 있으면 덮어쓰기를 거부한다. 같은 정책의 재확인이 필요하면 원본 후보 잠금과 공용 holdout 기록을 유지하고 별도 출력 경로를 사용한다.

```powershell
python -X utf8 .\evaluation\run_operational_recovery_test.py --artifact-dir .\artifacts\operational_message\recovery_v1\test_replay --report-prefix .\reports\operational_recovery_test_replay
```

## 다음 작업과 남은 단계

승인 통과 시 다음은 승인 묶음의 해시·입력 계약·정답·threshold를 검증해 FastAPI에 실제 모델 추론을 연결하는 작업이다. 그때도 C++ 코어의 비동기 HTTP 구조는 유지한다.

승인 후 남은 큰 단계는 3개다.

1. 승인 모델의 FastAPI 실제 추론 연결.
2. C++ TCP 종단 통합과 장애·지연·큐 포화·종료·부하 검증.
3. 재현 절차·보고서·GitHub README·면접 설명 자료 마무리.

승인 보류라면 추가 보정·독립 확인 묶음이 끝난 것이 아니므로 4개가 남는다. 기준에 맞지 않은 결과를 완료 숫자를 줄이려고 승인하지 않는다.
