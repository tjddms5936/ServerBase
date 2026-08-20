# 학습

이 폴더는 모델 학습 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
train_logistic_regression.py
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

이후 추가할 스크립트:

```text
train_random_forest.py
train_lstm.py
```

초기 목표:

1. 전처리된 UNSW-NB15 데이터를 로드합니다.
2. Logistic Regression 기준 모델을 학습합니다.
3. Accuracy, Precision, Recall, F1, ROC-AUC, Confusion Matrix를 계산합니다.
4. 학습된 모델 산출물을 `artifacts/`에 저장합니다.
5. 평가 지표를 `reports/`에 저장합니다.
