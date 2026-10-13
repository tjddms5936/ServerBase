# 운영 Logistic Regression 학습

## 이번 단계의 목적

C++ 서버에서 수집한 숫자형 입력 7개로 **운영용 첫 기준 모델 하나**를 직접 학습한다. 기존 UNSW-NB15 모델을 가져오는 작업이 아니다. 이번 단계는 RandomForest 학습이나 모델 선정까지 포함하지 않는다.

모델은 `normal=0`, `controlled_anomaly=1`을 구분한다. 정답은 사전에 정의한 통제된 트래픽 시나리오의 의도이며 실제 침입 정답이 아니다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_logistic_regression.json` | 고정 데이터셋·저장 경로·학습 조건을 정의한다. |
| `training/operational_data.py` | 완료 manifest, 파일 해시, feature 계약, X/y/metadata 정렬, run/session 비중복을 검사해 train과 validation만 로드한다. |
| `training/train_operational_logistic_regression.py` | train 전용 Pipeline 학습, validation 예측, 수렴·지표·가중치 보고서 및 모델 저장을 수행한다. |
| `tests/test_operational_training.py` | 전처리 누출, test 접근 금지, 파일 변조, 행 정렬, 모델 재로딩과 덮어쓰기 방지를 검사한다. |
| `reports/operational_logistic_regression.json` | 실제 조건·학습 결과·가중치·검증 지표·산출물 해시를 보관한다. |
| `reports/operational_logistic_regression.md` | 실제 실험의 결과와 한계를 한글로 설명한다. |

## 코드 흐름

1. `main()`이 설정을 읽는다. 설정의 상대 경로는 설정 파일이 있는 폴더 기준으로 해석한다.
2. `load_operational_data()`가 데이터셋이 완성됐고 현재 `server_application_message_v2` 의미 계약과 일치하는지 확인한다.
3. 허용된 train/validation CSV와 schema·label map·split manifest의 SHA-256을 비교한다. 실제로 읽은 바이트를 그대로 파싱하므로 검사와 파싱 사이에 파일을 다시 열지 않는다. `X_test.csv`, `y_test.csv`, `metadata_test.csv`는 해시 검사에도 열지 않는다.
4. X의 입력 7개, y의 정답, metadata의 행 수를 비교한다. 입력 행과 metadata의 feature 해시를 대조하고 두 분할의 run/session/row 식별자 교집합도 다시 검사한다.
5. `Pipeline(StandardScaler, LogisticRegression)`에 **train의 X와 y만** 넘겨 `fit()`을 한 번 호출한다.
6. 저장된 scaler와 가중치를 그대로 사용해 validation 확률을 계산한다. 이상 확률이 0.5 이상이면 이상으로 판정한다. train 지표는 학습 상태 확인용으로 함께 기록한다.
7. 지표와 시나리오별 FP/FN, 학습 반복 횟수, 가중치, scaler 통계를 저장한다. 모델을 다시 로드해 예측이 같은지 확인한 뒤 완료 manifest를 기록한다.

metadata의 run/session ID, 시나리오, 정답, 시각과 종료 요약은 모델 입력에 합치지 않는다. 누락을 0으로 채우거나 CSV를 다시 무작위 분할하지 않는다.

## 처음 배우는 개념

### StandardScaler와 fit

바이트 수와 시간은 숫자 크기가 다르다. `StandardScaler.fit()`은 train 각 열의 평균과 분산을 계산하고, `transform()`은 저장한 통계로 대략 `(입력 - 평균) / 표준편차`를 계산한다.

validation에서 다시 평균을 구하지 않는다. 예를 들어 train의 시간 평균이 10ms라면 validation에서도 10ms를 기준으로 변환한다. validation의 평균을 학습에 섞으면 학습 과정이 검증 데이터의 정보를 미리 사용하게 된다.

train에서 상수인 `error_count_total`은 분산이 0이다. sklearn은 이 열의 나눗셈 기준을 1로 처리한다. 입력을 삭제하거나 오류 정답으로 사용하는 것은 아니다.

### Pipeline과 가중치

Pipeline은 **전처리 후 모델 실행**을 한 객체로 묶는다. `pipeline.fit(X_train, y_train)`은 scaler의 통계를 학습한 후 변환된 train으로 LR을 학습한다. `pipeline.predict_proba(X_validation)`은 학습을 다시 하지 않고 같은 scaler로 변환한 뒤 확률을 계산한다.

LR은 표준화된 입력 7개의 가중치와 기본값인 `intercept`를 학습한다. 계산한 점수를 sigmoid로 바꿔 이상 확률을 만든다. JSON의 가중치는 원래 바이트·ms가 아니라 **표준화된 입력 기준**이다. 서로 중복되는 입력도 있어 가중치 크기를 독립적인 중요도나 원인으로 단정하면 안 된다.

### 반복 횟수와 수렴

`max_iter=1000`은 무조건 1,000번 학습한다는 뜻이 아니라 최적화 반복의 상한이다. `solver=lbfgs`가 확률 오차와 L2 규제를 고려해 가중치를 조정한다. `C=1.0`은 규제 강도의 역수에 해당하는 고정 설정이고 `tol=0.0001`은 solver의 중단 기준에 쓰인다.

실제 반복 횟수는 모델의 `n_iter_`에서 읽는다. 수렴 경고가 있으면 보고서에 남기며 명령 종료 코드 3을 반환한다. 경고가 없는 수렴은 학습 계산이 중단 조건을 충족했다는 뜻이지 정답률 100%나 일반화 성능 보장이 아니다. 보고서의 log loss는 규제항을 제외한 평균 확률 오차라 전체 최적화 목적값과는 다르다.

### threshold와 validation

threshold 0.5는 이번 baseline에서 사전에 고정했다. validation을 보며 값을 조정하지 않았다. 이후 제한된 LR/RF 후보와 threshold를 비교하는 별도 단계에서 선정한다. 최종 모델이나 배포 모델로 선택했다는 의미가 아니다.

Accuracy, Precision, Recall, F1, ROC-AUC, Average Precision, log loss, FPR/FNR와 confusion matrix를 저장한다. Average Precision은 precision-recall 곡선을 요약하는 방식 중 하나이며 사다리꼴 방식 PR-AUC와 같은 이름으로 취급하지 않는다. 이상을 양성 클래스로 두므로 FP는 정상 오탐, FN은 이상 누락이다.

## 실행과 산출물

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\training\train_operational_logistic_regression.py
```

