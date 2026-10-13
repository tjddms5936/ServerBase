# 선정 모델의 오분류·관측 한계 분석

## 이번에 한 일

선정된 LR C=1·threshold=0.7을 바꾸지 않고 Validation 138건을 다시 예측했다. FP 3건·FN 24건의 입력·확률·메시지 위치를 추적하고, 현재 입력이 제공하는 단서와 제공하지 않는 정보를 구분했다. 새 모델 학습, threshold 재선정, Test 평가 또는 새 트래픽 수집은 하지 않았다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `evaluation/analyze_operational_errors.py` | 선정 실험·모델·예측 기록을 검증하고 오분류·세션 prefix·입력 분포·표현 반례를 분석한다. |
| `configs/operational_augmentation_plan.json` | 독립 캠페인·새 생성기 제어·사전 분할·정답 정책·품질 기준을 설계한다. 현재 수집기로 실행하는 설정이 아니다. |
| `tests/test_operational_error_analysis.py` | 재학습·송신·Test 접근 금지, 선정 보존, 메시지 정렬·기여도·표현 반례와 보강 계획을 검사한다. |
| `reports/operational_error_analysis.json` | 27건의 상세 추적, 기여도·가까운 반대 정답 Train 예시, 입력 요약, source 해시와 설계를 보관한다. |
| `reports/operational_error_analysis.csv` | 27건의 row_id·run/session·메시지 번호·원본 행 번호·입력 7개·정답·확률·예측을 보관한다. |
| `reports/operational_error_analysis.md` | 실제 결과와 관측 한계를 한글로 설명한다. |

## 코드 흐름

1. `load_operational_data()`로 고정 Train/Validation만 로드한다. Test는 해시 검사에도 열지 않는다.
2. `load_selection()`이 완료 manifest·protocol 잠금·선정 조합·모델·schema·정답 정의·예측 CSV를 검증한다. 저장 threshold와 전체 후보 선정 규칙이 맞는지도 다시 확인한다.
3. 기존 `load_verified_model()`을 이용해 후보 Pipeline과 Train 전용 전처리를 검사한다. 선정 threshold로 재예측한 지표가 원래 기록과 같아야 한다.
4. 틀린 Validation 행을 골라 `error_cases()`에서 원본 추적 metadata와 입력을 결합한다. 원본 JSONL 자체는 열지 않고 보존된 `source_line`으로 추적 위치를 남긴다.
5. 세션별 메시지 위치와 첫 양성 판단 위치를 진단한다. Train/Validation의 시나리오별 입력 범위도 따로 비교한다.
6. 정상 본문의 문자열 길이 필드만 잘못 바꾼 오프라인 구성 반례로 현재 표현의 누락 정보를 살펴본다. 네트워크로 송신하거나 새 데이터셋에 넣지 않는다.
7. 독립 데이터 보강 설계와 실제 결과를 JSON·CSV·Markdown으로 저장한다. 모델·정답·threshold는 변경하지 않는다.

## 실제 오분류

| 시나리오 | FP | FN | 위치 |
| --- | ---: | ---: | --- |
| 정상 묶음 전송 | 3 | 0 | 하나의 세션에서 6~8번째 메시지 |
| 통제된 burst | 0 | 16 | 네 세션의 첫 4개 메시지 |
| 너무 짧은 본문 | 0 | 4 | 세션별 첫 메시지, payload 1바이트 |
| 잘못된 문자열 길이 | 0 | 4 | 세션별 첫 메시지, 정상과 겹치는 크기 |

burst 네 세션은 모두 5번째 메시지부터 이상으로 판정됐다. 이 위치는 **현재 메시지 모델의 진단 결과**이며 세션 전체를 정답으로 간주해 원래 Recall을 높인 것이 아니다. 초반 네 메시지를 정상으로 재라벨링하지도 않았다.

정상 묶음 FP 세 건은 같은 수신 누적값 2240바이트와 아주 짧은 파싱 간격을 보였다. 짧은 파싱 간격이 정상에서도 발생할 수 있다는 사례다. 이 세 건이 정상 시나리오 전체나 모든 TCP 묶음 전송을 대표하는 것은 아니다.

## 가중치 기여도란 무엇인가

LR 점수는 다음과 같이 계산된다.

```text
점수 = 기본값 + (표준화된 입력1 × 가중치1) + ... + (표준화된 입력7 × 가중치7)
```

각 곱을 이 보고서에서 기여도라고 부른다. 양수는 현재 모델의 이상 점수를 높이고 음수는 낮춘다. 합계가 실제 `decision_function()` 점수와 같은지도 검사했다. threshold 0.7에 해당하는 점수 경계는 `log(0.7 / 0.3)`이다.

첫 FP에서는 짧은 파싱 간격, 연결 시간, 수신·송신 누적값 항이 양의 기여를 보였다. 하지만 **가중치 계산을 분해한 것이지 네트워크 이상 원인을 입증한 것은 아니다.** 중복 입력·상관관계와 생성기 패턴도 반영될 수 있다.

