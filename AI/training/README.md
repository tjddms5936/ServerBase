# 학습

이 폴더는 모델 학습 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
train_logistic_regression.py
train_random_forest.py
run_validation_experiment.py
operational_data.py
train_operational_logistic_regression.py
train_operational_random_forest.py
run_operational_validation_experiment.py
run_operational_augmentation_experiment.py
operational_lstm.py
run_operational_lstm_experiment.py
```

전처리된 UNSW-NB15 데이터를 불러와 Logistic Regression 기준 모델을 학습하고 평가합니다.

실행:

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\training\train_logistic_regression.py
```

생성 파일:

```text
artifacts/baseline_logistic_regression.joblib
reports/baseline_metrics.json
```

RandomForest baseline 학습:

```powershell
python .\training\train_random_forest.py
```

동일한 전처리 train/test 데이터를 사용합니다. 트리 100개, `max_features=sqrt`, `max_depth=None`, `class_weight=None`, seed 42로 설정을 고정하고 최대 4개 작업 스레드로 학습합니다. test 결과를 보고 설정을 변경하지 않습니다.

추가 생성 파일:

```text
artifacts/baseline_random_forest.joblib
reports/random_forest_metrics.json
```

학습 설정, 평가 지표, feature 중요도, 실행 환경, 입력 파일과 모델의 SHA-256을 저장합니다. 공격 확률이 0.5 이상이면 공격으로 판정하며 두 baseline의 비교에도 같은 기준을 적용합니다.

Validation 분리와 고정 후보 비교:

```powershell
python .\training\run_validation_experiment.py
```

`configs/validation_experiment.json`에 후보와 선택 규칙을 미리 정의합니다. LR C=1과 RF min_samples_leaf=1/5/20을 학습하고, threshold 0.3~0.9 후보 중 validation FPR 10% 이하인 조합의 Recall을 최대화합니다. 동률은 F1 최대, FPR 최소, 후보 id, threshold 순으로 결정합니다. 수렴하지 않거나 제약을 충족하지 않은 후보는 선정하지 않습니다.

원본 train만 읽습니다. 동일 입력 feature 그룹은 한쪽에만 배치하고, 공격 유형을 층화한 5개 fold 중 고정 fold 0을 validation으로 사용합니다. 따라서 약 80:20이며 정확한 row 수는 보고서에 기록합니다. 전처리기는 분리된 학습 부분에만 fit합니다. 기존 전체 train 전처리 CSV와 공식 test는 사용하지 않습니다.

생성 파일:

```text
reports/validation_experiment.json
reports/validation_experiment.md
artifacts/validation_experiment/preprocessor.joblib
artifacts/validation_experiment/feature_schema.json
artifacts/validation_experiment/split_manifest.json
artifacts/validation_experiment/selection.json
artifacts/validation_experiment/<candidate_id>.joblib
```

`selection.json`은 선정 모델 경로와 threshold, 전처리기 경로를 함께 기록합니다. 선정 모델은 분리된 학습 부분으로 학습한 상태이며, 전체 train 재학습이나 공식 test 평가를 수행하지 않습니다. 같은 feature 그룹을 분리해도 시간/세션 전체의 독립성을 보장하는 것은 아닙니다.

## 운영 메시지 Logistic Regression

```powershell
python .\training\train_operational_logistic_regression.py
```

`configs/operational_logistic_regression.json`의 고정 운영 데이터셋을 사용한다. `operational_data.py`가 manifest·허용된 파일 해시·입력 계약·행 정렬·run/session 비중복을 검사하고, train만으로 StandardScaler와 LR을 fit한다. validation은 예측에만 사용하고 test CSV는 해시 검사에도 읽지 않는다.

최대 반복 1000회, C=1.0, solver=lbfgs, tol=0.0001, threshold=0.5의 고정 초기 모델이다. 설정이나 threshold를 validation에서 선정한 단계가 아니다. 기존 UNSW 모델을 덮어쓰지 않는다.

산출물은 `artifacts/operational_message/logistic_regression_v1/`, 보고서는 `reports/operational_logistic_regression.json`과 `.md`다. scaler와 LR을 하나의 Pipeline으로 저장하고 학습한 가중치·실제 반복 수·validation 예측을 남긴다. 기존 결과가 있으면 새 `--artifact-dir`과 `--report-prefix`를 지정한다.

