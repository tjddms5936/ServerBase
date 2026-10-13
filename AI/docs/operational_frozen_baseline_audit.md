# 기존 LR의 새 Validation 진단

## 이번 작업의 의미

기존 모델을 바꾸지 않은 채 실행 조건이 다른 새 데이터에서 얼마나 잘 동작하는지 확인했다. 새 데이터를 수집한 것과 새 데이터로 모델을 학습한 것은 다른 단계다. 이번에는 **기존 LR C=1과 threshold 0.7을 그대로 사용해 새 Validation 513건만 예측**했다.

기존 모델은 v1 Train 126건으로 학습한 StandardScaler와 Logistic Regression의 Pipeline이다. 새 Train 1,538건이나 새 Validation으로 scaler 평균·분산, 모델 가중치·기본값을 다시 학습하지 않았다. FastAPI와 C++은 변경하지 않았고 실제 추론 API는 계속 dummy 상태다.

## 평가 범위

| 항목 | 이번 사용 |
| --- | --- |
| 기존 모델·scaler·선정 threshold | 로딩·검증·그대로 사용 |
| 새 Validation 513건 | 모델 예측·지표·오분류·세션 prefix 진단 |
| 새 Train 1,538건 | 공통 로더의 형식·행 정렬·해시 검증만 수행, 학습·예측 없음 |
| 기존 v1 Train/Validation CSV | 읽지 않음. 이전 지표는 저장된 selection.json에서 참조 |
| 기존/새 Test CSV | 해시 검사에도 읽지 않음 |
| 원본 JSONL·종료 summary | 읽지 않고 입력이나 정답에 합치지 않음 |
| threshold 탐색·모델 후보 선정 | 하지 않음 |

새 Validation의 정상 정답은 349건, 통제된 이상 정답은 164건이다. 캠페인 1개에서 14개 run·56개 세션을 계획했지만, 헤더 거부 2개 run·8개 세션은 메시지 snapshot이 없어 메시지 성능에서 제외했다. 관측 가능한 **12개 run·48개 세션·513개 메시지**가 이번 평가 대상이다. 헤더 거부를 AI가 탐지했다고 세지 않는다.

## 기존 모델이 그대로인지 확인하는 과정

1. 기존 완료 experiment manifest와 selection.json·모델·schema·label map 해시를 대조한다.
2. 선정 protocol, LR C=1, 양성 클래스 1, `>=` 비교, threshold 0.7을 검증한다.
3. 새 데이터의 의미 계약·입력 순서·단위·타입이 기존 모델과 호환되는지 확인한다. 학습 데이터셋 ID가 다르다는 사실을 숨기지 않는다.
4. 후보 학습 manifest와 학습 보고서에서 기존 scaler 표본 수 126, 평균·분산·scale, 모델 가중치·기본값·설정을 대조한다. 새 Train 통계에 맞춰 다시 fit하지 않는다.
5. 새 데이터의 사전 캠페인 registry에 기록한 기준 모델·threshold와 현재 모델이 같은지 확인한다. Validation 캠페인에 배정한 run이 그대로인지 검사한다.
6. 예측 전후 메모리 모델 상태와 고정 입력·모델 파일 해시를 검사한다. 예측 호출은 새 Validation 전체에 한 번만 수행한다.

후보 파일의 `decision_policy.json`에는 초기 baseline 정책 0.5가 남아 있다. 최종 선정 실험의 `selection.json`은 0.7이다. 이번 진단은 수집 전에 고정한 **0.7**을 사용하며 두 정책을 혼합하거나 파일을 고쳐 덮어쓰지 않는다.

joblib은 직접 학습한 신뢰 가능한 로컬 산출물만 로딩한다. SHA-256 일치가 외부 pickle 파일의 안전성을 보증하는 것은 아니다.

## 전체 결과

| 지표 | 기존 선정 Validation 138건 | 새 Validation 513건 |
| --- | ---: | ---: |
| Accuracy | 80.43% | 73.10% |
| Precision | 93.02% | 56.19% |
| Recall | 62.50% | 71.95% |
| F1 | 74.77% | 63.10% |
| ROC-AUC | 0.8856 | 0.8342 |
| 정상 FPR | 4.05% | 26.36% |

