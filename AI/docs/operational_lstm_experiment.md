# 운영 LSTM 직접 학습·Validation 비교

후속 상태: 이 문서는 LSTM 개발 비교 단계의 기록이다. 이후 최종 Test·비용 평가에서 RF의 승인이 보류됐으며 최신 결과·다음 보정 작업은 `docs/operational_final_evaluation.md`와 `docs/project_roadmap.md`에서 확인한다.

## 이번에 한 작업

최근 최대 8개 메시지의 흐름이 단일 메시지 RF보다 정상 묶음·통제된 burst 구분에 도움이 되는지 실험했다. 기존 시계열 데이터셋을 사용해 PyTorch LSTM 두 후보의 가중치를 직접 학습하고 같은 Validation 513건에서 기존 선정 RF와 비교했다.

미리 학습된 LSTM을 가져온 것이 아니다. PyTorch의 LSTM·optimizer 구현을 이용하되 무작위 초기 가중치에서 시작해 우리 Train의 독립 시나리오 정답으로 학습했다. 이번 범위는 개선 가설의 구현·직접 학습·개발 비교이며 최종 Test 성능이나 실제 서버 배포 검증이 아니다.

**결과는 전체 개선이 아니었다.** 선정 LSTM은 RF보다 정상 오탐을 줄였지만 이상 누락을 크게 늘렸다. 사전 후보·threshold·선정 기준을 결과에 맞춰 바꾸지 않았고 기존 RF를 교체하지 않았다.

## 데이터·판단 시점

- 원본 메시지 데이터셋: `operational_message_v2_campaign_holdout`
- 시계열 데이터셋: `operational_message_sequence_v1`
- Train: 원본 메시지 1,538건, 창 `(1538, 8, 7)`
- Validation: 원본 메시지 513건, 창 `(513, 8, 7)`
- 창: 같은 campaign/run/session의 과거·현재 최대 8개, 현재 메시지의 기존 label

기존 RF는 현재 입력 7개만 사용하고 LSTM은 같은 입력 의미의 과거·현재 문맥을 사용한다. 판단 대상·정답·현재 시점은 같지만 입력 문맥과 모델이 함께 달라진다는 점을 공개한다. 미래 메시지·다른 연결·종료 summary를 쓰지 않으며 모든 첫 메시지와 짧은 세션을 유지한다.

원본 Train 1,538행으로 StandardScaler를 한 번 fit했다. 겹치는 창을 펼쳐 중복 입력·padding을 통계 학습에 넣지 않았다. Train/Validation 창의 유효 timestep만 같은 scaler로 변환하고 float32로 바꿨다. padding은 0으로 유지했다.

## 실제 모델·학습 규칙

