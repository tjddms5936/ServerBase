# 운영 LSTM 직접 학습과 Validation 비교

## 범위

원본 Train 1538행으로만 scaler를 fit하고 같은 Train 메시지의 시계열 창으로 LSTM 두 후보를 직접 학습했다.
Validation 513건의 현재 메시지에서 기존 선정 RF와 비교했다. Test·원본 JSONL·종료 summary는 읽지 않았다.
Validation은 이미 LR/RF 선정에 사용했다. checkpoint·threshold 선택에 다시 사용한 개발 결과이며 독립 최종 성능이 아니다.

## 실제 학습 기록

| 후보 | 사용 epoch | 최적 epoch | 종료 | 최저 Validation 손실 | 파라미터 수 |
| --- | ---: | ---: | --- | ---: | ---: |
| lstm_h16 | 30/30 | 30 | max_epochs | 0.270547 | 1617 |
| lstm_h32 | 30/30 | 27 | max_epochs | 0.255707 | 5281 |

padding은 유효 길이로 제외하고 마지막 유효 hidden 출력을 사용했다. 창마다 상태를 초기화하며 최적 checkpoint 저장·재로딩 예측도 일치했다.

## 모든 threshold 조합

| 후보 | threshold | FP | FN | FPR | Recall | F1 | 제약 충족 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| lstm_h16 | 0.3 | 79 | 0 | 22.64% | 100.00% | 80.59% | 미충족 |
| lstm_h16 | 0.4 | 51 | 1 | 14.61% | 99.39% | 86.24% | 미충족 |
| lstm_h16 | 0.5 | 13 | 50 | 3.72% | 69.51% | 78.35% | 충족 |
| lstm_h16 | 0.6 | 12 | 69 | 3.44% | 57.93% | 70.11% | 충족 |
| lstm_h16 | 0.7 | 10 | 96 | 2.87% | 41.46% | 56.20% | 미충족 |
| lstm_h16 | 0.8 | 4 | 119 | 1.15% | 27.44% | 42.25% | 미충족 |
| lstm_h16 | 0.9 | 0 | 160 | 0.00% | 2.44% | 4.76% | 미충족 |
| lstm_h32 | 0.3 | 80 | 0 | 22.92% | 100.00% | 80.39% | 미충족 |
| lstm_h32 | 0.4 | 42 | 5 | 12.03% | 96.95% | 87.12% | 미충족 |
| lstm_h32 | 0.5 | 10 | 62 | 2.87% | 62.20% | 73.91% | 충족 |
| lstm_h32 | 0.6 | 7 | 70 | 2.01% | 57.32% | 70.94% | 충족 |
| lstm_h32 | 0.7 | 6 | 79 | 1.72% | 51.83% | 66.67% | 충족 |
| lstm_h32 | 0.8 | 4 | 96 | 1.15% | 41.46% | 57.63% | 미충족 |
| lstm_h32 | 0.9 | 0 | 126 | 0.00% | 23.17% | 37.62% | 미충족 |

## LSTM 후보 선정

FPR 10% 이하·Recall 50% 이상 중 Recall 최대, 동률은 F1 최대·FPR 최소·후보 id·threshold 오름차순이다.
선정 LSTM: `lstm_h16`·threshold `0.5`. 이는 LSTM 후보 안의 개발 선정이며 기존 RF를 자동 교체하지 않는다.

## 같은 Validation의 RF/LSTM 비교

| 비교 역할 | threshold | FP | FN | FPR | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| reference_rf | 0.3 | 30 | 9 | 8.60% | 94.51% | 88.83% |
| lstm_h16__default | 0.5 | 13 | 50 | 3.72% | 69.51% | 78.35% |
| lstm_h16__best_feasible | 0.5 | 13 | 50 | 3.72% | 69.51% | 78.35% |
| lstm_h32__default | 0.5 | 10 | 62 | 2.87% | 62.20% | 73.91% |
| lstm_h32__best_feasible | 0.5 | 10 | 62 | 2.87% | 62.20% | 73.91% |
| selected_lstm | 0.5 | 13 | 50 | 3.72% | 69.51% | 78.35% |

