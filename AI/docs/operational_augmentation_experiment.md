# 새 Train 전용 보강 LR/RF 학습·선정·비교

## 이번 작업의 목적

기존 LR C=1·threshold 0.7은 새 Validation에서 정상 FPR 26.36%, FP 92건·FN 46건이었다. 이 한계를 확인한 뒤 실행 조건을 다양화한 새 Train 1,538건으로 LR/RF를 다시 학습하고, 동일 Validation 513건에서 비교했다.

알고리즘 6종을 가져온 것이 아니라 **LR 3개 설정 + RF 3개 설정**을 직접 학습했다. 기존 모델은 유지했고 새로운 산출물 경로를 사용했다. C++ 서버·feature 계약·FastAPI는 바꾸지 않았으며 API는 계속 dummy다.

## 학습 전에 고정한 후보·정책

| 모델 | 후보 설정 | 공통 조건 |
| --- | --- | --- |
| Logistic Regression | C=0.1 / 1 / 10 | StandardScaler, max_iter=1000, tol=0.0001, lbfgs, seed=42 |
| RandomForest | min_samples_leaf=1 / 5 / 10 | 트리 100개, max_depth=None, max_features=sqrt, bootstrap=True, n_jobs=1, seed=42 |

LR의 C는 규제 설정이고 RF의 min_samples_leaf는 잎 노드의 최소 학습 표본 수다. RF leaf=5는 트리가 5개라는 뜻이 아니다. 모든 RF 후보는 트리 100개다.

threshold 후보는 `[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]`다. 모델당 Train 학습은 한 번만 수행하고, 그 모델의 같은 Validation 확률에 threshold 7개를 적용했다. 실제 실행에서 **fit 6회 각각 1,538건**, 평가 조합 **42개**를 확인했다.

선정 규칙은 이전 실험과 같은 다음 조건을 유지했다.

1. LR은 수렴을 확인해야 한다. RF에는 LR 같은 반복 수렴 판정을 적용하지 않는다.
2. 전체 정상 메시지 FPR은 10% 이하여야 한다.
3. 통제된 이상 메시지 Recall은 50% 이상이어야 한다.
4. 제약 충족 조합 중 Recall이 가장 큰 조합을 선택한다.
5. 동률이면 F1 최대·FPR 최소·후보 id·threshold 오름차순을 따른다.

기준 미충족이면 미선정으로 완료하고 규칙·후보를 자동 변경하지 않는다. 이번 규칙은 **이번 학습 전에** 저장했다. 새 Validation은 이미 기존 모델 진단에 사용했으므로, Validation을 보기 전 사전 등록한 독립 실험이라고 주장하지 않는다. 이번에도 Test CSV는 해시 검사에조차 읽지 않았다.

## 데이터와 전처리 범위

- Train: 캠페인 3개·run 36개·세션 144개·메시지 1,538건. 정상 1,049건·통제된 이상 489건.
- Validation: 캠페인 1개·run 12개·세션 48개·메시지 513건. 정상 349건·통제된 이상 164건.
- LR scaler의 평균·분산·scale과 가중치는 새 Train만으로 학습했다.
- RF는 scaler 없이 같은 원본 feature 7개로 학습했다.
- Validation은 설정·threshold 선택과 비교에 사용했으며 Train에 합치지 않았다.
- 기존 v1 CSV, Test CSV, 원본 JSONL·종료 summary는 읽지 않았다.

입력은 같은 `server_application_message_v2`의 7개다. label·scenario·run/session ID·timestamp·종료 요약을 모델 입력에 추가하지 않았다. 정답은 기존의 독립 시나리오 정의를 유지한다.

## 두 잠금과 완료 상태

```text
설정 파일 → 기존 기준 모델·새 입력·기존 진단 예측 검증
          → augmentation_protocol_lock.json 저장
          → validation_selection/protocol_lock.json 저장
          → 후보 6개를 새 Train으로 각각 fit
          → threshold 42개 조합·선정/미선정 기록
          → 저장된 후보 재로딩·재예측·동일 행 비교
          → 입력·기준 모델·두 정책 보존 검사
          → augmentation_manifest.json 전체 완료 기록
```

첫 lock에는 후보 설정뿐 아니라 기준 모델·기존 진단·코드·입력 해시, 비교 조건과 Validation 이전 사용 이력이 들어 있다. 두 번째 lock은 재사용한 후보 선정 모듈의 학습 전 정책이다. 학습 뒤 고정 파일이 바뀌면 전체 실험 완료를 기록하지 않는다.

선정 모듈의 experiment manifest 완료와 전체 보강·비교 완료를 구분한다. 마지막 `augmentation_manifest.json`이 있어야 비교까지 성공한 실행이다. 실패한 부분 출력은 삭제해 숨기거나 이어서 후보를 바꿔 재사용하지 않고 새 경로로 별도 실행한다.

