# 고정 RF·0.7의 독립 Test 승인 확인

## 평가 정책과 접근 기록

Validation·비용으로 잠근 원본 7개 RF·threshold 0.7 한 개만 평가했다. 재학습·후보 비교·threshold 탐색·기준 완화는 하지 않았다.
Test 5,407건. 이전 접근 예약 여부: `False`.
이번 확인의 성격: `first_locked_policy_confirmation`. 같은 정책 재확인은 새 독립 평가라고 주장하지 않는다.
비용 완료 manifest·후보 잠금·정책 서명·보호 해시를 먼저 확인하고 평가 protocol·holdout 접근 예약을 남긴 뒤 Test CSV를 열었다.

## 전체 Test 결과

| 지표 | 결과 |
| --- | ---: |
| accuracy | 0.889218 |
| precision | 0.930612 |
| recall | 0.632017 |
| f1 | 0.752786 |
| roc_auc | 0.966644 |
| average_precision | 0.913124 |
| false_positive_rate | 0.017154 |
| false_negative_rate | 0.367983 |

TN 3,896 · FP 68 · FN 531 · TP 912.

## 시나리오별 결과

| 시나리오 | 메시지 | FP | FN | 정상 FPR | 이상 Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_burst_chat | 1299 | 0 | 460 | 해당 없음 | 64.59% |
| malformed_chat_payload | 72 | 0 | 0 | 해당 없음 | 100.00% |
| malformed_length_chat | 72 | 0 | 71 | 해당 없음 | 1.39% |
| normal_batched_chat | 1293 | 67 | 0 | 5.18% | 해당 없음 |
| normal_chat | 1321 | 1 | 0 | 0.08% | 해당 없음 |
| normal_fragmented_chat | 1350 | 0 | 0 | 0.00% | 해당 없음 |

## 승인 판단

상태: `approved_for_local_advisory_integration`.
미충족 사유: 없음.
전체 FPR 10% 이하·정상 묶음 FPR 10% 이하·Recall 50% 이상과 잠근 Validation 비용 기준을 그대로 적용했다.
기존 비용은 p95 4.7538ms·프로세스 peak RSS 154.90MiB이며 이번에 재측정하거나 Test 비용으로 재선정하지 않았다.

## 캠페인별 진단

| 캠페인 | 메시지 | FP | FN | 정상 FPR | 이상 Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| rec_261014_3878ea96_c4 | 2703 | 25 | 258 | 1.27% | 64.99% |
| rec_261014_3878ea96_c5 | 2704 | 43 | 273 | 2.15% | 61.33% |

캠페인·시나리오·첫 메시지 지표는 누락·편향 진단이다. 사전에 없던 추가 승인 기준으로 정책을 바꾸지 않는다.
메시지 평가 coverage: 2개 캠페인·108개 run·432개 세션.
메시지 snapshot이 없는 헤더 거부 run 18개는 모델 성능 분모에 넣지 않았다.

## 한계와 상태

- 같은 생성기·장비의 별도 캠페인 2개를 평가했다. 메시지 5,407건을 독립 시행 5,407개로 해석하지 않는다.
- 통제된 애플리케이션 메시지 이상 실험이며 공개 UNSW 성능이나 실제 외부 침입 탐지 성능과 합치지 않는다.
- 첫 메시지와 malformed의 부족한 과거·본문 정보를 raw7이 복원하지 못한다. 시나리오별 누락을 전체 평균과 함께 확인한다.
- Test 결과로 후보·threshold·성능 기준을 바꾸지 않았다. 같은 Test는 이제 노출된 자료이며 바뀐 정책에는 새 독립 평가가 필요하다.
- 비용은 이전 Validation의 오프라인 측정이며 JSON·HTTP·큐·FastAPI/TCP 종단 부하는 포함하지 않는다.
- 승인은 로컬 advisory 연동만 허용한다. FastAPI 교체·실제 운영·자동 차단은 이번 단계에서 수행하지 않는다.

모델·원본 schema·모델 입력 schema·label map·0.7 정책과 승인 manifest를 별도 deployment_bundle에 저장했다.
원본 후보 묶음·학습 기록은 수정하지 않았다. 패키지는 로컬 advisory 연동용이며 FastAPI는 아직 dummy다.

## 다음 작업

승인 묶음의 해시·입력 계약·threshold를 검증해 FastAPI 실제 추론에 연결한다. 남은 큰 작업 묶음은 3개다.
