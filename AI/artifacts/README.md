# 모델 산출물

이 폴더는 학습과 전처리 과정에서 만들어지는 재사용 가능한 산출물을 저장합니다.

예정 파일:

```text
preprocessor_unsw_nb15.joblib
feature_schema_unsw_nb15.json
label_map_unsw_nb15.json
baseline_logistic_regression.joblib
baseline_random_forest.joblib
validation_experiment/
```

각 파일의 역할:

- `preprocessor_unsw_nb15.joblib`: train 데이터로 학습한 전처리기입니다. FastAPI inference 서버에서도 같은 전처리 규칙을 쓰기 위해 저장합니다.
- `feature_schema_unsw_nb15.json`: 원본 feature, 숫자형 feature, 범주형 feature, one-hot encoding 이후 feature 순서를 기록합니다.
- `label_map_unsw_nb15.json`: `normal=0`, `attack=1` 같은 label 의미를 기록합니다.
- `baseline_logistic_regression.joblib`: 학습 완료된 Logistic Regression 이진 분류 모델입니다.
- `baseline_random_forest.joblib`: 학습 완료된 RandomForest 이진 분류 모델입니다.
- `validation_experiment/`: 학습 부분에만 fit한 전처리기, 후보 모델, 원본 row 위치, feature schema와 선정 threshold를 함께 저장합니다. 기존 baseline과 별도 경로입니다.

평가 지표와 비교 보고서는 `reports/`에 저장합니다.

학습된 모델과 전처리기 파일은 용량이 커질 수 있으므로 기본적으로 Git에 올리지 않습니다. 이 폴더에는 설명 문서와 `.gitkeep` 파일만 추적합니다.
