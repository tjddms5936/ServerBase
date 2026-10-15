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
operational_message/recovery_v1/validation_selection/
operational_message/recovery_v1/cost_v1/
operational_message/recovery_v1/test_v1/
inference_api/rf_v1/http_smoke_v1/
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
- `operational_message/recovery_v1/validation_selection/`: 새 Train 7,976건 전용 RF 입력 후보 4개와 사전 protocol, 원본 schema·정답 map, 후보별 모델 입력 schema·기본 0.5 정책·실제 학습 기록·Validation 예측, 20개 조합의 선정 정책·동일 행 비교·완료 manifest입니다. 개발 선정은 원본 7개 RF·0.7이며 비용과 새 Test는 미확인입니다. `selection.json`의 `cost_verified=false`, `test_confirmed=false`, `deployment_ready=false`를 확인합니다. 이 모델이 저장돼 있다고 FastAPI 배포가 승인된 것은 아닙니다. raw7 선정 정책은 추가 Python prefix 상태가 필요 없고 상태형 후보의 명세와 구분합니다.
- `operational_sequence/lstm_v1/`: scaler fit 전 protocol lock, 원본 Train 1,538행 전용 scaler, 기본 feature·정답·시계열 schema, 두 후보의 최적 `model.pt`·epoch 기록·Validation 예측, LSTM 후보 내 선정 정책과 완료 manifest입니다. padding은 유효 길이로 제외하고 저장 가중치·입력 계약·재예측을 검증합니다. h16·0.5가 선정됐지만 RF보다 누락이 늘어 기존 RF를 교체하지 않았습니다. 최종 Test·비용 평가와 배포 승인은 별도입니다.
- `operational_final_evaluation/v2/`: 최종 비교 전 protocol·Validation 비용 프로세스 기록·Test 전 후보 잠금·Test 창·완료 평가 manifest입니다. 고정 RF가 Test FPR 기준을 넘어서 현재 `deployment_bundle/`은 없습니다. 평가 완료와 모델 승인 보류를 구분합니다. `v1/`의 Test 전 수치 검사 실패 기록은 보존합니다.
- `operational_final_evaluation/holdout_usage/`: Test 파일 해시별 접근 예약과 최초 잠금·정책 서명을 보관합니다. 동일 정책 재검사는 노출 이력으로 표시하고 모델·threshold·승인 기준을 바꾼 재사용을 거부합니다. 이 기록을 삭제해 사용된 Test를 새 독립 Test인 것처럼 취급하지 않습니다.

- `operational_message/recovery_v1/cost_v1/`: 선정 원본 RF·0.7의 미승인 `candidate_bundle/`, 측정 전 protocol, Validation NPZ·동일 요청 목록·독립 프로세스 비용 기록, 조건부 Test 전 잠금과 완료 manifest입니다. 필수 후보 파일은 모델·원본 schema·모델 입력 schema·label map·선정 정책 5개입니다. `candidate_lock.json`은 비용 통과 때만 생성하며 독립 Test 확인·실제 모델 배포 승인과 구분합니다. 이전 학습 selection은 당시 상태를 보존하고 이번 상태는 새 비용 보고서에서 확인합니다. `deployment_bundle/`을 생성하거나 FastAPI를 교체하지 않습니다.

- `operational_message/recovery_v1/test_v1/`: Test 접근 전 평가 protocol, 고정 RF·0.7 판정 파일, 승인 시의 `deployment_bundle/`, 완료 평가 manifest입니다. 실제 새 Test는 전체 FPR 1.72%·정상 묶음 FPR 5.18%·Recall 63.20%로 통과했습니다. 승인 묶음의 모델·원본 schema·모델 입력 schema·label map·0.7 정책과 manifest는 로컬 advisory 연동용입니다. 원본 후보의 기본 0.5 정책·학습/비용 selection은 역사적 기록으로 유지합니다. 부분 생성 파일만으로 사용하지 말고 `requires_complete_evaluation_manifest`가 가리키는 완료 기록과 묶음 해시를 함께 검사합니다. FastAPI는 아직 dummy이며 잘못된 길이 FN 71/72건·첫 메시지 관측 한계가 남습니다. 공용 `operational_final_evaluation/holdout_usage/`의 새 Test 접근 기록은 지우지 않습니다.

- `inference_api/rf_v1/http_smoke_v1/`: 실제 승인 RF Uvicorn을 로컬 포트에 실행한 로그와 정상 종료·보고서 해시를 기록한 HTTP 기능 검사 manifest입니다. 검사용 프로세스는 종료했으며 새 학습 모델·평가 데이터가 아닙니다. 실제 RF API는 기존 `recovery_v1/test_v1/deployment_bundle/`을 수정하지 않고 검증·로딩합니다. 당시 승인 manifest의 FastAPI 미구현 표시는 역사적 기록으로 유지하고 현재 구현은 새 API 보고서에서 확인합니다.

평가 지표와 비교 보고서는 `reports/`에 저장합니다.

학습된 모델과 전처리기 파일은 용량이 커질 수 있으므로 기본적으로 Git에 올리지 않습니다. 이 폴더에는 설명 문서와 `.gitkeep` 파일만 추적합니다.