설명은 `docs/operational_logistic_regression.md`를 참조한다. 동일 데이터의 운영 RF 초기 학습·비교와 아래 validation 선정도 완료했다. FastAPI는 아직 dummy 모델을 유지한다.

## 운영 메시지 RandomForest

```powershell
python .\training\train_operational_random_forest.py
python .\evaluation\compare_operational_baselines.py
```

`configs/operational_random_forest.json`으로 트리 100개, max_depth=None, min_samples_leaf=1, max_features=sqrt, bootstrap=True, seed=42, n_jobs=1, threshold=0.5를 사전에 고정했다. RF는 scaler 없이 같은 원본 입력 7개를 사용하며 train만 fit하고 test는 읽지 않는다.

산출물은 `artifacts/operational_message/random_forest_v1/`, 학습 보고서는 `reports/operational_random_forest.json`과 `.md`, 비교 보고서는 `reports/operational_baseline_comparison.json`과 `.md`다. 기존 결과를 덮어쓰지 않는다. RF 초기 모델은 validation FP 14건, FN 29건으로 LR보다 오탐은 줄었지만 누락이 늘었다. 최종 개선 모델로 선정하지 않았다.

흐름·개념·재실행 방법은 `docs/operational_random_forest.md`에 정리했다. 기존 UNSW 학습과 운영 학습을 혼합하지 않는다.

## 운영 Validation 설정·threshold 선정

```powershell
python .\training\run_operational_validation_experiment.py
```

`configs/operational_validation_experiment.json`에서 LR C=0.1/1/10, RF min_samples_leaf=1/5/10과 threshold 0.3~0.9를 고정한다. FPR 10% 이하·Recall 50% 이상 중 Recall 최대를 선정하고 동률은 F1·FPR·후보 id·threshold 순으로 처리한다. 후보별 Train 학습은 한 번씩 수행하며 threshold마다 재학습하지 않는다. Test는 읽지 않는다.

학습 전 protocol 잠금과 모델 6개·조합 42개의 전체 결과를 저장한다. 결과는 `artifacts/operational_message/validation_selection_v1/`, `reports/operational_validation_experiment.json`과 `.md`다. `selection.json`에는 모델 경로와 선정 threshold를 함께 기록한다. 후보 기본 정책의 0.5와 선정 정책을 구분한다. 제약 충족 후보가 없으면 미선정으로 완료하고 기준을 자동 완화하지 않는다.

실제 선정은 LR C=1, threshold=0.7이며 Validation FP=3, FN=24였다. 기존 baseline을 덮어쓰지 않고 최종 모델·배포 확정으로 취급하지 않는다. 재실행은 새 `--artifact-dir`과 `--report-prefix`를 지정한다. 개념·코드 흐름·한계는 `docs/operational_validation_experiment.md`에 정리했다. 선정 모델의 오분류 분석·보강 설계도 완료했고 다음 작업은 생성기 제어·캠페인 분리 구현과 새 수집·검증이다. 설명은 `docs/operational_error_analysis.md`, `docs/operational_data_augmentation.md`를 참조한다.

## 새 Train 전용 보강 LR/RF 실험

```powershell
python .\training\run_operational_augmentation_experiment.py
```

`configs/operational_augmentation_experiment.json`에서 6개 후보·7개 threshold·선정 규칙을 먼저 고정한다. 기존 모델·진단 CSV·새 캠페인 입력을 검증하고 전체 보강 lock과 후보 선정 lock을 저장한 뒤 새 Train 1,538건으로만 학습한다. 같은 Validation 513건을 사용하며 Test CSV는 읽지 않는다. Validation을 이미 진단했다는 이력도 lock과 보고서에 보관한다.

기존 `run_operational_validation_experiment.py`의 학습·선정 로직을 재사용하고 저장 후보를 다시 검증해 고정 0.7 비교와 모델별 최적 정책 비교를 분리한다. 결과는 RF leaf=5·threshold=0.3, FP 30·FN 9·FPR 8.60%·Recall 94.51%·F1 88.83%다. 최종 모델 선정은 아니다.

산출물은 `artifacts/operational_message/augmentation_v1/`, 보고서는 `reports/operational_augmentation_validation.json/.md`, `reports/operational_augmentation_comparison.json/.md`다. 기존 경로는 덮어쓰지 않는다. 재실행에는 새 `--run-dir`, `--selection-report-prefix`, `--comparison-report-prefix`를 모두 지정한다. 실제 흐름·한계·재현은 `docs/operational_augmentation_experiment.md`에서 확인한다.

