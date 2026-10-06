# 보고서

이 폴더는 실험 결과와 포트폴리오용 분석 자료를 저장합니다.

예정 파일:

```text
dataset_profile_unsw_nb15.json
preprocess_summary_unsw_nb15.json
baseline_metrics.json
random_forest_metrics.json
baseline_comparison.json
logistic_error_analysis.json
logistic_error_analysis.md
model_comparison.md
figures/
```

각 파일의 역할:

- `dataset_profile_unsw_nb15.json`: 원본 데이터 row 수, column 수, label 분포, 결측치 정보를 정리한 EDA 결과입니다.
- `preprocess_summary_unsw_nb15.json`: 전처리 후 feature 개수, 저장 경로, label 분포, 결측치 처리 결과를 정리한 파일입니다.
- `baseline_metrics.json`: Logistic Regression baseline의 Accuracy, Precision, Recall, F1, ROC-AUC, Confusion Matrix와 실행 시간입니다.
- `random_forest_metrics.json`: RandomForest 고정 설정의 평가 지표, 학습 시간, 중요도, 실행 환경, 파일 SHA-256입니다.
- `baseline_comparison.json`: 두 모델의 재평가 지표, 공격 유형별 Recall, 변경된 오분류, 동일 조건 지연시간 및 주요 학습 배열 크기 비교입니다.
- `logistic_error_analysis.json`: 공격 유형별 Recall, FN, 정상 트래픽 FP 및 feature 차이를 저장한 정형 분석 결과입니다.
- `logistic_error_analysis.md`: 오분류 원인과 다음 RandomForest 실험 가설을 설명하는 포트폴리오용 보고서입니다.
- `model_comparison.md`: 현재 Logistic Regression과 RandomForest의 성능, 추론 시간, 저장 비용 및 주요 학습 배열 크기를 비교하는 한글 보고서입니다.
- `figures/`: 최종 보고서에 들어갈 그래프와 시각화 결과를 저장합니다.

이 폴더의 결과물은 “왜 이 모델을 선택했고 어떤 한계를 발견했는지”를 설명하는 근거 자료로 사용합니다.
