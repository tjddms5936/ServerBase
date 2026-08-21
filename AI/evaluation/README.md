# 평가

이 폴더는 오프라인 모델 평가와 추론 지연시간 benchmark 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
analyze_logistic_errors.py
```

저장된 Logistic Regression baseline과 UNSW-NB15 test 데이터를 이용해 공격 유형별 Recall, False Negative, False Positive 특성을 분석합니다.

실행:

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\evaluation\analyze_logistic_errors.py
```

생성 파일:

```text
reports/logistic_error_analysis.json
reports/logistic_error_analysis.md
```

이후 추가할 스크립트:

```text
evaluate_model.py
benchmark_latency.py
```

평가 지표:

- 정확도(Accuracy)
- 정밀도(Precision)
- 재현율(Recall)
- F1 점수(F1-score)
- ROC-AUC
- 혼동 행렬(Confusion matrix)
- 추론 지연시간(Inference latency)
- 모델 산출물 크기(Model artifact size)

오분류 분석 항목:

- 공격 유형별 Recall과 False Negative
- 정상 트래픽 False Positive Rate
- False Positive 공격 확률 분포
- FP와 TN의 숫자형 및 범주형 feature 차이
- 분류 threshold 변화에 따른 Precision, Recall, FPR 변화
- Logistic Regression 계수 분석
