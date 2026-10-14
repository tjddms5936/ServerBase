# 운영 메시지 시계열 데이터셋

후속 상태: 이 문서는 시계열 입력 준비 단계의 설명이다. 이후 LSTM 직접 학습·Validation RF 비교를 완료했으며 현재 결과·다음 작업은 `docs/operational_lstm_experiment.md`와 `docs/project_roadmap.md`에서 확인한다.

## 이번 작업의 목적과 범위

기존 LR/RF는 현재 메시지의 수치 7개를 입력받는다. LSTM 개선 실험은 같은 메시지를 판단하되 **같은 연결에서 이미 관측한 최근 메시지들의 흐름**까지 사용할 수 있도록 입력을 준비하는 작업이다.

이번에는 시계열 창 구성·저장·검증과 다음 LSTM 실험 설계까지만 수행했다. scaler 학습, LSTM 학습, 모델 예측과 FastAPI 연결은 하지 않았다. 기존 LR/RF 모델·threshold·메시지 원본·API도 변경하지 않았다.

원본은 `operational_message_v2_campaign_holdout`, 새 데이터셋 ID는 `operational_message_sequence_v1`이다. 사전 캠페인 분할을 그대로 유지하며 Train/Validation만 읽는다. Test CSV, 수집 원본 JSONL, 세션 종료 요약은 읽지 않는다.

## 메시지 하나를 어떻게 창으로 만드는가

창 크기를 최대 8개 메시지로 고정했다. 각 메시지는 수치 7개를 갖는다. 한 창의 입력 크기는 `(8, 7)`이며, 현재 메시지까지의 과거 기록만 사용한다.

아래의 A1~A9는 **같은 run/session에서 순서대로 받은 메시지**다. B1은 다른 연결에서 받은 첫 메시지이므로 A의 기록과 합치지 않는다.

```text
현재 메시지  입력 창                              현재 정답
A1          [A1, 0, 0, 0, 0, 0, 0, 0]            A1의 label
A2          [A1, A2, 0, 0, 0, 0, 0, 0]           A2의 label
A8          [A1, A2, A3, A4, A5, A6, A7, A8]     A8의 label
A9          [A2, A3, A4, A5, A6, A7, A8, A9]     A9의 label
B1          [B1, 0, 0, 0, 0, 0, 0, 0]            B1의 label
```

여기서 0은 수치 7개가 모두 0인 빈 칸을 뜻한다. 뒤쪽 빈 칸을 채우는 방식을 오른쪽 padding이라고 한다. 정답은 창의 다수결이나 세션 최종 정답이 아니라 **현재 판단 대상 메시지의 기존 독립 정답**이다.

메시지가 하나 들어올 때마다 창을 하나 만들므로 stride는 1이다. 짧은 세션이나 첫 메시지도 버리지 않는다. 다른 세션의 기록으로 빈 칸을 채우거나, 세션 종료 뒤 전체 길이를 알아낸 것처럼 입력에 넣지 않는다.

## 세션 경계·순서·원본 행 보존

- `(campaign_id, run_id, session_id)`가 같은 메시지만 한 흐름으로 묶는다. 서로 다른 실행에서 우연히 같은 session_id를 써도 섞이지 않는다.
- 세션 안에서는 `request_index`를 정수 순서로 정렬하고 1부터 연속인지 확인한다. 번호 누락·중복·역행한 누적값이 있으면 조용히 이어 붙이지 않고 구성을 중단한다.
- 현재 메시지보다 뒤의 기록, 다른 세션·캠페인·분할, 종료 summary는 창에 넣지 않는다.
- 창을 만들기 위해 내부 순서를 정렬하더라도 최종 창의 행 순서는 원본 CSV의 현재 메시지 순서를 유지한다.
- 각 유효 timestep의 원본 행 위치와 row_id를 함께 저장한다. 저장 후 모든 창을 원본과 다시 대조한다.

따라서 Validation의 513개 창은 기존 RF가 판단한 동일한 513개 메시지에 대응한다. 평가 대상과 정답을 바꾸지 않고, 해당 메시지까지 사용할 수 있었던 문맥만 추가한 비교가 가능하다.

## padding·mask·lengths

첫 메시지의 길이가 1이면 다음처럼 저장한다.

