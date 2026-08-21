# 보고서

이 폴더는 실험 결과와 포트폴리오용 분석 자료를 저장합니다.

예정 파일:

```text
dataset_profile_unsw_nb15.json
preprocess_summary_unsw_nb15.json
baseline_metrics.json
logistic_error_analysis.json
logistic_error_analysis.md
model_comparison.md
figures/
```

각 파일의 역할:

- `dataset_profile_unsw_nb15.json`: 원본 데이터 row 수, column 수, label 분포, 결측치 정보를 정리한 EDA 결과입니다.
- `preprocess_summary_unsw_nb15.json`: 전처리 후 feature 개수, 저장 경로, label 분포, 결측치 처리 결과를 정리한 파일입니다.
- `baseline_metrics.json`: Logistic Regression baseline의 Accuracy, Precision, Recall, F1, ROC-AUC, Confusion Matrix와 실행 시간입니다.
- `logistic_error_analysis.json`: 공격 유형별 Recall, FN, 정상 트래픽 FP 및 feature 차이를 저장한 정형 분석 결과입니다.
- `logistic_error_analysis.md`: 오분류 원인과 다음 RandomForest 실험 가설을 설명하는 포트폴리오용 보고서입니다.
- `model_comparison.md`: baseline과 개선 모델의 성능, 지연시간, 메모리 비교 보고서입니다.
- `figures/`: 최종 보고서에 들어갈 그래프와 시각화 결과를 저장합니다.

이 폴더의 결과물은 “왜 이 모델을 선택했고 어떤 한계를 발견했는지”를 설명하는 근거 자료로 사용합니다.