두 후보는 hidden 크기 16/32, LSTM 1층·단방향·dropout 0과 출력 1개의 선형 층이다. 매 창의 상태는 0에서 시작한다. `pack_padded_sequence`로 padding을 순환 계산에서 제외하고 최종 유효 hidden 출력을 판단에 사용했다. 길이순 내부 정렬이 원래 배치 정답 순서를 바꾸지 않는지도 검사했다. [PyTorch의 가변 길이 입력 API](https://docs.pytorch.org/docs/2.14/generated/torch.nn.utils.rnn.pack_padded_sequence.html), [LSTM 출력 정의](https://docs.pytorch.org/docs/2.14/generated/torch.nn.LSTM.html)를 따랐다.

| 항목 | 고정 조건 |
| --- | --- |
| CPU 환경 | PyTorch 2.14.1+cpu, intra-op 스레드 1 |
| seed | 42 |
| epoch 상한 | 후보당 30 |
| batch | 64 |
| optimizer | Adam, learning rate 0.001, weight decay 0.0001 |
| loss | BCEWithLogitsLoss, 양성 가중치 1.0 |
| checkpoint | Validation 손실 최소 epoch |
| 조기 종료 | 엄격한 손실 개선이 5 epoch 연속 없을 때 |
| threshold 후보 | 0.3/0.4/0.5/0.6/0.7/0.8/0.9 |
| 선정 제약 | FPR 10% 이하, Recall 50% 이상 |
| 선정 순서 | Recall 최대, F1 최대, FPR 최소, 후보 id, threshold 순 |

Train 배치 순서는 섞지만 창 내부의 시간 순서는 보존한다. Validation 손실은 gradient 없이 관측하며 Validation으로 가중치를 갱신하거나 Train과 합쳐 재학습하지 않는다. CPU 스레드 항목은 intra-op 설정이며 전체 프로세스의 스레드 수가 1이라는 뜻은 아니다. inter-op 설정과 나머지 실행 환경도 보고서에 남겼다.

설정·데이터·기준 모델·코드·환경을 scaler fit과 첫 gradient 갱신 전에 잠갔다. 원래 설계 파일의 `design_only_not_trained` 상태는 입력 준비 당시 기록으로 유지하고, 실제 실행은 별도 실험 설정·protocol·완료 manifest에 남겼다.

## 실제 epoch와 선정

| 후보 | 사용 epoch | 최적 epoch | 최저 Validation loss | 파라미터 수 |
| --- | ---: | ---: | ---: | ---: |
| `lstm_h16` | 30 | 30 | 0.270547 | 1,617 |
| `lstm_h32` | 30 | 27 | 0.255707 | 5,281 |

두 후보 모두 30 epoch 상한까지 수행했다. h32의 최적 epoch는 27이지만 남은 3 epoch가 patience 5에 도달하지 않아 종료 이유는 조기 종료가 아니라 `max_epochs`다. 마지막 30번째 가중치 대신 27번째 최적 가중치를 복원해 저장·평가했다.

14개 후보/threshold 조합 중 5개가 제약을 충족했고 **h16·threshold 0.5**가 선정됐다. h32의 최저 손실은 더 낮았지만 선정 목적은 손실 최소가 아니라 제약 안에서 Recall 최대다. checkpoint를 고르는 기준과 후보·threshold를 고르는 기준을 구분한다.

h16·0.4는 Recall 99.39%지만 FPR 14.61%로 탈락했고 h32·0.4도 FPR 12.03%로 탈락했다. 이를 보고 사후에 0.41 같은 threshold를 추가하거나 FPR 상한을 완화하지 않았다. 이번 결과는 사전에 제한한 후보 범위의 결과이지 LSTM이 낼 수 있는 모든 성능의 상한을 증명한 것은 아니다.

## 동일 Validation 비교

| 지표 | 기존 선정 RF leaf=5·0.3 | 선정 LSTM h16·0.5 |
| --- | ---: | ---: |
| FP | 30 | 13 |
| FN | 9 | 50 |
| 정상 FPR | 8.60% | 3.72% |
| 이상 Recall | 94.51% | 69.51% |
| F1 | 88.83% | 78.35% |
| Accuracy | 92.40% | 87.72% |

LSTM의 confusion matrix는 TN 336·FP 13·FN 50·TP 114, Precision 89.76%·ROC-AUC 0.9612다. 전체 정답률이나 정상 오탐 하나만 보고 RF보다 좋다고 주장하지 않는다. 현재의 Recall 우선 기준에서는 RF가 더 강한 개발 비교 대상이다.

같은 행에서 RF의 FP 23건·FN 2건을 LSTM이 수정했지만 FP 6건·FN 43건을 새로 만들었다. 따라서 FP의 순감소는 17건, FN의 순증가는 41건이다. 단순 건수 차이와 실제 같은 메시지의 변화도 구분했다.

## 시나리오·짧은 창·초기 판단

| 시나리오 | 메시지 | RF FP/FN | LSTM FP/FN |
| --- | ---: | --- | --- |
| 정상 일반 | 110 | FP 7 | FP 0 |
| 정상 묶음 | 130 | FP 21 | FP 13 |
| 정상 분할 | 109 | FP 2 | FP 0 |
| 통제된 burst | 148 | FN 9 | FN 34 |
| 너무 짧은 malformed 본문 | 8 | FN 0 | FN 8 |
| 내부 길이 필드가 잘못된 malformed 본문 | 8 | FN 0 | FN 8 |

길이 1인 Validation 메시지 48건도 모두 평가했다. 이 구간의 RF는 FP 14·FN 0, LSTM은 FP 0·FN 22였다. FN 22건에는 malformed 16건과 burst 최초 메시지 6건이 포함된다. 정상 최초 메시지를 정상으로 판단하는 대신 초반 이상도 많이 놓쳤다.

burst 세션 8개는 두 모델 모두 언젠가는 한 번 이상 양성으로 판단했다. 그러나 RF는 모두 첫 메시지에서 양성이었고 LSTM은 두 세션만 첫 메시지, 나머지 여섯 세션은 5~6번째 메시지에서 처음 양성이었다. 한 번 탐지했다는 이유로 이전·이후의 누락을 정답으로 바꾸지 않았다.

JSON의 첫 양성까지 경과시간은 메시지를 관측할 때의 시간차다. 모델의 CPU 추론 지연시간이나 TCP/HTTP 응답시간 측정이 아니며 다음 비용 benchmark와 혼동하지 않는다.

## 한계와 결론

Validation은 이미 LR/RF 선정에 사용했고 이번에는 epoch·후보·threshold도 고르는 데 사용했다. 따라서 독립 최종 성능은 아니다. 메시지별 창은 겹치고 Train 캠페인 3개·Validation 캠페인 1개, 같은 생성기·단일 seed의 한계도 남는다.

본문 필드는 여전히 없다. 길이 1에 과거 문맥도 없다. 이 결과만으로 “LSTM이 근본적으로 불가능하다”, “데이터가 부족한 것이 유일한 원인이다”, “특정 feature가 원인이다”라고 단정하지 않는다. class weight, 다른 창 크기, 더 세밀한 threshold 등을 사후 가설로 생각할 수는 있지만 이번 실험 결과를 보고 자동 추가 학습하지 않았다.

**이번 제한된 실험에서는 LSTM 도입이 Recall 우선의 전체 개선으로 이어지지 않았다.** 오탐 감소와 누락 증가의 교환을 확인했고 RF를 유지했다. 개선 가설을 설계·직접 학습·검증했다는 연구 과정과 실제 개선 여부를 구분한다.

## 파일 역할과 산출물

| 파일 | 역할 |
| --- | --- |
| `requirements-lstm.txt` | 공식 CPU wheel 저장소와 검증한 torch 버전을 지정한다. |
| `configs/operational_lstm_experiment.json` | 기존 설계 파일, 출력 경로, 실험 ID와 기대 행 수를 지정한다. |
| `training/operational_lstm.py` | LSTM·유효 입력 변환·학습·조기 종료·checkpoint 저장/재로딩을 구현한다. |
| `training/run_operational_lstm_experiment.py` | strict 설정·데이터·RF 검증, 학습 전 lock, Train 전용 fit, 14개 정책 선정과 보고서를 처리한다. |
| `evaluation/compare_operational_lstm.py` | 동일 Validation의 RF/LSTM 오분류·시나리오·길이별 coverage·burst prefix를 비교한다. |
| `tests/test_operational_lstm.py` | padding·배치 정렬·상태 분리·Validation 무갱신·누출·산출물 보호·재현을 검사한다. |
| `tests/run_lstm_reproducibility.py` | 실제 완료 실험을 임시 경로에서 다시 학습하고 비교 증거를 저장한다. |

```text
artifacts/operational_sequence/lstm_v1/
  protocol_lock.json
  scaler.joblib
  feature_schema.json
  sequence_schema.json
  label_map.json
  candidates/lstm_h16/
    model.pt
    training_history.csv
    candidate_report.json
    validation_predictions.csv
  candidates/lstm_h32/             같은 구성
  selection.json
  selected_validation_predictions.csv
  experiment_manifest.json

reports/
  operational_lstm_validation.json
  operational_lstm_validation.md
  operational_lstm_validation.predictions.csv
  operational_lstm_reproducibility.json
```

`model.pt`는 모델 전체 객체가 아니라 가중치·입력 계약·최적 epoch를 담은 checkpoint다. 직접 만든 신뢰 가능한 파일을 `weights_only=True`, CPU로 읽고 가중치 해시·구조·재예측을 검증한다. 이 옵션만으로 임의 외부 파일의 안전성이 보장되는 것은 아니다. [PyTorch 저장 모델 로딩 API](https://docs.pytorch.org/docs/2.14/generated/torch.load.html)를 사용한다.

`selection.json`에는 LSTM 후보 내 선정 모델과 threshold·scaler·schema를 묶는다. 기본 0.5 비교와 선정 정책을 구분하고, 제약 충족 후보가 없다면 null 경로와 미선정 상태로 완료한다. 기존 RF의 선정 정책은 변경하지 않았다.

## 실행·재현·검증

AI 폴더에서 기존 의존성·고정 메시지/시계열 데이터셋·기준 RF가 준비된 상태로 실행한다. C++ 서버나 FastAPI를 실행할 필요는 없다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python -m pip install --target .deps_lstm -r requirements-lstm.txt
python -X utf8 .\training\run_operational_lstm_experiment.py
```

`.deps_lstm`은 이 설치 명령으로 생성하는 로컬 학습 의존성 폴더다. `.deps`를 덮어쓰거나 전역 Python 패키지를 변경하지 않으며 Git에서도 제외한다. CPU용 torch와 필요 의존성만 추가했다.

기본 결과는 이미 존재한다. 다시 학습하려면 출력 경로를 둘 다 새로 지정한다.

```powershell
python -X utf8 .\training\run_operational_lstm_experiment.py --artifact-dir .\artifacts\operational_sequence\lstm_replay --report-prefix .\reports\operational_lstm_replay
```

완료된 원본과 실제 재학습을 대조하는 별도 검사도 제공한다. 기본 재현 보고서는 이미 있으므로 재실행에는 새 보고서 파일을 지정한다. 재학습 임시 모델만 비교 후 제거하고 실제 원본은 보존한다.

```powershell
python -X utf8 .\tests\run_lstm_reproducibility.py --report-path .\reports\operational_lstm_reproducibility_replay.json
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```

실제 재학습에서 두 후보의 epoch별 손실·가중치·checkpoint 해시, 14개 지표·선정·전체 비교 CSV 해시가 일치했다. 새로운 LSTM 관련 테스트 22개를 포함해 전체 245개가 통과했다. 실제 최초 학습도 Test·원본 JSONL·종료 summary 접근과 기준 LR/RF 재학습·트래픽 송신을 차단한 상태로 완료했다. 기존 원본·시계열·RF·코드·lock 해시를 검증했다.

재현 보고서는 동일 환경에서의 학습 재현 증거이며 독립 데이터 일반화 증거가 아니다. 다른 플랫폼·버전까지 수치 동일성을 보장하지 않는다. [PyTorch 재현성 문서](https://docs.pytorch.org/docs/2.14/notes/randomness.html)의 제한도 따른다.

## 다음 작업과 남은 단계

시계열 입력 준비와 LSTM 직접 학습·개발 비교를 마쳐 3단계를 완료했다. 이제 큰 작업 묶음은 4개다.

1. 최종 모델 성능·추론 지연시간·메모리·산출물 비용 비교와 승인
2. 실제 모델의 FastAPI 연결
3. C++ TCP 종단 통합과 장애·부하 검증
4. 재현·최종 보고서·GitHub 포트폴리오 정리

바로 다음 작업에서는 **Test를 열기 전에** 각 모델의 비교 정책·threshold와 Validation/비용 기준의 배포 후보·확인 규칙을 고정한다. 이후 Test 1,071건의 시계열 창을 같은 규칙으로 생성해 최종 비교하고 승인 여부를 확인한다. Test에서 가장 점수가 높은 모델로 사후 교체하거나 threshold를 다시 고르지 않는다. 실패하면 검증 실패로 기록하고 별도 새 개발·독립 평가 계획을 세운다.
