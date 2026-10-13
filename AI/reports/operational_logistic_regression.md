# 운영 Logistic Regression 초기 기준 모델

## 이번에 수행한 작업

고정 운영 데이터셋의 train만으로 StandardScaler와 Logistic Regression을 학습했다. validation에는 transform과 예측만 수행했다.
정답 0은 정상, 1은 통제된 이상이다. test CSV는 읽지 않았으며 FastAPI는 여전히 dummy 추론을 사용한다.

## 학습 조건

- 데이터셋: `operational_message_v1` / 의미 계약: `server_application_message_v2`
- train 126건, validation 138건, 입력 7개
- 최대 반복 1000회, 실제 반복 12회, 수렴 확인: True
- 설정: C=1.0, solver=lbfgs, tol=0.0001
- threshold=0.5는 사전에 고정한 baseline 기준이며 validation 최적화로 선택한 값이 아니다.
- 학습 손실은 확률의 오차와 L2 규제를 고려한다. 정답률을 100%로 만드는 반복 작업이 아니다.

## validation 결과

| 지표 | 값 |
| --- | ---: |
| accuracy | 0.760870 |
| precision | 0.706667 |
| recall | 0.828125 |
| f1 | 0.762590 |
| roc_auc | 0.885557 |
| average_precision | 0.881574 |
| log_loss | 0.351842 |
| false_positive_rate | 0.297297 |

TN=52, FP=22, FN=11, TP=53. FP는 정상 오탐, FN은 통제된 이상 누락이다.

## 시나리오별 validation 결과

| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_burst_chat | 56 | 0 | 8 | 0.857143 | 해당 없음 |
| malformed_chat_payload | 4 | 0 | 3 | 0.250000 | 해당 없음 |
| malformed_length_chat | 4 | 0 | 0 | 1.000000 | 해당 없음 |
| normal_batched_chat | 23 | 20 | 0 | 해당 없음 | 0.869565 |
| normal_chat | 27 | 1 | 0 | 해당 없음 | 0.037037 |
| normal_fragmented_chat | 24 | 1 | 0 | 해당 없음 | 0.041667 |

## 해석과 한계

- 통제된 시나리오 정답을 학습한 초기 결과이며 실제 침입 탐지 성능이 아니다.
- 분할별 run·세션·메시지 수는 dataset_splits에 기록한다. 같은 세션의 메시지는 독립 표본이 아니다.
- 단일 캠페인과 동일 생성기의 특성에 적응했을 수 있다. 별도 캠페인 일반화는 검증하지 않았다.
- error_count_total은 train에서 상수 0이다. 현재 본문의 검증 실패는 snapshot 이후라 이 입력에 반영되지 않는다.
- packet_size와 payload_size는 8바이트 차이인 중복 정보다. 가중치를 원인 또는 독립적인 feature 중요도로 해석하지 않는다.
- train/validation 손실은 규제항을 제외한 평균 log loss다. solver의 수렴 기준이나 전체 최적화 목적값과 동일하지 않다.
- test 최종 평가·설정/threshold 선정·지연시간/메모리 비교·FastAPI 모델 연결은 아직 하지 않았다.

## 산출물

모델 묶음: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\artifacts\operational_message\logistic_regression_v1`

JSON에 scaler의 평균·분산, 표준화 입력에 대한 가중치 7개·기본값, 실제 반복 수, 입력·코드 해시와 실행 환경을 남겼다.
validation_predictions.csv는 추적 metadata, 정답, 확률, 예측 및 오분류 종류를 보관한다. metadata는 학습 입력이 아니다.

## 다음 작업

동일한 train/validation으로 운영 RandomForest를 학습해 LR과 비교한다. 이후 제한된 설정·threshold를 validation에서 선정하며 test는 최종 비교까지 보류한다.