가까운 반대 정답 예시는 Train 평균·표준편차 기준의 거리로 찾았다. FN이면 가까운 정상 Train 행, FP이면 가까운 이상 Train 행을 남겼다. 이를 위해 새 모델이나 scaler를 fit하지 않았다. 거리가 작아도 의미적으로 동일한 프로토콜이라는 뜻은 아니다. packet_size와 payload_size가 중복돼 거리 계산에 두 번 영향을 주는 한계도 있다.

## 관측 가능한 단서와 없는 정보

### 너무 짧은 본문

현재 CS_CHAT은 문자열 길이 2바이트와 정수 4바이트를 포함해 최소 고정 필드 6바이트가 필요하다. payload 1바이트는 이보다 짧다. 따라서 **현재 `payload_size`에도 형식 오류의 단서가 있다.** 이번 LR은 그 단서를 결정적인 분기처럼 사용하지 않아 threshold 0.7에서 네 건을 놓쳤다.

이 사례를 모든 malformed 메시지가 원천적으로 관측 불가능하다는 주장에 사용하지 않는다. 현재 C++의 본문 검증은 이미 실패를 검출하고 연결을 종료한다. 그 검증을 새 AI 개선 성과처럼 포장하지 않는다.

### 잘못된 문자열 길이

본문의 실제 크기는 그대로인데 내부 문자열 길이 필드만 60000으로 바꿀 수 있다. 입력 7개에는 이 선언 길이나 본문 바이트가 없다. 시각과 누적값도 같게 고정하면 정상·비정상 본문의 표현이 같아질 수 있다.

분석 도구는 이런 두 프레임을 메모리 안에서 구성해 같은 입력이면 같은 모델 출력이 나오는 것을 확인했다. **실제 수집된 두 행이 같다는 증거는 아니다.** 실제 Train/Validation의 완전히 같은 feature·다른 정답 그룹은 0개였다. 비슷한 크기·시각으로 우연히 분류할 수 있어도 본문 유효성을 직접 관측한 것은 아니다.

### 현재 오류 수가 0인 이유

`Session::ParsePackets()`에서 PacketParsed observer가 먼저 호출되고, 그 후 `ServerSession::HANDLE_CP_CHAT()`의 본문 검증이 수행된다. FeatureCollector의 메시지 snapshot은 이 앞 시점의 카운터를 저장한다.

본문 오류가 나중에 발생해도 과거 메시지의 `error_count_total`에 덧붙이지 않는다. 종료 summary의 오류를 끌어오면 실시간 판단 시점에는 없던 정보를 사용하게 된다. 데이터 정답 역시 오류 수가 아니라 송신 전에 정의한 독립 시나리오에서 가져온다.

### 시계열 모델의 한계

burst의 반복 패턴에는 과거 메시지를 이용하는 창이 도움이 될 가능성이 있다. 그러나 첫 메시지에는 그 과거가 없고, malformed 두 시나리오는 한 메시지 뒤 종료된다. LSTM이 빈 과거·누락된 본문 필드를 자동 복원할 수는 없다. 짧은 세션 처리, 판단 시점, 탐지 지연과 coverage를 별도로 설계해야 한다.

## 실행별 분포 차이

burst 연결 시간의 Train 최대는 3.445ms, Validation 최대는 17.847ms였다. Validation 56건 중 43건이 같은 시나리오의 Train 범위를 벗어났다. 반면 전체 Train의 정상 긴 연결까지 합친 범위 안에는 들어갈 수 있어 전체 범위만 보면 차이를 놓칠 수 있다.

이 결과는 실행·스케줄링·생성기 조건을 추가 확인할 근거다. 작은 단일 캠페인의 범위 차이를 통계적으로 검증된 분포 이동이나 유일한 오분류 원인으로 단정하지 않는다.

## 실행 방법

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\evaluation\analyze_operational_errors.py
python -m unittest discover -s tests -p "test_*.py"
```

고정 데이터셋과 완료된 선정 실험이 필요하다. 기본 결과가 이미 있으면 새 경로로 재검사한다.

```powershell
python .\evaluation\analyze_operational_errors.py --report-prefix .\reports\operational_errors_recheck
```

성공은 종료 코드 0, 입력·선정·저장 검증 실패는 2다. 기존 결과와 선정 실험 내부를 덮어쓰지 않는다. 신뢰할 수 없는 joblib은 로드하지 않는다.

## 다음 작업

독립 수집 설계는 `docs/operational_data_augmentation.md`와 `configs/operational_augmentation_plan.json`에 정리했다. 다음 구현은 생성기 제어와 캠페인 전체 분리, 새 캠페인 수집·검증이다. 이번에는 분석·설계만 완료했고 새 캠페인은 0개다. 로드맵 2단계는 진행 중이며 전체 큰 작업 묶음은 아직 6개가 남는다.
