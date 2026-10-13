# 운영 Validation 설정·threshold 선정

## 이번 단계의 목적

초기 LR/RF 비교 이후, **제한된 학습 설정과 판정 기준을 같은 Validation에서 비교해 임시 baseline을 선정**한다. 설정을 무한히 바꾸거나 Test 성능을 보고 유리한 조합을 고르는 작업이 아니다.

이번 실험은 모델 6개를 학습하고 판정 조합 42개를 비교했다. 기존 운영 LR/RF와 UNSW 산출물, C++ 코어, FastAPI dummy 동작은 변경하지 않았다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_validation_experiment.json` | 후보·threshold·선택 기준과 저장 경로를 학습 전에 정의한다. |
| `training/run_operational_validation_experiment.py` | 설정 검증·protocol 잠금·후보 학습·threshold 비교·제약 검사·선정·저장을 실행한다. |
| `tests/test_operational_validation.py` | Train 전용 학습, Test 미접근, fit 횟수, 동률 처리, 미선정·수렴 실패, 해시와 덮어쓰기 방지를 검사한다. |
| `reports/operational_validation_experiment.json` | 모든 조합의 지표·시나리오별 결과·탈락 이유, 후보별 학습 상태, 선정 결과와 해시를 보관한다. |
| `reports/operational_validation_experiment.md` | 실제 실험 조건·전체 비교 표·선정 근거·한계를 한글로 설명한다. |

기존 `operational_data.py`의 엄격한 데이터 검증과 기존 운영 LR/RF 학습 함수를 재사용한다. 후보 학습 결과는 새 실험 폴더에만 저장하며 기존 모델 파일을 덮어쓰지 않는다.

## 미리 정한 후보와 선택 규칙

- LR: `C=0.1`, `C=1.0`, `C=10.0`; max_iter=1000, tol=0.0001, solver=lbfgs, class_weight=None, seed=42 고정
- RF: `min_samples_leaf=1`, `5`, `10`; 트리 100개, max_depth=None, max_features=sqrt, bootstrap=True, class_weight=None, n_jobs=1, seed=42 고정
- threshold: 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9
- 제약: 정상 FPR 10% 이하, 이상 Recall 50% 이상, LR 학습 수렴 확인
- 목표: 제약을 충족한 조합 중 Recall 최대
- 동률 처리: F1 최대, FPR 최소, 후보 id 오름차순, threshold 오름차순

LR의 C는 규제 강도의 역수에 해당한다. 작은 C는 규제를 더 강하게 적용한다. RF의 최소 leaf 표본 수를 늘리면 아주 적은 메시지만으로 분기 결과를 만드는 것을 제한할 수 있다. 이번 후보는 초기 차이를 살펴볼 작은 집합이며 전체 설정 공간을 탐색한 것이 아니다.

FPR/Recall의 상한·하한은 이번 **실험용 기준**이지 실제 운영 요구사항이 아니다. 현재 Validation의 정상 74건에서 FPR 10% 이하는 FP 7건 이하이고, 이상 64건에서 Recall 50% 이상은 TP 32건 이상이다. 모두 정상으로 판정하면 FPR은 0이지만 Recall도 0이므로 제외한다.

RF에는 LR과 같은 수렴 판정을 적용하지 않는다. 지정한 트리 수 학습·저장이 정상 완료됐는지 확인한다. 수렴 경고가 있는 LR은 지표가 좋아도 선정에서 제외한다.

## 학습 설정과 threshold의 차이

`C`나 `min_samples_leaf`를 바꾸면 다른 모델을 학습해야 한다. 그래서 후보 6개를 각각 Train으로 한 번 학습한다. LR scaler도 각 후보의 Train에만 fit하고, RF에는 scaler를 사용하지 않는다.

threshold는 이미 계산된 확률을 어느 클래스라고 판정할지 정하는 기준이다. 같은 메시지의 이상 확률이 0.65이면 threshold 0.5에서는 이상, 0.7에서는 정상으로 판정한다. threshold를 바꾼다고 확률이나 학습한 가중치·트리가 바뀌지 않는다.

따라서 모델 6개 × threshold 7개는 **학습 6회 + 판정 비교 42개**다. 여기서 학습 6회는 `Pipeline.fit()` 호출 횟수이며, LR 내부 최적화 반복 횟수나 RF 트리 수와는 또 다른 개념이다.

## 코드 흐름과 저장 구조

1. 설정 파일을 읽고 경로·후보 중복·threshold 범위·선정 규칙을 검증한다.
2. 완료된 고정 데이터셋의 Train/Validation만 로드한다. 입력 순서, 파일 해시, 메시지 정렬과 run/session 비중복을 확인한다.
3. 학습 전에 `protocol_lock.json`에 실제 실행 설정·시각·입력 해시와 protocol SHA-256을 저장한다. 외부 제3자 사전등록을 대신하는 것은 아니며 실행 내부의 근거다.
4. 후보마다 기존 LR/RF 학습 함수를 호출해 Train으로만 학습하고 새 후보 폴더에 저장한다.
5. 모델을 재로딩해 기본 0.5 지표가 학습 기록과 맞는지 확인한 후 Validation 확률을 계산한다. 이 확률에 7개 threshold를 적용한다.
6. 각 조합의 지표·시나리오별 FP/FN·제약 미충족 사유를 기록한다. 선정 시 지표와 수렴 상태를 다시 확인한다.
7. 입력·잠금 파일이 실행 중 변경되지 않았는지 확인하고 사전 규칙으로 선정한다. 동률이면 항상 같은 결과가 나오도록 순서를 고정한다.
8. `selection.json`, 선정 threshold의 메시지별 예측과 전체 보고서를 저장하고 마지막에 완료 manifest를 기록한다.

```text
artifacts/operational_message/validation_selection_v1/
  protocol_lock.json
  candidates/<candidate_id>/
    pipeline.joblib
    feature_schema.json
    label_map.json
    decision_policy.json
    validation_predictions.csv
    training_manifest.json
  candidate_reports/<candidate_id>.json / .md
  selection.json
  selected_validation_predictions.csv
  experiment_manifest.json
