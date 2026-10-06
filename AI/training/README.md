# 학습

이 폴더는 모델 학습 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
train_logistic_regression.py
train_random_forest.py
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
