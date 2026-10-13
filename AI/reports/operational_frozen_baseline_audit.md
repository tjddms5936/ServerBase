# 고정 LR의 새 캠페인 Validation 진단

## 평가 조건

기존 LR C=1·threshold=0.7·기존 scaler를 그대로 사용해 새 Validation 513건만 예측했다.
기존 scaler는 v1 Train 126건에서 학습한 값이다. 새 Train이나 Validation으로 다시 fit하지 않았다.
Train CSV는 공통 로더의 형식·행·해시 검사에만 읽었고 모델 예측에는 넣지 않았다. 기존 v1 CSV, Test CSV, 원본 JSONL·종료 요약은 읽지 않았다.
후보 파일의 초기 decision_policy는 0.5지만, 이번 판단은 수집 전에 고정한 selection.json의 0.7을 사용했다.

## 기존 선정 Validation과 새 Validation

| 지표 | 기존 v1 | 새 캠페인 |
| --- | ---: | ---: |
| rows | 138.000000 | 513.000000 |
| accuracy | 0.804348 | 0.730994 |
| precision | 0.930233 | 0.561905 |
| recall | 0.625000 | 0.719512 |
| f1 | 0.747664 | 0.631016 |
| roc_auc | 0.885557 | 0.834160 |
| average_precision | 0.881574 | 0.650641 |
| false_positive_rate | 0.040541 | 0.263610 |
| log_loss | 0.351842 | 0.940756 |

새 결과: TN=257, FP=92, FN=46, TP=118.
FP는 정상 오탐, FN은 통제된 이상 누락이다. 기존 수치는 저장된 선정 보고서 참조이며 이번에 기존 Validation을 재예측하지 않았다.
서로 다른 표본 수·시나리오 비율·설정의 데이터다. 건수 증감이나 점수 차이를 보강 모델의 개선 효과로 해석하지 않는다.

## 시나리오별 결과

| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_burst_chat | 148 | 0 | 30 | 0.797297 | 해당 없음 |
| malformed_chat_payload | 8 | 0 | 8 | 0.000000 | 해당 없음 |
| malformed_length_chat | 8 | 0 | 8 | 0.000000 | 해당 없음 |
| normal_batched_chat | 130 | 51 | 0 | 해당 없음 | 0.392308 |
| normal_chat | 110 | 41 | 0 | 해당 없음 | 0.372727 |
| normal_fragmented_chat | 109 | 0 | 0 | 해당 없음 | 0.000000 |

## run별 오분류

| run | 메시지 | FP | FN |
| --- | ---: | ---: | ---: |
| aug_261013_e1cafdde_selection_01_r00_normal_batched_chat | 63 | 0 | 0 |
| aug_261013_e1cafdde_selection_01_r00_malformed_length_chat | 4 | 0 | 4 |
| aug_261013_e1cafdde_selection_01_r00_normal_fragmented_chat | 52 | 0 | 0 |
| aug_261013_e1cafdde_selection_01_r00_malformed_chat_payload | 4 | 0 | 4 |
| aug_261013_e1cafdde_selection_01_r00_normal_chat | 52 | 36 | 0 |
| aug_261013_e1cafdde_selection_01_r00_controlled_burst_chat | 70 | 0 | 14 |
| aug_261013_e1cafdde_selection_01_r01_normal_fragmented_chat | 57 | 0 | 0 |
| aug_261013_e1cafdde_selection_01_r01_controlled_burst_chat | 78 | 0 | 16 |
| aug_261013_e1cafdde_selection_01_r01_normal_batched_chat | 67 | 51 | 0 |
| aug_261013_e1cafdde_selection_01_r01_malformed_length_chat | 4 | 0 | 4 |
| aug_261013_e1cafdde_selection_01_r01_malformed_chat_payload | 4 | 0 | 4 |
| aug_261013_e1cafdde_selection_01_r01_normal_chat | 58 | 5 | 0 |

## 세션 진단

| 시나리오 | 세션 | 한 번 이상 양성 | 양성 없음 | 오분류 포함 |
| --- | ---: | ---: | ---: | ---: |
| normal_batched_chat | 8 | 4 | 4 | 4 |
| malformed_length_chat | 8 | 0 | 8 | 8 |
| normal_fragmented_chat | 8 | 0 | 8 | 0 |
| malformed_chat_payload | 8 | 0 | 8 | 8 |
| normal_chat | 8 | 5 | 3 | 5 |
| controlled_burst_chat | 8 | 8 | 0 | 7 |

정상 세션의 양성은 오탐이다. 이상 세션에서 한 번 잡았어도 이전 메시지 누락이 사라지지 않는다.