## 선정 결과

42개 조합 중 **18개가 제약을 충족**했다. 전체 선정은 **RF min_samples_leaf=5·threshold=0.3**이다. 세 LR 후보는 최대 1,000회 중 각각 11/20/23회에서 수렴했고, RF 후보마다 트리 100개를 학습했다.

| 선정 RF 지표 | 결과 |
| --- | ---: |
| Accuracy | 92.40% |
| Precision | 83.78% |
| Recall | 94.51% |
| F1 | 88.83% |
| ROC-AUC | 0.9815 |
| 정상 FPR | 8.60% |
| TN / FP / FN / TP | 319 / 30 / 9 / 155 |

규칙이 Recall 최대를 우선했기 때문에 leaf=1·threshold=0.3의 더 낮은 FPR 6.88%보다, leaf=5의 더 높은 Recall 94.51%를 선택했다. 이번 제약 내 선택이지 모든 비용·환경에서 가장 좋은 모델이라는 뜻은 아니다.

## 비교를 구분한 이유

| 비교 역할 | 후보 | threshold | FP | FN | FPR | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 기존 고정 모델 | 기존 LR C=1 | 0.7 | 92 | 46 | 26.36% | 71.95% | 63.10% |
| 같은 LR 설정 재학습 | 보강 LR C=1 | 0.7 | 0 | 134 | 0% | 18.29% | 30.93% |
| RF 고정 정책 비교 | 보강 RF leaf=1 | 0.7 | 5 | 59 | 1.43% | 64.02% | 76.64% |
| LR 제약 충족 최적 조합 | 보강 LR C=0.1 | 0.5 | 31 | 57 | 8.88% | 65.24% | 70.86% |
| RF 제약 충족 최적 조합 | 보강 RF leaf=5 | 0.3 | 30 | 9 | 8.60% | 94.51% | 88.83% |

같은 LR C=1·threshold 0.7 재학습은 오탐을 없앴지만 누락 134건으로 악화됐다. 데이터를 더 모으면 이전 threshold까지 그대로 좋아진다는 가정은 틀렸다. LR도 새 Validation에서 정책을 검토해야 했다.

선정 RF와 기존 LR의 차이는 데이터 보강·모델 종류·설정·threshold 선택을 함께 포함한다. 이 전체 차이를 데이터 수 증가만의 효과나 RF 알고리즘만의 효과로 단정하지 않는다. 동일 0.7 비교도 두 모델의 확률 보정 수준이 같다는 뜻은 아니다.

## 시나리오와 오분류 변화

| 시나리오 | 메시지 | 선정 RF FP | 선정 RF FN | 해당 지표 |
| --- | ---: | ---: | ---: | --- |
| normal_chat | 110 | 7 | 0 | FPR 6.36% |
| normal_batched_chat | 130 | 21 | 0 | FPR 16.15% |
| normal_fragmented_chat | 109 | 2 | 0 | FPR 1.83% |
| controlled_burst_chat | 148 | 0 | 9 | Recall 93.92% |
| malformed_chat_payload | 8 | 0 | 0 | Recall 100% |
| malformed_length_chat | 8 | 0 | 0 | Recall 100% |

전체 정상 FPR 8.60%는 정상 349건을 분모로 한 값이다. 정상 묶음의 FPR은 여전히 16.15%여서 시나리오별 운영 한계가 남는다. 규칙을 사후에 시나리오별 10%로 바꾸거나 이 시나리오를 삭제하지 않았다.

같은 행의 이전 오분류를 추적하면 선정 RF는 기존 FP 81건·FN 38건을 수정했고, 신규 FP 19건·FN 1건을 만들었다. 따라서 `92-81+19=30`, `46-38+1=9`다. 단순히 오분류 총량만 줄었다고 모든 사례가 좋아졌다고 주장하지 않는다.

burst 8개 세션은 모두 첫 메시지부터 한 번 이상 양성이었다. 하지만 일부 세션의 뒤쪽 메시지를 놓쳐 FN 9건이 남았으며, 첫 양성이 있었다고 세션 전체 메시지를 정답 처리하지 않았다. prefix별 실제 분모·첫 양성 위치·경과시간은 JSON에 보관했다.

