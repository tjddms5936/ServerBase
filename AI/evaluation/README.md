# 평가

이 폴더는 오프라인 모델 평가와 추론 지연시간 benchmark 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
analyze_logistic_errors.py
compare_baselines.py
audit_feature_alignment.py
audit_operational_data.py
compare_operational_baselines.py
analyze_operational_errors.py
```

## audit_operational_data.py

보관한 자동 트래픽 수집 캠페인의 사전 기대값, 실제 JSONL, 독립 정답, 송수신 결과와 정상 종료 통계를 대조합니다. 누락·중복·의미 버전 혼입·정답 출처 불일치를 검출하고 run/세션/메시지 단위 분포를 보고합니다. 학습이나 모델 성능 평가를 수행하지 않습니다.

```powershell
python .\evaluation\audit_operational_data.py --campaign-dir "원본_캠페인_절대_경로"
```

최신 결과는 `reports/operational_data_quality.json`과 `.md`에 저장하며 검사 실패 시 종료 코드 2를 반환합니다. 메시지 관측 불가·연동 테스트·불완전 run의 학습 후보 제외 규칙은 `docs/traffic_generation.md`에서 확인합니다.

## 오프라인 Baseline 평가

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

두 baseline 학습을 마친 뒤 성능과 추론 비용을 비교합니다:

```powershell
python .\evaluation\compare_baselines.py
```

생성 파일:

```text
reports/baseline_comparison.json
reports/model_comparison.md
```

원본 test feature를 재변환해 row 정렬을 검사하고, 현재 RandomForest 학습 입력의 SHA-256 및 모델 재예측 지표를 검증합니다. 과거 Logistic Regression 학습 보고서에는 입력 hash가 없어 당시 학습 파일의 완전한 동일성까지 증명할 수는 없습니다.

공격 유형별 Recall/FN, FPR 및 같은 row에서 수정되거나 새로 발생한 오분류를 비교합니다. 지연시간은 CPU 스레드 1개, 동일한 `predict_proba` 호출, 워밍업 후 단일 요청 200회와 전체 배치 5회의 중앙값으로 비교합니다. 단일 요청 p95도 기록합니다.

압축 모델 파일 크기는 디스크 사용량이고, 주요 학습 배열 크기는 메모리 비용의 부분 추정치입니다. 프로세스 전체 메모리와 inference 최대 메모리를 의미하지 않습니다. 과거 학습 시간은 별도 실행 시점의 참고 기록으로 표시합니다.

비교 로직 검증:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

운영 feature 정합성 검사:

```powershell
python .\evaluation\audit_feature_alignment.py
```

원본 train의 헤더 42개 입력과 `configs/operational_feature_schema.json`을 대조하고, 현재 FastAPI의 필드 이름·타입과 feature 순서를 확인합니다. 메시지 7개와 세션 종료 요약 8개는 각각 별도 입력 정의로 취급합니다. C++ 수집 의미는 코드 검토 결과이며 자동 검사가 실제 네트워크 패킷을 관측하는 것은 아닙니다.

모델 schema의 입력 순서, 단위, 수집 계층, 관측 범위와 의미 버전도 비교합니다. 현재 UNSW 모델은 비호환이므로 운영 입력을 이름 변경이나 0 보정으로 연결하지 않습니다. 학습 및 실제 모델 추론은 수행하지 않고 `reports/feature_alignment.json`, `reports/feature_alignment.md`를 생성합니다.

```powershell
python .\evaluation\audit_feature_alignment.py --event-type message --require-compatible
```

이 옵션은 지정 이벤트의 모델 schema가 비호환 또는 미존재이면 종료 코드 2를 반환합니다. 별도 운영 모델 schema가 없는 현재 상태에서는 예상된 결과입니다. payload 값 검사는 `inference_api/payload_validation.py`로 분리해 현재 FastAPI와 오프라인 검사에서 함께 사용합니다. 현재 의미 버전은 v2입니다.

## 운영 LR / RF 초기 비교

```powershell
python .\evaluation\compare_operational_baselines.py
```

완료된 운영 LR/RF 모델의 데이터셋·입력·분할 해시, schema·label map·고정 threshold·산출물·보고서를 검증한다. 저장 모델로 같은 validation을 다시 예측해 기존 지표와 메시지별 예측이 일치하는지도 확인한다. 비교 중 재학습하지 않고 test CSV는 읽지 않는다.

`reports/operational_baseline_comparison.json`과 `.md`에 전체 지표 차이, 시나리오별 Recall/FPR 및 같은 메시지의 수정·신규 FP/FN을 저장한다. 기존 보고서는 덮어쓰지 않으므로 재실행은 새 `--report-prefix`로 지정한다. LR/RF를 새 경로에 학습했다면 `--lr-dir`/`--rf-dir`도 함께 지정한다.

이 결과는 통제된 메시지의 초기 validation 비교이며, threshold 선정·최종 test 평가·지연시간·메모리 benchmark·최종 모델 선정이 아니다. 설명은 `docs/operational_random_forest.md`를 참조한다.

이 비교 스크립트는 초기 threshold=0.5 비교 전용이다. 이후 설정·threshold 선정은 `training/run_operational_validation_experiment.py`와 `reports/operational_validation_experiment.json`·`.md`에서 확인한다. 선정된 0.7 정책의 메시지별 판단은 해당 실험의 `selection.json`과 `selected_validation_predictions.csv`를 사용하며 초기 0.5 예측과 혼합하지 않는다.

## 선정 운영 모델 오분류·관측 한계

```powershell
python .\evaluation\analyze_operational_errors.py
```

완료된 선정 실험의 protocol·모델·threshold·행별 예측을 검증하고 같은 Validation을 재예측한다. FP/FN의 원본 위치·입력·확률·LR 기여도와 반대 정답의 가까운 Train 예시, 세션 prefix 및 시나리오별 Train/Validation 범위를 기록한다. 재학습·threshold 변경·Test 접근·원본 JSONL 읽기·트래픽 송신은 하지 않는다.

`reports/operational_error_analysis.json`, `.md`, `.csv`를 생성한다. 기본 결과가 있으면 새 `--report-prefix`로 지정한다. CSV는 오분류 27건의 추적용이고 새 학습 데이터가 아니다. 구성 반례를 실제 수집된 동일 feature·다른 정답 쌍으로 취급하지 않는다.

독립 캠페인 설계는 `configs/operational_augmentation_plan.json`이며 기존 CampaignPlan 명령에 직접 넣는 설정이 아니다. 새 수집 명령에서 v2 계획으로 변환해 6개 캠페인을 수집했다. 설명은 `docs/operational_error_analysis.md`, `docs/operational_data_augmentation.md`, `docs/operational_augmentation_collection.md`를 참조한다.

## 기존 LR의 새 Validation 진단

```powershell
python .\evaluation\audit_frozen_operational_baseline.py
```

수집 전에 고정한 기존 LR C=1·threshold 0.7과 기존 scaler로 새 Validation 513건만 예측한다. 모델 provenance와 새 캠페인 입력 계약·사전 역할·기준 모델 해시를 별도로 검증한다. 새 Train은 형식·해시 검사만 수행하고 fit·예측은 하지 않으며 Test CSV·기존 v1 CSV·원본 JSONL은 읽지 않는다.

전체·시나리오·run 지표, 세션별 첫 양성 위치·시각, burst prefix와 헤더 거부 coverage를 `reports/operational_frozen_baseline_audit.json/.md`에 기록한다. 전체 513건은 `.predictions.csv`, 오분류 138건은 `.errors.csv`에 보관한다. 기존 결과는 덮어쓰지 않으므로 재실행은 새 `--report-prefix`를 지정한다. threshold 탐색 옵션은 없다.

실제 결과는 FP 92건·FN 46건, FPR 26.36%·Recall 71.95%·F1 63.10%다. 기존 FPR 10% 이하 기준을 충족하지 못했고 malformed 16건은 모두 놓쳤다. 이는 같은 모델의 새로운 데이터 진단이지 보강 학습 효과가 아니다. 설명과 다음 학습 설계는 `docs/operational_frozen_baseline_audit.md`에서 확인한다.

## 보강 모델의 동일 Validation 비교

`evaluation/compare_operational_augmentation.py`는 보강 실행 모듈이 호출하는 비교 전용 모듈이다. 별도 CLI로 새 학습을 시작하지 않는다. 저장 모델·학습 manifest·42개 지표를 검증하고 동일 Validation에서 기존 고정 LR, 같은 설정·0.7 재학습 LR/RF, 모델 종류별 제약 충족 최적 조합을 구분한다.

```powershell
python .\training\run_operational_augmentation_experiment.py
```

수정/신규 FP/FN과 모든 변경 행·확률·threshold·burst prefix를 `reports/operational_augmentation_comparison.json/.md`에 보관한다. 전체 비교 CSV는 `artifacts/operational_message/augmentation_v1/comparison_predictions.csv`다. 이번 선정은 RF leaf=5·0.3이지만 정상 묶음의 높은 FPR과 본문 관측 한계는 남아 있다. 설명은 `docs/operational_augmentation_experiment.md`에 정리했다.

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
