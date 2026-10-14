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
operational_message/logistic_regression_v1/
operational_message/random_forest_v1/
operational_message/validation_selection_v1/
operational_message/augmentation_v1/
operational_sequence/lstm_v1/
operational_final_evaluation/v2/
operational_final_evaluation/holdout_usage/
```

각 파일의 역할:

- `preprocessor_unsw_nb15.joblib`: train 데이터로 학습한 전처리기입니다. FastAPI inference 서버에서도 같은 전처리 규칙을 쓰기 위해 저장합니다.
- `feature_schema_unsw_nb15.json`: 원본 feature, 숫자형 feature, 범주형 feature, one-hot encoding 이후 feature 순서를 기록합니다.
- `label_map_unsw_nb15.json`: `normal=0`, `attack=1` 같은 label 의미를 기록합니다.
- `baseline_logistic_regression.joblib`: 학습 완료된 Logistic Regression 이진 분류 모델입니다.
- `baseline_random_forest.joblib`: 학습 완료된 RandomForest 이진 분류 모델입니다.
- `validation_experiment/`: 학습 부분에만 fit한 전처리기, 후보 모델, 원본 row 위치, feature schema와 선정 threshold를 함께 저장합니다. 기존 baseline과 별도 경로입니다.
- `operational_message/logistic_regression_v1/`: 운영 train만으로 학습한 `pipeline.joblib`(scaler+LR), feature schema, label map, 고정 threshold, validation 메시지별 예측, 완료 manifest를 저장합니다. 운영 초기 baseline이며 FastAPI에 배포하거나 최종 모델로 선정하지 않았습니다. 기존 실험은 덮어쓰지 않습니다.
- `operational_message/random_forest_v1/`: 같은 운영 train만으로 학습한 RF Pipeline, identity 전처리 schema, 정답 의미, 고정 threshold=0.5, validation 메시지별 예측과 완료 manifest입니다. scaler는 사용하지 않고 기존 LR을 변경하지 않습니다. 초기 비교 대상이며 최종 모델로 선정하거나 배포하지 않았습니다.
- `operational_message/validation_selection_v1/`: 학습 전 protocol 잠금, 6개 후보 모델·개별 보고서, 모델 경로와 선정 threshold를 묶은 `selection.json`, 선정 Validation 예측과 완료 manifest입니다. 후보 기본 정책 0.5를 수정하지 않고 선정 threshold를 별도로 저장합니다. 미선정이면 모델 경로·threshold는 null이며, 선정 예측 CSV는 생성하지 않습니다. 기존 baseline은 유지합니다.
- `operational_message/augmentation_v1/`: 전체 보강 학습 전 `augmentation_protocol_lock.json`, `validation_selection/`의 6개 직접 학습 모델·42개 조합·선정 정책, 같은 Validation의 `comparison_predictions.csv`, 전체 완료 `augmentation_manifest.json`입니다. 기존 모델·데이터·진단을 보존합니다. 선정 RF의 threshold 0.3은 `validation_selection/selection.json`에서 확인하며 후보 기본 정책 0.5와 구분합니다. 최종 Test나 실제 FastAPI 모델 배포는 아직 하지 않았습니다.
- `operational_sequence/lstm_v1/`: scaler fit 전 protocol lock, 원본 Train 1,538행 전용 scaler, 기본 feature·정답·시계열 schema, 두 후보의 최적 `model.pt`·epoch 기록·Validation 예측, LSTM 후보 내 선정 정책과 완료 manifest입니다. padding은 유효 길이로 제외하고 저장 가중치·입력 계약·재예측을 검증합니다. h16·0.5가 선정됐지만 RF보다 누락이 늘어 기존 RF를 교체하지 않았습니다. 최종 Test·비용 평가와 배포 승인은 별도입니다.
- `operational_final_evaluation/v2/`: 최종 비교 전 protocol·Validation 비용 프로세스 기록·Test 전 후보 잠금·Test 창·완료 평가 manifest입니다. 고정 RF가 Test FPR 기준을 넘어서 현재 `deployment_bundle/`은 없습니다. 평가 완료와 모델 승인 보류를 구분합니다. `v1/`의 Test 전 수치 검사 실패 기록은 보존합니다.
- `operational_final_evaluation/holdout_usage/`: Test 파일 해시별 접근 예약과 최초 잠금·정책 서명을 보관합니다. 동일 정책 재검사는 노출 이력으로 표시하고 모델·threshold·승인 기준을 바꾼 재사용을 거부합니다. 이 기록을 삭제해 사용된 Test를 새 독립 Test인 것처럼 취급하지 않습니다.

평가 지표와 비교 보고서는 `reports/`에 저장합니다.

학습된 모델과 전처리기 파일은 용량이 커질 수 있으므로 기본적으로 Git에 올리지 않습니다. 이 폴더에는 설명 문서와 `.gitkeep` 파일만 추적합니다.