reports/operational_validation_experiment.json
reports/operational_validation_experiment.md
```

모델 경로는 `selection.json`이 있는 폴더 기준의 상대 경로다. LR scaler는 `pipeline.joblib` 내부에 포함된다. 선정 threshold도 함께 읽어야 한다. 후보 폴더의 `decision_policy.json`은 기본 0.5 비교용이며, 선정 후의 판정 기준은 `selection.json`의 threshold다. 후보 기록을 수정해 서로 다른 실험을 하나처럼 만들지 않는다.

제약을 충족한 조합이 없으면 `status=no_feasible_candidate`를 기록하고 모델 경로·threshold를 null로 남긴다. 이때 선정 예측 CSV는 생성하지 않는다. 미선정도 비교가 끝난 실험 결과이므로 완료 manifest는 생성되지만 배포 준비 상태는 false다.

## 실제 선정 결과

42개 조합 중 제약 충족은 6개였고 모두 LR이었다. 선정 결과는 **LR C=1.0, threshold=0.7**이다. C=1과 C=10의 일부 조합은 Recall·F1·FPR이 같아 후보 id 순서로 C=1이 선정됐다. 같은 C=1의 0.7과 0.8도 지표가 같아 threshold 오름차순으로 0.7을 택했다.

| Validation 결과 | 초기 LR, threshold 0.5 | 선정 LR, threshold 0.7 |
| --- | ---: | ---: |
| 정상 오탐 FP | 22 | 3 |
| 이상 누락 FN | 11 | 24 |
| FPR | 29.73% | 4.05% |
| Recall | 82.81% | 62.50% |
| Precision | 70.67% | 93.02% |
| F1 | 76.26% | 74.77% |

선정 모델은 초기 C=1 LR과 모델 파일 해시도 동일했다. 이번 최종 선정의 차이는 **같은 모델의 threshold 변경**이며 LR 자체의 알고리즘을 개선한 것은 아니다. FPR 제약을 충족했지만 Recall과 F1은 낮아졌다. 따라서 전체 성능 개선이라고 표현하지 않는다.

선정 confusion matrix는 TN=71, FP=3, FN=24, TP=40이다. 통제된 burst는 56건 중 40건을 탐지했지만, `malformed_chat_payload` 4건과 `malformed_length_chat` 4건은 모두 놓쳤다. 정상 묶음 전송의 시나리오별 FPR도 3/23=13.04%로 전체 FPR 4.05%보다 높다. 현재 제약은 전체 지표에 적용했고 시나리오별 하한·상한은 별도 제약으로 사용하지 않았다.

이 결과는 현재 규칙에서 선정된 **임시 운영 baseline**이다. 일부 시나리오 Recall이 0인 모델을 최종 배포 모델로 확정한 것이 아니다. 이를 다음 오분류·관측 한계 분석의 근거로 남긴다.

## 실행과 재현

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\training\run_operational_validation_experiment.py
python -m unittest discover -s tests -p "test_*.py"
```

고정 운영 데이터셋이 필요하다. 새 checkout에서는 먼저 수집·데이터셋 구성 단계를 수행한다. 이미 기본 경로에 실험 결과가 있으면 덮어쓰지 않고 새로운 경로를 지정한다.

```powershell
python .\training\run_operational_validation_experiment.py --artifact-dir .\artifacts\operational_message\validation_recheck --report-prefix .\reports\operational_validation_recheck
```

후보나 기준을 변경하려면 별도 설정 파일을 만들고 `--config`와 새 저장 경로를 지정한다. 추가 Validation 탐색을 독립 검증으로 간주하지 않는다. 기본 CLI는 종료 코드 0을 실험 완료에 사용하므로 성공 종료만 보고 선정됐다고 판단하면 안 된다. `selection.json`의 상태를 확인해야 한다. 설정·입력·저장 실패는 종료 코드 2다. 완료 manifest가 없는 부분 출력은 완료 실험으로 사용하지 않는다.

모델·후보 보고서·선정 예측 등 artifacts 내용은 기존 ignore 규칙으로 Git에서 제외한다. 코드·설정·설명 문서와 최상위 실험 보고서를 추적한다. 신뢰할 수 없는 외부 joblib은 로드하지 않는다.

## 해석 범위와 다음 작업

Test CSV는 해시 검사에도 열지 않았고 Validation을 Train에 합쳐 재학습하지 않았다. 선정에 사용한 Validation 점수는 독립적인 최종 성능 추정치가 아니다. 작은 단일 캠페인과 같은 생성기의 편향, 메시지 간 상관관계, 본문 검증 이전 snapshot의 한계가 남는다.

선정 조합의 오분류·관측 한계 분석과 독립 보강 설계는 완료했다. 결과는 `docs/operational_error_analysis.md`, 설계는 `docs/operational_data_augmentation.md`에 정리했다. 다음 작업은 생성기 제어·캠페인 단위 분리 구현과 새 캠페인 수집·검증이다. 현재 Test를 보고 결정하거나 오류 수로 정답을 만들지 않는다.

로드맵 1단계인 운영 LR/RF baseline 학습·Validation 선정은 완료했다. 최종 마무리까지 남은 큰 작업 묶음은 **6단계**이며 FastAPI는 여전히 dummy 상태다.