## burst prefix

| 메시지 번호 | 관측 수 | TP | FN | Recall |
| --- | ---: | ---: | ---: | ---: |
| 1 | 8 | 3 | 5 | 0.375000 |
| 2 | 8 | 2 | 6 | 0.250000 |
| 3 | 8 | 3 | 5 | 0.375000 |
| 4 | 8 | 2 | 6 | 0.250000 |
| 5 | 8 | 6 | 2 | 0.750000 |
| 6 | 8 | 6 | 2 | 0.750000 |
| 7 | 8 | 6 | 2 | 0.750000 |
| 8 | 8 | 6 | 2 | 0.750000 |
| 9 | 8 | 8 | 0 | 1.000000 |
| 10 | 8 | 8 | 0 | 1.000000 |
| 11 | 8 | 8 | 0 | 1.000000 |
| 12 | 8 | 8 | 0 | 1.000000 |
| 13 | 8 | 8 | 0 | 1.000000 |
| 14 | 6 | 6 | 0 | 1.000000 |
| 15 | 6 | 6 | 0 | 1.000000 |
| 16 | 6 | 6 | 0 | 1.000000 |
| 17 | 6 | 6 | 0 | 1.000000 |
| 18 | 4 | 4 | 0 | 1.000000 |
| 19 | 4 | 4 | 0 | 1.000000 |
| 20 | 4 | 4 | 0 | 1.000000 |
| 21 | 3 | 3 | 0 | 1.000000 |
| 22 | 2 | 2 | 0 | 1.000000 |
| 23 | 2 | 2 | 0 | 1.000000 |
| 24 | 1 | 1 | 0 | 1.000000 |

burst 세션별 첫 양성 메시지·시각은 JSON에 저장했다. 기존 입력만 사용하는 단일 메시지 모델이며 실제 시계열 창을 학습한 결과는 아니다.

## 관측 coverage

Validation 캠페인 1개, 계획 run 14개·세션 56개 중 메시지 관측 run 12개·세션 48개를 평가했다.
헤더 거부 run 2개·세션 8개는 메시지 관측이 없어 메시지 성능에서 제외한다. 이를 AI가 탐지했다고 세지 않는다.

## 해석

- 새 Validation의 정상 FPR은 26.36%, 통제된 이상 Recall은 71.95%다. 모델·threshold는 그대로다.
- controlled_burst_chat: 메시지 148건, FP 0건, FN 30건.
- malformed_chat_payload: 메시지 8건, FP 0건, FN 8건.
- malformed_length_chat: 메시지 8건, FP 0건, FN 8건.
- normal_batched_chat: 메시지 130건, FP 51건, FN 0건.
- normal_chat: 메시지 110건, FP 41건, FN 0건.
- normal_fragmented_chat: 메시지 109건, FP 0건, FN 0건.

## 한계

- 새 캠페인은 기존 선정 데이터와 다른 실행·seed지만 같은 생성기·Windows 장비·C++ 프로토콜이다. 외부 실제 침입으로의 일반화는 검증하지 않았다.
- 새 Validation은 캠페인 1개다. 같은 세션의 메시지는 독립 표본이 아니며 513건을 513개 독립 실험으로 해석하지 않는다.
- 이 결과는 현재 모델의 조건 변화 진단이지 새 데이터로 학습한 모델의 개선 성과가 아니다. 점수 차이만으로 원인을 확정하지 않는다.
- 메시지 정답은 사전 시나리오 의도다. burst 초기 메시지를 사후 정상으로 바꾸지 않았고, 프로토콜 오류 검출과 AI 판단은 구분한다.
- 본문 길이 필드는 현재 입력에 없다. LSTM이나 보강 학습이 없는 본문 관측 정보를 자동 복원한다고 가정하지 않는다.
- 헤더 거부는 메시지 관측 불가로 별도 coverage를 보고한다. 관측 가능한 메시지 성능을 전체 연결의 탐지 성능으로 확대하지 않는다.
- threshold 재선정·보강 학습·시계열 모델·최종 Test·지연시간/메모리 비교·FastAPI 실모델 연결은 별도 작업이다.

## 추적·재현

전체 예측 CSV에는 metadata·입력 7개·정답·확률·예측·오류 종류를 저장한다. errors CSV는 그중 오분류만 보관한다. 입력·모델·사전 역할·코드 SHA-256과 기존 가중치/scaler 보존 검증을 JSON에 남겼다.

## 다음 작업

새 Train 전용 보강 LR/RF 학습의 제한된 후보·threshold·선택 규칙을 먼저 고정한다. 새 Validation으로 비교하고 Test는 최종 평가까지 보류한다.