malformed 16건을 이번 Validation에서 잡았지만 **본문 필드의 유효성을 학습했다고 해석하면 안 된다**. 내부 문자열 길이 필드는 여전히 입력에 없고, 크기·시간·수신/송신 누적값 등 이 생성기의 연관 패턴으로 구분했을 수 있다. 같은 관측값에서 본문만 달라지는 한계는 남는다. 정상 프로토콜 검증 경로와 AI 메시지 판단을 구분한다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_augmentation_experiment.json` | 새 데이터셋·행 수·기준 모델·이전 진단·6개 후보·7개 threshold·선정 규칙과 별도 출력 경로다. |
| `training/run_operational_augmentation_experiment.py` | 기준 모델/진단 검증, 전체 정책 잠금, 새 Train 학습, 동일 Validation 비교와 전체 완료 표시를 묶는다. |
| `training/run_operational_validation_experiment.py` | 기존 후보 학습·42개 조합·사전 선택 규칙을 재사용한다. 캠페인 수 설명만 실제 metadata에 맞춰 수정했다. |
| `training/train_operational_logistic_regression.py` | Train 전용 scaler/LR 학습과 저장·재로딩 검증을 재사용한다. 기존 모델을 덮어쓰지 않는다. |
| `training/train_operational_random_forest.py` | Train 전용 RF 학습과 identity schema·직접 학습한 트리 기록을 재사용한다. |
| `evaluation/compare_operational_augmentation.py` | 저장 후보·42개 지표를 재검증하고 고정 정책/선정 정책을 분리해 같은 행의 FP/FN 변화와 burst prefix를 계산한다. |
| `tests/test_operational_augmentation_experiment.py` | 학습 전 두 lock, Train 전용 fit, 기존 모델·진단 보존, Test 접근 금지, 미선정·실패·완료 상태와 비교 CSV를 검사한다. |
| `reports/operational_augmentation_validation.json/.md` | 6개 후보의 직접 학습·기본 0.5 평가·42개 조합·수렴·선정 근거다. |
| `reports/operational_augmentation_comparison.json/.md` | 같은 513건의 비교 역할별 지표, 시나리오, 수정/신규 오분류, 모든 변경 행·prefix·근거 해시다. |

모델은 `artifacts/operational_message/augmentation_v1/validation_selection/candidates/<candidate_id>/`에 보관한다. 선정 정책은 `validation_selection/selection.json`이다. 후보의 기본 `decision_policy.json`은 0.5이고 이번 선정은 0.3이므로 둘을 혼합하지 않는다.

전체 비교 CSV는 `artifacts/operational_message/augmentation_v1/comparison_predictions.csv`이며 같은 행 위치에 같은 메시지의 각 비교 역할별 확률·판단·threshold를 보관한다. artifact 디렉터리는 기존 Git 제외 정책을 유지한다. 공개 비교 JSON에도 변경 행과 추적 ID를 저장한다.

## 실행과 재현

AI 폴더에서 다음 명령으로 실행한다. 수집 서버를 켜거나 새 트래픽을 보낼 필요가 없다.

```powershell
python .\training\run_operational_augmentation_experiment.py
```

기본 결과가 이미 존재하면 덮어쓰기 없이 종료한다. 재실행은 산출물 root와 두 보고서 경로를 모두 새로 지정한다.

```powershell
python .\training\run_operational_augmentation_experiment.py --run-dir artifacts\operational_message\augmentation_repeat --selection-report-prefix reports\operational_augmentation_validation_repeat --comparison-report-prefix reports\operational_augmentation_comparison_repeat
python -m unittest discover -s tests -p "test_*.py"
```

원본·기준 모델·기존 진단이 없는 checkout에서는 수집·학습·진단 절차부터 재현해야 한다. 실험 이름만 바꾸거나 보고서 숫자만으로 모델을 복원하지 않는다.

## 검증과 마무리 상태

전체 자동 테스트 **203개가 통과**했다. 실제 실행에서도 fit에 전달한 X/y가 새 Train과 같고 6회 모두 1,538건인지, 두 lock이 첫 fit 전에 있는지 확인했다. Test CSV·기존 v1 CSV·원본 JSONL·종료 summary 접근을 차단한 상태로 실행했다.

동일 설정·seed를 임시 경로에서 재학습한 재현 검증은 선정 조합, 모든 비교 역할의 지표·prefix 진단, 비교 CSV SHA-256이 일치했다. 이는 추가 후보 탐색이나 독립 데이터 평가가 아니라 같은 실험의 재현 확인이다. 전체 산출물 inventory와 고정 입력·기준 모델·이전 선정 산출물·보고서 해시도 확인했다.

**독립 데이터 보강 학습·비교 단계는 완료했다. 이제 큰 작업 묶음은 5개 남는다.** 다음은 분리된 각 split의 동일 run/session 내부에서 시계열 창을 구성하고 LSTM 개선 실험을 설계하는 것이다. 짧은 malformed 세션은 메시지 1개뿐이므로 padding·mask·판단 시점·coverage를 명시하고 없는 과거/본문 정보를 만들어 넣지 않는다.

이후 Validation에서 비교할 모델·정책을 고정한 다음 최종 Test, 지연시간·메모리, 실모델 FastAPI, TCP 통합, 포트폴리오 문서로 진행한다. LSTM이 RF보다 나쁘면 그 결과와 한계도 남긴다. 이번 Validation 선정 RF는 최종 배포 모델 확정이 아니다.