```text
X:                [첫 메시지, 0, 0, 0, 0, 0, 0, 0]
lengths:          1
mask:             [True, False, False, False, False, False, False, False]
source_positions: [원본 행 번호, -1, -1, -1, -1, -1, -1, -1]
```

`True`인 칸만 실제 관측이다. 실제 수치가 0인 메시지도 mask가 True이면 유효한 입력이다. padding은 결측값을 0으로 대체한 것이 아니며, lengths는 현재 창에서 사용할 수 있는 관측 수이지 세션 최종 길이가 아니다.

다음 LSTM 학습에서는 padding을 유효 timestep으로 처리하지 않는다. 마지막 고정 칸인 8번째 칸이 아니라 **마지막 유효 메시지의 출력**을 읽는다. 창마다 초기 hidden state를 재설정해 다른 창·세션의 숨겨진 상태를 넘기지 않는 방식으로 설계했다.

## 입력 7개와 정규화 규칙

입력 순서와 의미는 기존 `server_application_message_v2`를 그대로 유지한다.

```text
packet_size
payload_size
connection_duration_ms
message_interval_ms
bytes_received_total
bytes_sent_total
error_count_total
```

식별자·정답·시나리오·창 길이·원본 위치·종료 요약은 추가 수치 feature로 넣지 않는다. 길이와 mask는 padding을 제외하기 위한 구조 정보로만 사용한다.

현재 NPZ에는 정규화되지 않은 float64 원본을 보관한다. 다음 단계의 StandardScaler는 **원본 Train 1,538행으로만 한 번 fit**해야 한다. 겹치는 창을 펼쳐서 fit하면 A1 같은 과거 메시지가 여러 번 등장하고 빈 padding도 섞여 평균·표준편차가 바뀐다.

학습 시 순서는 다음과 같다.

1. 원본 Train 메시지 1,538행으로 scaler를 fit한다. Validation은 fit에 넣지 않는다.
2. 같은 scaler로 Train/Validation 창의 유효 timestep만 변환한다.
3. 변환 후 padding 칸은 다시 0으로 유지하고 학습 텐서만 float32로 변환한다.
4. 유효 lengths와 mask에 맞춰 LSTM을 실행한다. padding을 포함한 평균이나 마지막 고정 칸의 출력으로 판단하지 않는다.

## 생성 결과

| 분할 | 창 shape | 캠페인 | run | 세션 | 길이 8 | 길이 1~7 | 길이 1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | `(1538, 8, 7)` | 3 | 36 | 144 | 822 | 716 | 144 |
| Validation | `(513, 8, 7)` | 1 | 12 | 48 | 274 | 239 | 48 |

Train 정답은 정상 1,049건·통제된 이상 489건, Validation은 정상 349건·통제된 이상 164건으로 원본과 같다. 모든 메시지를 유지하므로 메시지 coverage는 두 분할 모두 100%다. 메시지를 아예 관측할 수 없었던 헤더 거부 run은 기존 메시지 데이터셋과 동일하게 여전히 제외된다.

malformed 두 종류는 Train 각 24건·Validation 각 8건이며 모두 길이 1의 창이다. 이 64건을 버리거나 가상의 과거를 만들지 않았다. 이 경우 시계열 문맥이 없다는 사실을 이후 시나리오별 결과와 함께 공개해야 한다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_sequence_dataset.json` | 원본/출력 경로, 최대 길이 8, 오른쪽 padding, 현재 정답과 기대 행 수를 고정한다. |
| `data_preparation/operational_sequences.py` | 경계·연속 번호 검증, 창 구성, 저장·재로딩·원본 대조와 보고서 생성을 담당한다. |
| `scripts/prepare_operational_sequences.py` | 설정을 읽고 위 모듈을 실행하는 명령 진입점이다. |
| `tests/test_operational_sequences.py` | 미래·다른 연결 혼입, 잘못된 순서·mask·정답, 짧은 창, Test 접근 금지와 재현성을 검사한다. |
| `docs/operational_lstm_plan.md` | 다음 LSTM 실험의 가설·제한된 후보·학습 종료·선정 규칙을 설명한다. |

기본 출력은 다음과 같다. 데이터 파일은 Git에서 제외하고 생성 코드·설정·문서·작은 보고서를 추적한다.

```text
data/processed/operational_sequence/v1/
  window_protocol_lock.json
  sequence_schema.json
  train.npz
  validation.npz
  metadata_train.csv
  metadata_validation.csv
  sequence_manifest.json