## 운영 LSTM 직접 학습·Validation 비교

```powershell
python -m pip install --target .deps_lstm -r requirements-lstm.txt
python -X utf8 .\training\run_operational_lstm_experiment.py
```

`configs/operational_lstm_experiment.json`이 기존 설계 파일과 출력 경로를 지정한다. 시계열 계약·원본 행·RF 해시·선정 정책을 검증하고 scaler fit 전 실제 계획·코드·환경 lock을 저장한다. 원본 Train 1,538행으로만 scaler를 fit하며 padding을 제외한 길이 8의 창으로 hidden 16/32 두 후보를 직접 학습한다.

최대 30 epoch, 손실 개선이 5회 연속 없을 때 조기 종료하고 Validation 손실 최소 checkpoint를 저장한다. 후보마다 threshold 0.3~0.9를 비교하며 기존 FPR 10% 이하·Recall 50% 이상 중 Recall 최대 규칙을 재사용한다. Validation은 gradient 없이 손실·확률을 관측하며 가중치 갱신에는 사용하지 않는다. Test는 읽지 않는다.

실제 두 후보는 30 epoch를 사용했고 최적 epoch는 h16 30·h32 27이다. 14개 조합 중 5개가 충족해 h16·0.5를 선정했다. Validation FP 13·FN 50·F1 78.35%로 기존 RF보다 오탐은 줄고 누락은 늘었다. RF를 교체하지 않았다.

산출물은 `artifacts/operational_sequence/lstm_v1/`, 보고서는 `reports/operational_lstm_validation.json/.md/.predictions.csv`다. 기존 출력은 보존한다. 재실행에는 새 `--artifact-dir`와 `--report-prefix`가 필요하다. 설명과 실제 재학습 검사는 `docs/operational_lstm_experiment.md`, `tests/run_lstm_reproducibility.py`에서 확인한다. 이후 단계는 최종 성능·비용 비교이며 API는 아직 dummy다.

## 보정 RF 입력 표현·Validation 실험

```powershell
python -X utf8 .\training\run_operational_recovery_experiment.py
```

새 Train 7,976건만으로 원본 7개·송수신량 제외 5개·평균 간격 5개·평균+파싱 합 6개 입력의 RF 4개를 각각 한 번 학습합니다. 사전 계획의 트리 100·최소 leaf 5·seed 42·n_jobs=1을 유지하고 scaler나 Validation 합산 fit은 하지 않습니다. 후보/threshold를 수집 전 계획과 대조하고 새 Validation 예측·학습 전에 실제 protocol을 잠급니다.

새 Validation 2,737건에서 20개 정책을 비교합니다. 전체 FPR·정상 묶음 FPR 모두 10% 이하, Recall 50% 이상 중 Recall·F1·FPR 순으로 고릅니다. 통과한 2개 중 원본 입력 RF·0.7이 선정됐고 FP 39·FN 275건입니다. 기존 RF·0.3은 재학습 없이 같은 Validation에서 FP 266·FN 71건이었습니다. 오탐 감소와 누락 증가를 함께 보고합니다.

산출물은 `artifacts/operational_message/recovery_v1/validation_selection/`, 보고서는 `reports/operational_recovery_validation.json/.md`와 `reports/operational_recovery_comparison.json/.md`입니다. 원본 schema와 모델 입력 schema, 기본 0.5와 선정 0.7 정책을 구분합니다. 새 Test CSV·raw JSONL·종료 summary·비용 측정·배포는 미실행입니다.

기본 출력은 이미 있으므로 재실행은 새 `--artifact-dir`, `--report-prefix`, `--comparison-report-prefix`를 모두 지정합니다. 설명과 검증은 `docs/operational_recovery_experiment.md`에 있습니다. 현재 선정은 비용/Test 확인 대기이며, raw7 입력이므로 추가 Python prefix 상태는 필요 없습니다.

## 초기 공개 데이터 목표

1. 전처리된 UNSW-NB15 데이터를 로드합니다.
2. Logistic Regression 기준 모델을 학습합니다.
3. Accuracy, Precision, Recall, F1, ROC-AUC, Confusion Matrix를 계산합니다.
4. 학습된 모델 산출물을 `artifacts/`에 저장합니다.
5. 평가 지표를 `reports/`에 저장합니다.