새 confusion matrix는 다음과 같다. 행은 실제 정답, 열은 모델 판단이다.

| 실제 정답 | 정상 판단 | 이상 판단 |
| --- | ---: | ---: |
| 정상 349건 | TN 257 | FP 92 |
| 통제된 이상 164건 | FN 46 | TP 118 |

- FP 92건: 정상 메시지를 이상이라고 잘못 판단했다.
- FN 46건: 통제된 이상 메시지를 정상이라고 놓쳤다.
- FPR 26.36%는 `92 / 349`다. 전체 메시지 513건을 분모로 사용하지 않는다.
- Recall 71.95%는 `118 / 164`다. 전체 정답률과 다른 지표다.

기존 선정 기준의 FPR 10% 이하에는 실패했고 Recall 50% 이상에는 통과했다. 이 기준을 새 데이터에서 참고로 확인했을 뿐 새로운 threshold를 선택하지 않았다.

기존과 새 데이터는 표본 수, 정상·이상 비율, 세션 길이·송신 설정이 다르다. FP가 3→92건이라는 건수만으로 성능을 비교하면 안 된다. Recall 상승도 **모델 개선의 결과가 아니다**. 같은 모델의 평가 데이터가 바뀌었으며 FPR과 F1은 나빠졌다.

## 시나리오별 결과

| 시나리오 | 메시지 | FP | FN | 해당 지표 |
| --- | ---: | ---: | ---: | --- |
| normal_chat | 110 | 41 | 0 | FPR 37.27% |
| normal_batched_chat | 130 | 51 | 0 | FPR 39.23% |
| normal_fragmented_chat | 109 | 0 | 0 | FPR 0% |
| controlled_burst_chat | 148 | 0 | 30 | Recall 79.73% |
| malformed_chat_payload | 8 | 0 | 8 | Recall 0% |
| malformed_length_chat | 8 | 0 | 8 | Recall 0% |

정상 오탐 92건은 일반 전송 41건·묶음 전송 51건에 집중됐다. 세션 기준으로는 일반 정상 8개 중 5개, 묶음 정상 8개 중 4개 세션에서 적어도 한 번 오탐했다. 이는 메시지 단위 FPR과 별도의 참고 진단이다.

두 malformed 시나리오 16건은 모두 놓쳤다. 짧은 payload에는 크기 단서가 있지만 기존 모델·정책이 해당 사례를 양성으로 판단하지 않았다. 잘못된 내부 문자열 길이 필드는 현재 입력 7개에 없다는 기존 관측 한계도 유지된다. 프로토콜 파서는 본문 오류를 거부하지만, 그 기존 검증 기능을 AI 성능으로 바꿔 계산하지 않는다.

## burst prefix 진단

burst 8개 세션 모두 적어도 한 번 양성으로 판단했지만 메시지 30건은 여전히 놓쳤다. 세션당 첫 양성 위치는 **1/2/5/9번째** 메시지로 달랐다. 처음에 양성으로 판단했다가 중간 메시지를 다시 정상으로 놓친 세션도 있었다. 한 번 잡았다는 이유로 세션의 모든 메시지를 정답 처리하지 않는다.

| 메시지 위치 | 관측 수 | FN |
| --- | ---: | ---: |
| 1 | 8 | 5 |
| 2 | 8 | 6 |
| 3 | 8 | 5 |
| 4 | 8 | 6 |
| 5~8 각각 | 8 | 2 |
| 9~13 각각 | 8 | 0 |

누락 30건은 모두 1~8번째에 있었다. 이 결과는 초기 prefix에서의 한계를 보여 주지만 시계열 모델의 개선을 미리 보장하지 않는다. 뒤쪽 번호에서는 짧은 세션이 빠져 분모가 작아진다. prefix별 실제 관측 수와 세션별 첫 양성의 연결 시간·첫 메시지 이후 경과시간을 JSON에 기록했다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `evaluation/audit_frozen_operational_baseline.py` | 기존 모델의 학습 provenance와 새 캠페인 계약을 따로 검증하고 Validation만 예측·보고한다. |
| `tests/test_frozen_operational_baseline.py` | 재학습 금지, 새 Validation 단독 예측, Test·기존 CSV·JSONL 접근 차단, 모델 변조·정책 변경·기존 출력 보호와 prefix 분모를 검사한다. |
| `reports/operational_frozen_baseline_audit.json` | 전체·시나리오·run별 지표, 세션·prefix, 관측 coverage, 원본·모델·코드 해시와 수행 범위를 기록한다. |
| `reports/operational_frozen_baseline_audit.md` | 자동 생성한 한글 결과 보고서다. |
| `reports/operational_frozen_baseline_audit.predictions.csv` | Validation 513건 전부의 metadata·입력 7개·정답·확률·예측·threshold·오류 종류다. |
| `reports/operational_frozen_baseline_audit.errors.csv` | 오분류 138건만 추린 추적 자료다. 새 학습 데이터나 정답 재생성 파일이 아니다. |

