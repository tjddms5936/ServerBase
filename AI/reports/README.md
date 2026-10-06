# 보고서

이 폴더는 실험 결과와 포트폴리오용 분석 자료를 저장합니다.

예정 파일:

```text
dataset_profile_unsw_nb15.json
preprocess_summary_unsw_nb15.json
baseline_metrics.json
random_forest_metrics.json
baseline_comparison.json
validation_experiment.json
validation_experiment.md
feature_alignment.json
feature_alignment.md
runtime_hardening_e2e.json
data_collection_e2e.json
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
- `validation_experiment.json`: 그룹 분리 크기, 전처리 학습 범위, 고정 후보별 threshold 지표, 공격 유형별 Recall 및 선정 결과입니다.
- `validation_experiment.md`: 공식 train에서만 수행한 설정 선택의 근거와 중복 그룹 분리, 평가 해석 범위를 설명합니다.
- `feature_alignment.json`: UNSW 원본 입력 42개와 운영 입력의 의미 대조, FastAPI schema 일치 검사, 모델 schema 비호환 사유 및 근거 코드 SHA-256입니다.
- `feature_alignment.md`: 메시지 7개와 종료 요약 8개의 단위·관측 범위, UNSW 대응 불가 사유와 수집 한계를 정리한 보고서입니다. 운영 성능 평가 결과가 아닙니다.
- `runtime_hardening_e2e.json`: 실제 C++ 서버/FastAPI에서 정상 메시지와 잘못된 payload/ID/크기 8개 시나리오의 v2 JSON, dummy 응답 및 오류 1회 집계 결과입니다. 학습 데이터나 탐지 성능 지표가 아닙니다.
- `data_collection_e2e.json`: 독립된 정상/비정상 두 run에서 실제 C++ feature의 JSONL 보관, 독립 정답 참조, 서버·API 정상 종료와 최종 flush를 검증한 보고서입니다. 임시 원본은 제거하며 학습 데이터로 사용하지 않습니다.
- `logistic_error_analysis.json`: 공격 유형별 Recall, FN, 정상 트래픽 FP 및 feature 차이를 저장한 정형 분석 결과입니다.
- `logistic_error_analysis.md`: 오분류 원인과 다음 RandomForest 실험 가설을 설명하는 포트폴리오용 보고서입니다.
- `model_comparison.md`: 현재 Logistic Regression과 RandomForest의 성능, 추론 시간, 저장 비용 및 주요 학습 배열 크기를 비교하는 한글 보고서입니다.
- `figures/`: 최종 보고서에 들어갈 그래프와 시각화 결과를 저장합니다.

이 폴더의 결과물은 “왜 이 모델을 선택했고 어떤 한계를 발견했는지”를 설명하는 근거 자료로 사용합니다.
