# 운영 LSTM 개선 실험 설계

## 설계 기록과 후속 구현

`configs/operational_lstm_plan.json`은 시계열 입력 준비 당시의 사전 설계 기록이다. JSON 자체는 학습 실행 명령이 아니며 입력 준비 단계에서는 PyTorch 설치·scaler fit·모델 학습·예측을 수행하지 않았다. 해당 시점의 상태 필드는 원래 기록으로 유지한다.

후속 작업에서 이 설계를 검증하는 학습 코드·CPU 의존성·학습 전 lock·checkpoint·학습 기록을 구현하고 실제 실험을 완료했다. 실행은 `configs/operational_lstm_experiment.json`과 `training/run_operational_lstm_experiment.py`, 결과·한계는 `docs/operational_lstm_experiment.md`를 참조한다. 기존 설계 조건은 결과를 본 뒤 변경하지 않았다.

## 가설과 비교 대상

현재 선정 RF는 현재 메시지의 수치 7개로 판단한다. 같은 새 Validation에서 FP 30건·FN 9건, FPR 8.60%·Recall 94.51%·F1 88.83%였다. 정상 묶음 전송의 오탐은 21/130건으로 FPR 16.15%이며 burst의 일부 메시지도 놓쳤다.

가설은 **최근 메시지의 크기·간격·누적값 변화 문맥이 정상 묶음과 통제된 burst 구분에 도움이 되는가**다. 모델 종류만 바꾸는 것이 아니라 같은 정답을 판단하는 입력 표현을 단일 메시지에서 과거·현재의 창으로 확장한다.

비교 시점은 현재 메시지의 feature snapshot이며 미래 메시지와 종료 summary를 쓰지 않는다. 기존 RF는 현재 수치, LSTM은 같은 수치 의미의 최근 최대 8개를 받는다. 따라서 원시 입력 의미와 판단 대상은 같지만 사용할 수 있는 과거 문맥은 다르다는 차이를 공개한다.

LSTM이 RF보다 좋지 않아도 가설 검증 결과로 기록한다. 최종 배포 모델이 반드시 LSTM이어야 하는 것은 아니다.

## 입력·모델 후보

기본 창 설정은 `operational_message_sequence_v1`과 `server_application_message_sequence_v1` 계약을 따른다. Train `(1538, 8, 7)`, Validation `(513, 8, 7)`이며 창마다 현재 메시지의 기존 label을 정답으로 사용한다.

| 후보 | hidden 크기 | 층 수 | 방향 | dropout |
| --- | ---: | ---: | --- | ---: |
| `lstm_h16` | 16 | 1 | 과거에서 현재로 단방향 | 0 |
| `lstm_h32` | 32 | 1 | 과거에서 현재로 단방향 | 0 |

새 모델 종류 두 개가 아니라 **LSTM 한 종류의 작은 설정 두 개**다. 작은 데이터에 후보 수와 모델 크기를 제한하며, hidden 크기만 바꾸고 나머지 조건은 같다. 창 크기 8은 이번 실험에서 고정하며 별도 탐색하지 않는다.

설계한 흐름은 다음과 같다.

```text
원본 Train 1,538행으로만 fit한 StandardScaler
  -> 창의 유효 timestep만 변환, 오른쪽 padding은 0
  -> float32 입력, lengths/mask로 padding 제외
  -> 단방향 LSTM
  -> 마지막 유효 메시지의 hidden 출력
  -> 출력 1개인 선형 층
  -> logit 및 sigmoid 이상 확률
```

각 창의 hidden state는 0에서 시작한다. 다른 창의 hidden state를 넘기거나 다른 연결의 기록을 공유하지 않는다. lengths/mask는 유효 부분을 고르는 용도이며 별도 학습 feature로 붙이지 않는다.

길이 1인 Validation 48건, 특히 malformed 두 종류 각 8건도 평가한다. 과거가 없으므로 이 구간에서 얻는 판단은 첫 메시지의 수치에서 나온 것이지 시계열 증거가 아니다.

## 학습 횟수와 종료 규칙

