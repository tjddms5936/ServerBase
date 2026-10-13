# 운영 RandomForest 초기 기준 모델

## 학습 범위

검증된 동일 운영 feature 7개로 train만 학습했다. RF는 크기 기준으로 분기하는 트리 모델이므로 scaler를 사용하지 않았다.
validation은 예측에만 사용했고 test CSV는 해시 검사에도 열지 않았다. FastAPI는 여전히 dummy 상태다.

- train 126건, validation 138건
- 트리 100개, max_depth=None, min_samples_leaf=1
- max_features=sqrt, bootstrap=True, seed=42, n_jobs=1
- threshold=0.5는 사전에 고정한 초기 기준이며 validation에서 선정하지 않았다.
- 트리 수는 LR의 최적화 반복 횟수와 다른 개념이다. RF에는 LR 같은 수렴 판정을 적용하지 않는다.

## validation 결과

| 지표 | 값 |
| --- | ---: |
| accuracy | 0.688406 |
| precision | 0.714286 |
| recall | 0.546875 |
| f1 | 0.619469 |
| roc_auc | 0.802576 |
| average_precision | 0.728034 |
| log_loss | 1.092817 |
| false_positive_rate | 0.189189 |

TN=60, FP=14, FN=29, TP=35.

## 시나리오별 결과

| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_burst_chat | 56 | 0 | 28 | 0.500000 | 해당 없음 |
| malformed_chat_payload | 4 | 0 | 0 | 1.000000 | 해당 없음 |
| malformed_length_chat | 4 | 0 | 1 | 0.750000 | 해당 없음 |
| normal_batched_chat | 23 | 13 | 0 | 해당 없음 | 0.565217 |
| normal_chat | 27 | 1 | 0 | 해당 없음 | 0.037037 |
| normal_fragmented_chat | 24 | 0 | 0 | 해당 없음 | 0.000000 |

## 불순도 감소 기반 중요도

| 입력 | 중요도 |
| --- | ---: |
| connection_duration_ms | 0.485797 |
| message_interval_ms | 0.205870 |
| bytes_received_total | 0.141949 |
| bytes_sent_total | 0.091661 |
| packet_size | 0.039858 |
| payload_size | 0.034865 |
| error_count_total | 0.000000 |

## 해석과 한계

- 통제된 시나리오 정답의 초기 검증 결과이며 실제 침입 탐지 성능이 아니다.
- train/validation의 독립 run 수가 작고 동일 생성기·단일 캠페인 편향이 남는다. 메시지마다 독립 표본인 것은 아니다.
- 불순도 감소 기반 중요도는 학습 분기의 사용 정도다. 값의 종류가 많은 입력에 편향될 수 있고 인과관계나 원인으로 해석하지 않는다.
- packet_size와 payload_size는 중복 정보라 중요도를 나누어 가질 수 있다. error_count_total은 train에서 상수 0이다.
- 현재 snapshot 이후 발생한 본문 오류는 입력에 포함되지 않는다. RF 도입만으로 관측 불가능한 정보를 복원하지 못한다.
- 트리 수·설정·threshold 탐색, 최종 test 평가, 지연시간·메모리 비교와 FastAPI 모델 연결은 아직 하지 않았다.

## 산출물

모델 묶음: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\artifacts\operational_message\random_forest_v1`

Pipeline에 RF만 보관하며 전처리는 identity로 명시한다. schema·label map·고정 threshold·메시지별 validation 예측·완료 manifest를 함께 저장한다.

## 다음 작업

저장한 LR/RF를 동일 validation에서 비교하고, 그 다음 별도 단계에서 제한된 설정·threshold 후보를 선정한다. test는 최종 비교까지 보류한다.