## 시나리오별 결과

| 비교 역할 | 시나리오 | 메시지 | FP | FN | Recall | FPR |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| reference_rf | controlled_burst_chat | 148 | 0 | 9 | 93.92% | 해당 없음 |
| reference_rf | malformed_chat_payload | 8 | 0 | 0 | 100.00% | 해당 없음 |
| reference_rf | malformed_length_chat | 8 | 0 | 0 | 100.00% | 해당 없음 |
| reference_rf | normal_batched_chat | 130 | 21 | 0 | 해당 없음 | 16.15% |
| reference_rf | normal_chat | 110 | 7 | 0 | 해당 없음 | 6.36% |
| reference_rf | normal_fragmented_chat | 109 | 2 | 0 | 해당 없음 | 1.83% |
| selected_lstm | controlled_burst_chat | 148 | 0 | 34 | 77.03% | 해당 없음 |
| selected_lstm | malformed_chat_payload | 8 | 0 | 8 | 0.00% | 해당 없음 |
| selected_lstm | malformed_length_chat | 8 | 0 | 8 | 0.00% | 해당 없음 |
| selected_lstm | normal_batched_chat | 130 | 13 | 0 | 해당 없음 | 10.00% |
| selected_lstm | normal_chat | 110 | 0 | 0 | 해당 없음 | 0.00% |
| selected_lstm | normal_fragmented_chat | 109 | 0 | 0 | 해당 없음 | 0.00% |

## 길이별 관측 coverage

| 비교 역할 | 구간 | 메시지 | FP | FN |
| --- | --- | ---: | ---: | ---: |
| reference_rf | length_one | 48 | 14 | 0 |
| reference_rf | partial_length_2_to_max_minus_1 | 191 | 6 | 8 |
| reference_rf | full_window | 274 | 10 | 1 |
| selected_lstm | length_one | 48 | 0 | 22 |
| selected_lstm | partial_length_2_to_max_minus_1 | 191 | 3 | 26 |
| selected_lstm | full_window | 274 | 10 | 2 |

모든 원본 메시지를 유지했다. 길이 1인 최초·malformed 메시지에 과거 정보를 만들어 넣지 않았다.
변경 row_id·확률·수정/신규 FP/FN, burst 첫 양성과 request_index별 누락을 JSON과 비교 CSV에 보관한다.

## 한계

- Validation은 이미 LR/RF 진단·선정에 사용했고 이번에도 epoch·후보·threshold를 선택했다. 최고 점수는 독립 최종 성능이 아니다.
- RF는 현재 메시지, LSTM은 최근 과거·현재 창을 사용한다. 같은 판단 대상·원시 입력 의미지만 사용 문맥과 모델이 함께 달라졌다.
- 겹친 창은 독립 표본이 아니다. 생성기 편향과 작은 캠페인 수, 단일 seed 결과의 한계가 남는다.
- 첫 메시지와 짧은 malformed 세션도 유지했다. 없는 과거나 본문 필드를 LSTM이 복원했다고 해석하지 않는다.
- scaler와 가중치 갱신은 Train만 사용했다. Validation에서는 gradient 없이 손실·확률을 관측하고 최적 epoch를 선택했다.
- LSTM 후보 내 선정은 기존 RF 교체나 최종 모델 확정이 아니다. 개선이 없으면 결과를 그대로 기록한다.
- Test·추론 지연시간·메모리·실제 FastAPI 모델 연결은 이번 작업에서 수행하지 않았다.

## 다음 작업

후보·threshold를 고정한 뒤 최종 Test와 동일 조건 추론 지연시간·메모리·산출물 비용을 비교해 최종 모델을 선택한다. FastAPI는 아직 dummy 상태다.
