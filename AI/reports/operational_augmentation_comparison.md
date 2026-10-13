# 보강 LR/RF의 동일 Validation 비교

## 학습 전에 고정한 규칙

Train 1538건, Validation 513건, 후보 6개, threshold 조합 42개.
LR C=0.1/1/10, RF 최소 leaf 표본 수=1/5/10, threshold=0.3~0.9를 고정했다. LR scaler와 모든 모델은 새 Train으로만 학습했다.
정상 FPR <= 10%, 이상 Recall >= 50% 중 Recall 최대; 동률은 F1 최대·FPR 최소·후보 id·threshold 오름차순이다.
Validation은 이미 고정 기존 모델의 진단에 사용했다. 이번 학습 전에 protocol을 고정한 것이지 Validation을 보기 전 사전 등록 실험은 아니다.
기존 모델은 그대로 유지하고 Train에 Validation을 합치지 않았다. Test CSV는 해시 검사에도 읽지 않았다.

## 같은 Validation의 모델별 결과

| 비교 역할 | 후보 | threshold | FP | FN | FPR | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| frozen_lr_0_7 | lr_c_1 | 0.7 | 92 | 46 | 26.36% | 71.95% | 63.10% |
| augmented_lr_c_1_0_7 | lr_c_1 | 0.7 | 0 | 134 | 0.00% | 18.29% | 30.93% |
| augmented_rf_leaf_1_0_7 | rf_leaf_1 | 0.7 | 5 | 59 | 1.43% | 64.02% | 76.64% |
| best_lr_validation | lr_c_0_1 | 0.5 | 31 | 57 | 8.88% | 65.24% | 70.86% |
| best_rf_validation | rf_leaf_5 | 0.3 | 30 | 9 | 8.60% | 94.51% | 88.83% |

고정 0.7 비교는 같은 LR 설정의 재학습 변화와 RF를 구분한다. Validation 최적 조합은 설정·threshold 선택까지 포함하므로 데이터 보강만의 효과로 해석하지 않는다. 같은 threshold가 모델 확률의 같은 보정 수준을 뜻하지는 않는다.

## 선정 결과

선정 후보 `rf_leaf_5`, threshold `0.3`.
Validation FPR 8.60%, Recall 94.51%, F1 88.83%.
선정은 이번 제한된 후보 집합의 운영 baseline이다. 최종 Test·LSTM·비용 비교 전이므로 배포 모델 확정은 아니다.

## 선정 조합의 시나리오별 결과

| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_burst_chat | 148 | 0 | 9 | 93.92% | 해당 없음 |
| malformed_chat_payload | 8 | 0 | 0 | 100.00% | 해당 없음 |
| malformed_length_chat | 8 | 0 | 0 | 100.00% | 해당 없음 |
| normal_batched_chat | 130 | 21 | 0 | 해당 없음 | 16.15% |
| normal_chat | 110 | 7 | 0 | 해당 없음 | 6.36% |
| normal_fragmented_chat | 109 | 2 | 0 | 해당 없음 | 1.83% |

## 기존 모델의 오분류 변화

| 비교 역할 | 수정 FP | 수정 FN | 신규 FP | 신규 FN |
| --- | ---: | ---: | ---: | ---: |
| augmented_lr_c_1_0_7 | 92 | 0 | 0 | 88 |
| augmented_rf_leaf_1_0_7 | 92 | 11 | 5 | 24 |
| best_lr_validation | 61 | 0 | 0 | 11 |
| best_rf_validation | 81 | 38 | 19 | 1 |

변경된 모든 row_id·run/session·정답·확률·판단과 burst prefix를 JSON에 보관한다. 같은 Validation 행의 수정과 새로운 오분류를 분리해 보고한다.

## 한계

- 이미 진단한 Validation으로 후보·threshold를 선정한 개발 실험이다. 독립적인 최종 성능 추정치가 아니다.
- 고정 LR C=1·0.7 재학습 비교와 후보/threshold 선정 비교를 구분한다. 데이터 수·조건·학습 분포도 함께 바뀌므로 하나의 원인을 확정하지 않는다.
- Train 3개·Validation 1개 캠페인은 같은 생성기·장비·프로토콜이다. 실제 외부 침입·다른 장비에 대한 일반화를 보장하지 않는다.
- 같은 세션의 메시지는 독립 표본이 아니다. 시나리오별 FP/FN과 burst prefix를 전체 점수와 함께 보고한다.
- 본문 필드가 입력에 없는 관측 한계는 남는다. malformed를 잡거나 놓친 결과만으로 본문 유효성 검증을 배웠다고 주장하지 않는다.
- 헤더 거부는 메시지 관측 범위 밖이다. 기존 프로토콜 거부 경로를 AI 탐지 건수로 합치지 않는다.
- 성능이 좋아도 학습 데이터 생성기의 크기·타이밍 규칙에 적응한 결과일 수 있다. Test·LSTM·지연시간/메모리·실제 모델 배포는 아직 하지 않았다.

## 저장 및 재현

보강 산출물: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\artifacts\operational_message\augmentation_v1`
전체 42개 조합과 학습 기록은 선정 보고서에, 동일 행 비교는 비교 CSV와 JSON에 보관한다. 두 단계의 사전 lock과 전체 완료 manifest를 남겼다.

## 다음 작업

분리된 각 split의 동일 run/session 내부에서 시계열 창을 구성하고 LSTM 개선 실험을 설계한다. Test는 최종 비교까지 모델에 넣지 않는다.
