# 운영 RandomForest 학습과 LR 비교

## 이번에 한 일

지난 단계의 Logistic Regression과 **같은 원본 입력 7개·같은 Train/Validation·같은 정답**으로 RandomForest 하나를 직접 학습했다. 모델을 바꿔봤다는 사실만 남기는 것이 아니라, 어떤 오분류가 줄거나 늘었는지 같은 메시지에서 비교한다.

이번에는 설정 후보 탐색이나 threshold 조정을 하지 않았다. Test 평가, 최종 모델 선정과 FastAPI 모델 연결도 하지 않았다. 기존 LR과 UNSW 산출물은 그대로 보존한다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_random_forest.json` | 원본 데이터셋·저장 경로·트리 수·깊이·최소 leaf 크기·seed·threshold를 사전에 고정한다. |
| `training/train_operational_random_forest.py` | 검증된 Train으로 RF를 학습하고 Validation 지표, 시나리오별 오분류, 학습 트리 정보와 중요도를 저장한다. |
| `evaluation/compare_operational_baselines.py` | 두 모델의 입력 해시·schema·학습 기록·산출물을 검증하고 동일 Validation을 다시 예측해 비교한다. |
| `tests/test_operational_random_forest.py` | Train 전용 학습, Test 미접근, seed 재현, 저장 검증, 비교 중 재학습 금지와 오분류 변화 계산을 검사한다. |
| `reports/operational_random_forest.json` / `.md` | 실제 RF 학습 조건·지표·중요도·한계를 보관한다. |
| `reports/operational_baseline_comparison.json` / `.md` | LR/RF 지표 차이, 시나리오별 Recall/FPR, 수정·신규 오분류와 해당 row_id를 보관한다. |

기존 `training/operational_data.py`의 manifest·해시·행 정렬·run/session 검사와 LR의 지표 계산 함수를 재사용한다. 기존 LR 학습 코드 자체를 수정하거나 재학습하지 않는다.

## 처음 배우는 핵심 개념

### Decision Tree와 RandomForest

Decision Tree는 입력 값에 대한 분기를 학습하는 모델이다. 예를 들어 연결 시간이 특정 기준 이하인지, 수신 바이트가 특정 크기 이상인지 조건을 나누며 정답을 구분한다. 이번 코드에서 사람이 그 분기 기준을 직접 작성하지 않는다. Train의 입력과 정답을 이용해 sklearn이 분기를 학습한다.

RandomForest는 여러 트리를 학습하고 각 트리의 클래스 확률을 평균해 결과를 만든다. `bootstrap=True`는 Train 메시지에서 중복을 허용해 표본을 뽑는 방식이고, `max_features="sqrt"`는 각 분기에서 일부 입력 후보를 고려하는 설정이다. Validation에서 표본을 뽑는 것이 아니다.

현재 bootstrap의 단위는 메시지다. 이미 Train/Validation은 run 단위로 분리했지만, RF 내부가 세션 전체를 독립 단위로 다시 샘플링하는 것은 아니다. 같은 세션 메시지의 상관관계는 여전히 한계로 기록한다.

### 트리 100개와 학습 100회는 다르다

`n_estimators=100`은 트리 100개를 구성한다는 뜻이다. LR의 `max_iter=1000`, 실제 반복 12회와 같은 최적화 반복 횟수가 아니다. 트리 수를 늘리는 것만으로 정답률을 최대화하거나 일반화를 보장하지 않는다.

`max_depth=None`은 별도 깊이 상한을 두지 않는다는 뜻이다. 표본 수·분기 조건 등에 따라 각 트리의 깊이는 유한하게 결정된다. `min_samples_leaf=1`은 leaf에 최소 한 표본을 허용한다. 현재 설정은 초기 기준이며 이후 더 얕은 트리나 더 큰 leaf를 제한된 후보로 비교할 수 있다.

### RF에 scaler가 없는 이유

LR은 Train 평균·분산으로 표준화한다. RF는 값의 순서와 분기 기준을 이용하므로 이번 숫자형 입력에는 scaler를 추가하지 않았다. 전처리 계약을 `identity_no_scaler`로 명시하고 Pipeline에는 RF만 저장한다.

공정한 비교는 두 모델의 전처리 코드가 완전히 같아야 한다는 뜻이 아니다. 같은 원본 정보·메시지·정답·분할을 사용하고 각 모델의 전처리를 명시해야 한다. RF에 추가 metadata나 종료 후 오류 정보를 제공하지 않았다.

### Feature 중요도

보고서에는 학습 트리의 불순도 감소 기반 중요도를 저장한다. 값이 큰 입력은 학습 분기를 구성하는 데 많이 사용됐다는 의미다. 입력 값 종류 수나 중복 정보의 영향을 받으므로 실제 이상 원인 또는 독립적인 중요도로 단정하지 않는다.

현재 `packet_size`와 `payload_size`는 8바이트 차이인 중복 정보이고, `error_count_total`은 Train에서 전부 0이다. 본문 검증 이후의 오류를 끌어와 중요도를 높이거나 정답으로 사용하지 않았다.

## 학습과 비교 흐름

1. 설정과 완료된 운영 데이터셋을 검증한다. Train 126건과 Validation 138건만 로드한다.
2. Train 원본 입력 7개와 정답으로 RF를 한 번 학습한다. 트리 100개·seed 42·작업 스레드 1개·threshold 0.5를 사용한다.
3. Train 지표는 학습 상태 확인용으로 기록하고 Validation 지표·시나리오별 FP/FN·메시지별 확률을 저장한다.
4. RF 모델을 저장한 뒤 재로딩해 예측이 같은지 확인한다. 산출물과 보고서가 저장된 뒤 완료 manifest를 남긴다.
5. 별도 비교 스크립트가 LR/RF의 입력 파일 해시를 현재 데이터셋과 대조한다. 두 모델 모두 Train 전용 학습이고 Test 미사용인지 검사한다.
6. schema·정답 의미·고정 threshold·산출물 해시를 확인하고 저장 모델의 Validation 지표를 다시 계산한다. 메시지별 예측 CSV의 row_id·정답·확률·예측도 대조한다.
7. 같은 메시지에서 LR 오분류가 수정되었는지, RF에서 새 오분류가 생겼는지 계산한다. 비교 중에는 `fit()`을 호출하지 않는다.

Test CSV는 학습·비교의 해시 검사에도 열지 않는다. 데이터셋 manifest에 이미 기록된 분할 설명을 읽는 것과 실제 Test 메시지나 성능을 확인하는 것은 다르다.

## 실제 결과

| Validation 지표 | LR | RF |
| --- | ---: | ---: |
| Accuracy | 76.09% | 68.84% |
| Precision | 70.67% | 71.43% |
| Recall | 82.81% | 54.69% |
| F1 | 76.26% | 61.95% |
| 정상 오탐 FP | 22 | 14 |
| 이상 누락 FN | 11 | 29 |

RF는 정상 오탐을 8건 줄였지만 이상 누락이 18건 늘었다. 같은 메시지 기준으로는 기존 FP 8건과 FN 3건을 수정했고, 신규 FN 21건이 발생했다. 합계 FN 변화는 `기존 11 - 수정 3 + 신규 21 = 29`다. 새 FP는 없었다.

정상 묶음 전송의 FP는 20→13건으로 줄었다. 반면 통제된 burst의 FN은 8→28건으로 늘었다. malformed 본문 시나리오 중 일부는 개선됐지만, 전체 F1과 Recall은 낮아졌다. 따라서 **현재 고정 조건에서 RF가 전체적으로 개선됐다고 결론 내리지 않는다.**

RF의 Train Accuracy/F1은 100%였지만 Validation Accuracy는 68.84%다. 이런 차이는 과적합 또는 run 사이 분포 차이를 검토할 근거다. 현재 결과만으로 원인을 확정하거나 모든 RandomForest가 LR보다 나쁘다고 일반화하지 않는다. 설정 변경으로 반드시 해결될 것이라고 가정하지도 않는다.

## 실행과 저장

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\training\train_operational_random_forest.py
python .\evaluation\compare_operational_baselines.py
```