기존 `analyze_operational_errors.py`는 기존 학습/선정 데이터 해시가 같은 경우의 진단 전용으로 유지했다. 이를 느슨하게 바꿔 새 데이터를 기존 학습 데이터인 것처럼 취급하지 않았다. 새 평가 명령은 동일 입력 의미를 가진 **다른 데이터셋으로의 고정 모델 진단**을 별도로 표현한다.

## 실행·재현

AI 폴더에서 실행한다. 기존 서버를 켜거나 트래픽을 새로 생성할 필요는 없다.

```powershell
python .\evaluation\audit_frozen_operational_baseline.py
```

기본 결과가 이미 존재하면 덮어쓰기 없이 종료한다. 재평가는 새 report-prefix를 지정한다.

```powershell
python .\evaluation\audit_frozen_operational_baseline.py --report-prefix reports\operational_frozen_baseline_audit_repeat
python -m unittest discover -s tests -p "test_*.py"
```

기본 데이터셋은 `data/processed/operational_message/v2_campaign_holdout/`, 기존 모델은 `artifacts/operational_message/validation_selection_v1/`이다. 원본 데이터와 모델 묶음이 없는 checkout은 이전 수집·학습·선정 절차부터 재현해야 한다. 보고서 숫자만으로 모델을 복원하지 않는다.

`--expected-validation-rows`의 기본값 513은 다른 데이터셋을 실수로 평가하는 것을 막는 검사다. 다른 버전의 독립 캠페인이라면 데이터셋·ID·예상 행 수를 명시적으로 함께 바꿔야 한다. threshold 옵션은 제공하지 않는다.

## 검증 결과

추가 진단 테스트 16개를 포함해 전체 자동 테스트 **191개가 통과**했다. 실제 데이터도 임시 보고서 경로에서 다시 평가하면서 `Pipeline.fit`, `StandardScaler.fit`, Test CSV·기존 v1 CSV·원본 JSONL·종료 요약 접근을 차단했다. 전체 지표·세션 진단과 두 CSV의 SHA-256이 최초 결과와 일치했다.

수집 전 고정한 모델·threshold·원본 역할 파일·기존 수집 코드 해시와 기존 v1의 비-Test 출력 보존도 확인했다. Test는 이 보존 확인을 위한 해시 검사에도 열지 않았다. 재현 검증은 같은 Validation을 다시 계산한 것이며 새로운 독립 데이터 평가가 아니다.

## 다음 작업과 남은 단계

이 진단 이후 새 Train 1,538건 전용 보강 학습의 **제한된 LR/RF 후보·threshold·선정 규칙 고정과 학습·비교도 완료**했다. 동일한 새 Validation에서 RF leaf=5·threshold=0.3을 선정했으며 이 Validation을 이미 진단했다는 사실을 공개했다. 결과는 `docs/operational_augmentation_experiment.md`에 있다. Test는 최종 비교까지 사용하지 않는다.

관측된 정상 오탐 증가를 보강 실험의 근거로 사용하되, 원인이 모델 종류 하나라고 단정하거나 오분류 행의 정답을 바꾸지 않는다. 일반 정상·묶음 정상·burst 초기 prefix와 malformed를 따로 보고한다.

로드맵의 데이터 보강 단계는 완료돼 **이제 큰 작업 묶음은 5개**다. 다음은 동일 run/session 내부의 시계열 창 구성·LSTM 실험이며, 이후 최종 성능·지연·메모리 비교, 실제 FastAPI 모델, TCP 통합, 포트폴리오 마무리로 넘어간다.
