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
operational_data_quality.json
operational_data_quality.md
operational_dataset.json
operational_dataset.md
operational_logistic_regression.json
operational_logistic_regression.md
operational_random_forest.json
operational_random_forest.md
operational_baseline_comparison.json
operational_baseline_comparison.md
operational_validation_experiment.json
operational_validation_experiment.md
operational_error_analysis.json
operational_error_analysis.md
operational_error_analysis.csv
operational_augmentation_collection.json
operational_augmentation_collection.md
operational_dataset_v2.json
operational_dataset_v2.md
operational_frozen_baseline_audit.json
operational_frozen_baseline_audit.md
operational_frozen_baseline_audit.predictions.csv
operational_frozen_baseline_audit.errors.csv
operational_augmentation_validation.json
operational_augmentation_validation.md
operational_augmentation_comparison.json
operational_augmentation_comparison.md
operational_sequence_dataset.json
operational_sequence_dataset.md
operational_lstm_validation.json
operational_lstm_validation.md
operational_lstm_validation.predictions.csv
operational_lstm_reproducibility.json
operational_final_evaluation.json
operational_final_evaluation.md
operational_final_evaluation.predictions.csv
operational_rf_diagnosis.json
operational_rf_diagnosis.md
operational_rf_diagnosis.predictions.csv
operational_rf_diagnosis.errors.csv
operational_recovery_collection.json
operational_recovery_collection.md
operational_recovery_dataset.json
operational_recovery_dataset.md
operational_recovery_validation.json
operational_recovery_validation.md
operational_recovery_comparison.json
operational_recovery_comparison.md
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
- `operational_data_quality.json`: 보관한 다중 run 캠페인의 사전 기대값 대조, 원본 누락·중복·정답 출처·실행 종료 검사, 시나리오·run·세션·메시지 정답 분포와 메시지 학습 후보입니다. 실제 모델 성능이 아닙니다.
- `operational_data_quality.md`: 해당 캠페인의 결과, 제외 사유·관측 불가 범위와 다음 데이터셋 분리 작업을 설명하는 한글 보고서입니다. 최신 보고서가 참조하는 실제 원본은 raw 캠페인 디렉터리에 보관합니다.
- `operational_dataset.json`: 운영 메시지의 고정 run/session 분할, 정답·시나리오 분포, 식별자 및 feature 중복, 제외 원본, train 전용 진단, 원본·출력·생성 코드 해시입니다. 전처리나 모델 학습 결과는 아닙니다.
- `operational_dataset.md`: train·validation·test의 실제 크기·분할 원칙·제외 사유와 해석 한계를 정리한 한글 보고서입니다. test 성능 지표를 포함하지 않습니다.
- `operational_logistic_regression.json`: 운영 train 전용 scaler/LR 학습 조건, 가중치·기본값, 실제 반복·수렴 상태, train/validation 지표, 시나리오별 FP/FN과 입력·코드·산출물 해시입니다. test CSV는 읽지 않고 threshold=0.5를 고정했습니다.
- `operational_logistic_regression.md`: 초기 운영 LR의 실제 결과·오탐·누락·한계와 다음 RF 비교 작업을 정리합니다. 최종 모델 선정이나 실제 침입 탐지 성능 보고서가 아닙니다.
- `operational_random_forest.json`: 운영 train 전용 RF 초기 학습 조건, 실제 트리 수·깊이·노드 수, 불순도 감소 중요도, train/validation 지표·시나리오별 FP/FN과 해시입니다. threshold=0.5를 고정하고 test는 읽지 않습니다.
- `operational_random_forest.md`: 해당 RF 실험 결과·한계와 LR 비교 및 후속 validation 선정 작업을 설명합니다.
- `operational_baseline_comparison.json`: 두 모델의 입력·분할·산출물·보고서 검증 후 같은 validation 재예측 지표, 시나리오별 Recall/FPR, 수정·신규 FP/FN과 예측이 달라진 row_id·확률을 보관합니다.
- `operational_baseline_comparison.md`: RF에서 오탐이 줄었지만 누락이 증가한 실제 초기 결과를 설명합니다. 최종 모델 선정이나 지연시간·메모리 benchmark가 아닙니다.
- `operational_validation_experiment.json`: 학습 전에 고정한 6개 후보·7개 threshold·FPR/Recall 제약, 전체 42개 조합 지표·시나리오별 결과·탈락 이유, 후보 수렴·임시 baseline 선정·해시를 보관합니다. Test는 읽지 않고 Validation 합산 재학습도 하지 않습니다.
- `operational_validation_experiment.md`: 실제 선정 LR C=1·threshold=0.7의 근거와 오탐 감소·누락 증가, malformed 시나리오 Recall 0 및 다음 관측 한계 분석을 설명합니다. 최종 모델 확정이나 독립적인 최종 성능 평가가 아닙니다.
- `operational_error_analysis.json`: 선정 모델의 Validation 오분류·세션 prefix·입력 범위, LR 기여도·가까운 반대 정답 Train 예시, 본문 정보 구성 반례·source 해시와 독립 보강 설계입니다. 재학습·Test 평가·새 수집은 하지 않았습니다.
- `operational_error_analysis.md`: FP 3건·FN 24건의 위치와 두 malformed 시나리오의 서로 다른 관측 한계를 설명합니다. 원인 확정이나 독립 성능 평가가 아닙니다.
- `operational_error_analysis.csv`: 오분류 27건의 row_id·run/session·메시지·원본 행 번호·입력 7개·정답·예측·확률·threshold입니다. 추적용 파생 보고서이며 새 학습 데이터가 아닙니다.
- `operational_augmentation_collection.json/.md`: 독립 캠페인 6개의 사전 역할·계획·기준 모델 해시, 실제 run·누락·중복·종료·정답 분포, 제어 coverage와 Train/Validation 송신·파싱 관측을 보관합니다. 모델 학습·예측·Test 성능 평가는 하지 않습니다.
- `operational_dataset_v2.json/.md`: 사전 캠페인 단위 Train/Validation/Test 분할의 크기, 식별자 중복, feature 값 중복, 헤더 거부 제외 사유, 원본·출력 SHA-256입니다. v1 데이터셋 보고서를 덮어쓰지 않습니다.
- `operational_frozen_baseline_audit.json/.md`: 기존 LR·threshold 0.7로 새 Validation 513건만 예측한 전체·시나리오·run 지표, 세션 첫 양성·burst prefix·헤더 거부 coverage와 입력·모델 보존 해시입니다. 재학습·Train 예측·Test 접근·threshold 변경은 하지 않았습니다.
- `operational_frozen_baseline_audit.predictions.csv`: 새 Validation 전체의 식별자·입력 7개·정답·확률·예측·0.7 threshold·오류 종류입니다. 정답은 기존 독립 시나리오 정의를 유지합니다.
- `operational_frozen_baseline_audit.errors.csv`: 정상 오탐 92건·이상 누락 46건, 총 138건의 추적 자료입니다. 이 파일을 새 정답이나 추가 학습 데이터로 자동 사용하지 않습니다.
- `operational_augmentation_validation.json/.md`: 새 Train 1,538건 전용 LR/RF 후보 6개 직접 학습·수렴/트리 기록과 학습 전 고정한 42개 조합·제약·선정 근거입니다. Validation 513건에서 RF leaf=5·threshold=0.3을 선정했습니다. Test는 읽지 않습니다.
- `operational_augmentation_comparison.json/.md`: 같은 Validation 행에서 기존 LR 0.7, 보강 LR/RF 0.7, 모델별 제약 충족 최적 정책을 구분한 지표·수정/신규 오분류·모든 변경 row_id·burst prefix·입력/모델 보존 근거입니다. 이미 진단한 Validation의 개발 비교이며 최종 성능·배포 결과가 아닙니다.
- `operational_sequence_dataset.json/.md`: 같은 campaign/run/session의 과거·현재 최대 8개 창 구성, Train/Validation shape·정답·유효 길이·시나리오 coverage·식별자 비중복·원본/출력/코드 해시입니다. 짧은 창과 길이 1 메시지를 모두 유지하며 저장 후 원본 대조를 수행합니다. scaler fit·LSTM 학습·모델 예측·Test 접근은 하지 않았고 모델 성능 보고서도 아닙니다.
- `operational_lstm_validation.json/.md`: 원본 Train 전용 scaler·LSTM 두 후보 직접 학습, epoch별 손실·최적 checkpoint·종료·파라미터 수, 14개 threshold 조합·선정 근거와 기존 RF 대비 같은 Validation의 시나리오·길이·오분류·burst prefix입니다. h16·0.5를 LSTM 후보 중 선정했지만 RF보다 FN이 늘었습니다. Test·최종 배포 검증은 아닙니다.
- `operational_lstm_validation.predictions.csv`: 동일 Validation 513개 현재 메시지의 식별자·원본 수치·정답·문맥 길이·각 비교 역할의 확률/판단/threshold입니다. 비교·추적 자료이며 새로운 학습 데이터나 정답이 아닙니다.
- `operational_lstm_reproducibility.json`: 완료된 실제 LSTM 실험을 임시 경로에 재학습해 후보별 epoch 손실·가중치·checkpoint 해시, 전체 14개 지표·선정·비교 CSV가 일치함을 검증한 결과입니다. 현재 CPU 환경에서의 재현 증거이며 독립 데이터 일반화 평가가 아닙니다. 원본 모델은 보존하고 재현 임시 산출물만 제거합니다.
- `operational_final_evaluation.json/.md`: 고정 LR/RF/LSTM의 Validation 기반 독립 프로세스 비용, Test 전 RF 잠금, Test 1,071건의 성능·시나리오·coverage·prefix·같은 행 오분류 비교와 승인 판단입니다. RF의 FPR 16.01%로 승인을 보류했으며 Test에서 다른 모델로 재선정하지 않았습니다. 비용은 API/HTTP 종단 측정이 아니고 실제 침입·운영 배포 승인을 의미하지 않습니다.
- `operational_final_evaluation.predictions.csv`: Test 전체의 식별자·원본 수치·독립 정답·문맥 길이·각 고정 모델의 확률/판단/threshold입니다. 현재 Test는 이미 모델 평가에 사용됐으며 이 CSV를 새 학습 데이터로 자동 추가하거나 새 독립 평가 자료로 주장하지 않습니다.
- `operational_rf_diagnosis.json/.md`: 고정 RF의 Train/Validation 재예측, 정상 묶음 오탐·첫 메시지·파싱 간격·입력 중요도·prefix 분포와 새 보정 후보·독립 캠페인 설계 검증입니다. 기존 Test는 완료 보고서에서만 인용하고 새 Test 접근·학습·수집·승인은 하지 않았습니다.
- `operational_rf_diagnosis.predictions.csv`: Train/Validation 2,051행의 원본 식별자·입력·정답·고정 확률/판단과 과거·현재 prefix 진단 값입니다. 기존 최종 Test 예측 CSV와 구분합니다.
- `operational_rf_diagnosis.errors.csv`: 고정 RF의 Train FP 113건과 Validation FP 30건·FN 9건, 총 152건의 추적 자료입니다. 새로운 학습 정답으로 자동 추가하지 않습니다.
- `operational_recovery_collection.json/.md`: 보정 캠페인 6개·378개 run의 실제 실행·수집 품질·독립 정답·사전 역할·해시 잠금·정상 묶음 9조합·Train/Validation 관측과 데이터셋 대조 결과입니다. 메시지 16,120건을 수집했지만 학습 모델 로딩·예측·Test 성능 평가·배포는 하지 않았습니다.
- `operational_recovery_dataset.json/.md`: 새 Train 7,976건·Validation 2,737건·Test 5,407건의 사전 캠페인 분할, 정답 분포·식별자 비중복·동일 feature 값 반복·헤더 거부 제외·원본/출력 해시입니다. 정규화·모델 학습·독립 승인 결과가 아니며 새 Test는 품질 확인에만 사용했습니다.
- `operational_recovery_validation.json/.md`: 새 Train 7,976건 전용 RF 후보 4개 학습·실제 트리·입력 변환 schema·protocol과 새 Validation 2,737건의 전체 20개 정책·탈락 근거·개발 선정입니다. rf_raw7·0.7의 FP 39·FN 275건을 기록하며 비용·새 Test·배포는 미확인입니다.
- `operational_recovery_comparison.json/.md`: 같은 Validation에서 기존 RF·0.3과 새 원본 RF·0.3, 기본 0.5·후보별 최적·전체 선정 정책을 구분한 시나리오·첫 메시지·오분류 변경·burst prefix입니다. FP 266→39건과 FN 71→275건을 함께 해석하고 재학습/threshold 효과를 분리합니다. 실제 모델이나 threshold를 Test로 고른 결과가 아닙니다.
- `operational_recovery_cost.json/.md`: 선정 원본 RF·0.7의 학습·입력 보호 해시, 동일 Validation의 별도 프로세스 3개 비용 원본·요약, 단일/배치 판단 일치, 계산 스레드·Torch 미로딩 확인, 사전 예산과 Test 전 후보 잠금입니다. 모델 재학습·정책 변경·Test 예측·접근 예약·실제 배포 승인은 하지 않습니다. HTTP·JSON·큐 비용은 별도 후속 검증이며 후보 묶음은 미승인입니다.
- `logistic_error_analysis.json`: 공격 유형별 Recall, FN, 정상 트래픽 FP 및 feature 차이를 저장한 정형 분석 결과입니다.
- `logistic_error_analysis.md`: 오분류 원인과 다음 RandomForest 실험 가설을 설명하는 포트폴리오용 보고서입니다.
- `model_comparison.md`: 현재 Logistic Regression과 RandomForest의 성능, 추론 시간, 저장 비용 및 주요 학습 배열 크기를 비교하는 한글 보고서입니다.
- `figures/`: 최종 보고서에 들어갈 그래프와 시각화 결과를 저장합니다.

이 폴더의 결과물은 “왜 이 모델을 선택했고 어떤 한계를 발견했는지”를 설명하는 근거 자료로 사용합니다.
