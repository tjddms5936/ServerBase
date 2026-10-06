# 학습

이 폴더는 모델 학습 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
train_logistic_regression.py
train_random_forest.py
run_validation_experiment.py
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

이후 추가할 스크립트:

```text
train_lstm.py
```

초기 목표:

1. 전처리된 UNSW-NB15 데이터를 로드합니다.
2. Logistic Regression 기준 모델을 학습합니다.
3. Accuracy, Precision, Recall, F1, ROC-AUC, Confusion Matrix를 계산합니다.
4. 학습된 모델 산출물을 `artifacts/`에 저장합니다.
5. 평가 지표를 `reports/`에 저장합니다.