비교에는 이전 단계의 LR 산출물이 필요하다. 새 checkout에서는 원본 수집·데이터셋 구성·LR 학습을 먼저 수행한다. 임의 외부 joblib 파일은 로드하지 않는다. 해시 일치만으로 파일의 안전성을 보증할 수 없다.

```text
artifacts/operational_message/random_forest_v1/
  pipeline.joblib
  feature_schema.json
  label_map.json
  decision_policy.json
  validation_predictions.csv
  training_manifest.json
reports/operational_random_forest.json
reports/operational_random_forest.md
reports/operational_baseline_comparison.json
reports/operational_baseline_comparison.md
```

모델·예측 CSV는 기존 artifacts ignore 규칙에 따라 Git에서 제외한다. 재현용 코드·설정·설명·보고서는 Git에 포함한다. 학습 시간과 모델 파일 크기는 참고 기록이며 이번 단계는 지연시간·메모리 benchmark가 아니다.

기존 결과를 덮어쓰지 않는다. 재현 확인은 새로운 경로를 사용하며, 비교에서 `--rf-dir`도 새 모델 경로로 지정한다. LR 경로는 그대로 사용할 수 있다.

```powershell
python .\training\train_operational_random_forest.py --artifact-dir .\artifacts\operational_message\rf_recheck --report-prefix .\reports\operational_rf_recheck
python .\evaluation\compare_operational_baselines.py --rf-dir .\artifacts\operational_message\rf_recheck --report-prefix .\reports\operational_comparison_recheck
python -m unittest discover -s tests -p "test_*.py"
```

각 명령은 성공 시 종료 코드 0, 검증·저장 실패 시 2를 반환한다. 부분 출력에는 완료 manifest가 없을 수 있으므로 학습 완료 모델로 사용하지 않는다.

## 다음 작업

제한된 설정·threshold와 선택 규칙을 사전에 고정한 Validation 선정까지 완료했다. 결과와 설명은 `docs/operational_validation_experiment.md`에 정리했다. RF는 모든 선정 제약을 동시에 만족하지 못했고 LR C=1·threshold=0.7이 임시 baseline으로 선정됐다. Test와 FastAPI 모델 연결은 뒤 단계에 남긴다.

전체 로드맵 1단계의 LR/RF 학습·Validation 선정과 2단계의 오분류 분석·보강 설계는 완료했다. 다음 작업은 생성기 제어·캠페인 분리 구현과 새 수집·검증이며, 설계는 `docs/operational_data_augmentation.md`에 정리했다. 실제 데이터 보강이 남아 2단계는 진행 중이고 큰 작업 묶음은 6개가 남는다.