reports/
  operational_sequence_dataset.json
  operational_sequence_dataset.md
```

NPZ는 여러 NumPy 배열을 하나의 압축 파일에 담는 형식이다. 문자열 객체나 pickle 없이 아래 수치 배열만 저장한다.

| 배열 | 크기 | 의미 |
| --- | --- | --- |
| `X` | `(메시지 수, 8, 7)` | 과거·현재의 원본 입력과 오른쪽 padding |
| `y` | `(메시지 수,)` | 현재 메시지의 정답 0/1 |
| `lengths` | `(메시지 수,)` | 각 창의 실제 관측 수 1~8 |
| `mask` | `(메시지 수, 8)` | 실제 관측 True, padding False |
| `source_positions` | `(메시지 수, 8)` | 해당 split 원본 CSV의 0부터 시작하는 행 위치, padding은 -1 |

metadata CSV는 기존 열을 유지하고 `window_id`, `context_length`, `context_start_request_index`, `source_row_ids`를 추가한다. 위치·정답·시나리오를 모델 입력과 분리한 추적 자료다.

`window_protocol_lock.json`은 첫 창을 만들기 전에 설정과 원본 해시를 저장한다. `sequence_schema.json`은 입력 계약이다. 완료 manifest는 저장 후 재로딩과 원본 대조가 통과했을 때만 생성하며 출력·원본·생성 코드의 해시를 기록한다.

## 실행과 재현

AI 폴더에서 실행한다. 고정 원본 메시지 데이터셋과 기존 AI 의존성 환경이 필요하며 C++ 서버나 FastAPI를 켤 필요는 없다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\prepare_operational_sequences.py
```

기본 결과는 이미 생성했다. 기존 데이터셋·보고서는 덮어쓰지 않으므로 재실행에는 **두 경로를 모두** 새로 지정한다.

```powershell
python .\scripts\prepare_operational_sequences.py --output-dir .\data\processed\operational_sequence\replay_v1 --report-prefix .\reports\operational_sequence_replay
python -m unittest discover -s tests -p "test_operational_sequences.py"
```

원본이 없는 Git checkout에서는 기존 독립 캠페인 수집·메시지 데이터셋 구성부터 재현해야 한다. 아무 순서의 공개 데이터 행이나 별도 CSV를 이 창의 원본으로 대체하지 않는다.

## 검증과 한계

새 테스트 20개를 포함해 전체 223개가 통과했다. 실제 데이터의 모든 창에서 마지막 유효 입력·정답·원본 행 위치가 현재 메시지와 일치했다. 다른 임시 출력으로 재구성한 NPZ 2개·metadata CSV 2개·schema 1개의 SHA-256이 일치했다. 시간·실행 경로가 포함된 lock/manifest 자체는 재실행마다 같을 필요가 없다.

캠페인·run·세션·행의 Train/Validation 중복은 모두 0이다. 기존 보강 산출물 54개·보고서 4개와 Train/Validation 원본 입력의 해시도 보존했다. 실행 검사에서 Test CSV·원본 JSONL·종료 summary 접근과 scaler/model fit·예측을 차단한 상태로 구성을 완료했다.

창끼리는 같은 과거를 공유하므로 메시지 1,538개가 독립된 1,538번의 실험을 뜻하지 않는다. 같은 생성기와 작은 캠페인 수의 편향도 남는다. 또한 본문 필드가 여전히 없으므로 LSTM이 본문 유효성을 복원하거나 반드시 RF보다 좋아진다고 주장할 수 없다.

## 다음 작업

`configs/operational_lstm_plan.json`을 실제 학습 설정으로 검증·잠금하고 PyTorch 기반 LSTM 두 후보를 직접 학습한다. scaler는 원본 Train만 사용하고 epoch별 손실·최적 checkpoint·조기 종료를 기록한다. 같은 Validation 513건에서 고정 threshold 후보를 비교하며 선정 RF와 오분류·prefix·짧은 창 coverage를 대조한다. Test는 계속 보류한다.