| 항목 | 설계 값 |
| --- | --- |
| 실행 장치·스레드 | CPU, 1개 스레드 |
| seed | 42 |
| 최대 epoch | 후보당 30 |
| batch 크기 | 64 |
| optimizer | Adam |
| learning rate | 0.001 |
| weight decay | 0.0001 |
| 학습 손실 | BCEWithLogitsLoss, 양성 가중치 1.0 |
| checkpoint 선택 | Validation 손실이 가장 낮은 epoch |
| 조기 종료 | Validation 손실의 엄격한 개선이 5 epoch 연속 없으면 종료 |
| Validation 포함 재학습 | 하지 않음 |

epoch 1회는 모든 Train 창을 한 번 사용하는 것이다. batch 64개씩 가중치를 갱신하며 후보당 최대 30회를 돌지만 조기 종료로 실제 횟수는 더 적을 수 있다. 정답을 모두 맞힐 때까지 무한히 조정하는 방식이 아니다.

Train 창의 배치 순서는 섞어도 **한 창 내부의 메시지 순서는 섞지 않는다**. Validation에서 가중치를 갱신하지 않으며 padding을 loss의 별도 정답으로 만들지 않는다. 정답은 창 하나당 현재 메시지 하나다.

실제 구현에서 epoch별 Train/Validation 손실, 실제 epoch 수, 최적 epoch·종료 이유·학습 시간·환경·산출물 해시를 저장했다. 동일 seed 재현은 해당 실행 환경에서 검증하며 장치·라이브러리가 달라도 완전히 같은 결과라는 보장은 하지 않는다.

## threshold·선정 기준

최저 Validation 손실 checkpoint의 이상 확률에 threshold 0.3/0.4/0.5/0.6/0.7/0.8/0.9를 적용한다. 두 후보이므로 비교 정책은 14개다. checkpoint 선택과 threshold 선택은 서로 다른 과정이며 둘 다 개발용 Validation을 이용한다.

선정 규칙은 기존 운영 LR/RF 실험과 같다.

1. 정상 FPR 10% 이하, 이상 Recall 50% 이상인 정책만 남긴다.
2. Recall을 최대화한다.
3. 동률이면 F1 높은 순, FPR 낮은 순, candidate_id 사전순, threshold 낮은 순으로 결정한다.
4. 충족 정책이 없다면 없다고 기록한다. 가장 좋아 보이는 후보를 억지로 통과시키지 않는다.

전체 accuracy·precision·recall·F1·ROC-AUC·confusion matrix와 시나리오별 FPR/Recall을 저장한다. 기존 선정 RF의 threshold 0.3과 같은 513개 현재 메시지에서 비교하며 FP/FN의 변화, 정상 묶음 오탐, burst 초반 및 이후 누락, 길이 1 구간을 따로 추적한다. 학습 종료·후보·threshold의 모든 근거를 남긴다.

## 누출·해석의 한계

- scaler는 겹친 창이 아니라 원본 Train 1,538행에만 fit한다. Validation 입력·padding·정답·metadata를 통계 학습에 넣지 않는다.
- 동일 campaign/run/session은 split 사이에 섞지 않으며 시계열 창은 과거·현재만 포함한다.
- 이 Validation은 이미 기존 모델 진단과 LR/RF 선정에 사용했다. LSTM의 최고 개발 점수는 독립 최종 성능이 아니다.
- Test 1,071건의 창 생성·모델 예측·평가는 최종 비교 단계까지 보류한다. Test 결과로 후보·threshold를 다시 고르지 않는다.
- 겹친 창의 상관과 생성기 편향, 작은 독립 캠페인 수, 본문 정보 부재가 남는다. 모든 관측을 실제 네트워크 침입 정답으로 해석하지 않는다.

## 이후 완료 조건

사전 완료 기준은 두 후보의 직접 학습, 유효 timestep 처리 검증, 학습 기록·저장 checkpoint 재로딩, 같은 Validation의 14개 정책 비교와 선정 RF 대비 결과 문서화였다. 후속 작업에서 이 조건을 완료했다. h16·0.5가 LSTM 후보 내 선정됐지만 RF보다 이상 누락이 늘어 전체 개선이라고 주장하지 않는다. 3단계는 학습·실험 완료로 표시하며 남은 큰 작업 묶음은 4개다.