```text
artifacts/operational_message/logistic_regression_v1/
  pipeline.joblib              train으로 학습한 scaler와 LR
  feature_schema.json          의미 계약·입력 순서·단위·타입·전처리 상태
  label_map.json               정답 의미
  decision_policy.json         고정 threshold와 비교 연산
  validation_predictions.csv   메시지별 정답·확률·예측·오분류와 추적 metadata
  training_manifest.json       저장 완료 상태·산출물/입력/보고서 해시
reports/operational_logistic_regression.json
reports/operational_logistic_regression.md
```

모델과 원본 데이터는 기존 ignore 규칙에 따라 Git에 넣지 않는다. 새 checkout에서는 먼저 데이터 수집·데이터셋 구성이 필요하다. `.deps` 또는 현재 Python 환경에 `requirements.txt`의 의존성이 있어야 한다.

기존 실험을 덮어쓰지 않으므로 이미 실행했다면 새 경로를 지정한다. 반복 실행은 재현 확인이며 그 자체가 검증 설정 탐색은 아니다.

```powershell
python .\training\train_operational_logistic_regression.py --artifact-dir .\artifacts\operational_message\lr_recheck --report-prefix .\reports\operational_lr_recheck
python -m unittest discover -s tests -p "test_*.py"
```

종료 코드 0은 저장 성공과 수렴 경고 없음, 2는 설정·데이터·저장 검사 실패, 3은 수렴 경고 발생이다. 실패한 부분 출력에는 완료 manifest가 없을 수 있으므로 완료 결과로 사용하지 않는다. 수렴하지 않은 모델도 진단을 위해 보관하지만 배포 준비 상태로 표시하지 않는다. 신뢰할 수 없는 외부 joblib 파일을 로드하지 않는다.

## 현재 결과의 의미

초기 실험은 train 126건, validation 138건이며 각각 run 6개·세션 24개다. 최대 1,000회 중 12회에서 수렴했고 validation은 TN=52, FP=22, FN=11, TP=53이었다. 정상 메시지 묶음 전송 시나리오의 23건 중 20건이 오탐이었다. 이는 다음 비교에서 확인할 관찰 결과이며 원인을 확정한 것은 아니다.

데이터 규모가 작고 동일 생성기·단일 캠페인을 사용했다. 같은 세션의 메시지 사이에는 상관관계가 있다. 숫자형 snapshot만으로 본문의 오류를 충분히 볼 수 없는 한계도 있다. 높은 지표가 나오더라도 실제 공격 대응 성능이라고 주장하지 않는다.

## 다음 작업과 전체 진행률

이 LR 단계 이후 운영 RF 학습·비교, Validation 선정과 오분류 분석·독립 보강 설계도 완료했다. 해당 설명은 `docs/operational_random_forest.md`, `docs/operational_validation_experiment.md`, `docs/operational_error_analysis.md`에 정리했다. 현재의 다음 작업은 생성기 제어·캠페인 분리 구현과 새 수집·검증이다. 계획은 `docs/operational_data_augmentation.md`를 참조한다.

전체 로드맵 1단계의 LR/RF 학습·Validation 선정은 완료했다. 최종 마무리까지 남은 큰 작업 묶음은 6개다.

FastAPI는 계속 dummy 상태다. test 평가와 실제 모델 연결은 뒤 단계에서 수행한다.
