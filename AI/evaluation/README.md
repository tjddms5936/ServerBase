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
compare_operational_augmentation.py
compare_operational_lstm.py
operational_final_data.py
operational_final_models.py
operational_cost_worker.py
run_operational_final_evaluation.py
analyze_operational_rf_errors.py
compare_operational_recovery.py
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

## 운영 LSTM의 동일 Validation 비교

`evaluation/compare_operational_lstm.py`는 LSTM 실행 모듈이 호출하는 비교 전용 모듈이다. 별도 CLI로 학습하거나 Test를 여는 스크립트가 아니다. 기존 선정 RF와 LSTM 기본 0.5·후보별 최적 정책·전체 선정 정책을 구분해 같은 513개 현재 메시지를 비교한다.

전체·시나리오·길이별 FP/FN, 같은 행에서 수정/신규 오분류, burst 첫 양성·request_index별 누락을 기록한다. 모든 메시지와 길이 1 malformed를 유지한다. RF는 현재 입력 7개, LSTM은 같은 의미의 과거·현재 창이라는 문맥 차이를 공개한다.

```powershell
python -X utf8 .\training\run_operational_lstm_experiment.py
```

결과는 `reports/operational_lstm_validation.json/.md/.predictions.csv`다. 선정 LSTM h16·0.5는 FP 13·FN 50·Recall 69.51%로, 기존 RF의 FP 30·FN 9·Recall 94.51%보다 오탐은 줄고 누락은 늘었다. 최종 성능·비용 benchmark나 모델 교체 승인이 아니다. threshold와 epoch 선택에 쓴 Validation의 개발 비교이며 Test는 아직 보류했다. 설명은 `docs/operational_lstm_experiment.md`에 있다.

## 운영 최종 Test·비용 평가

```powershell
python -m pip install --target .deps -r requirements-evaluation.txt
python -X utf8 .\evaluation\run_operational_final_evaluation.py
```

기존 선정 LR/RF/LSTM 정책을 그대로 검증한다. Validation 비용을 모델별 별도 프로세스에서 측정하고 성능·비용 기준으로 후보를 잠근 뒤에만 Test CSV를 읽는다. 기존 학습용 로더의 Test 금지와 이전 코드·모델 해시는 유지한다. 전처리 포함/모델 단독 지연시간, 프로세스 RSS/native peak/USS, 주요 배열·필수 파일 저장량을 나눠 기록한다.

같은 Test의 사용 예약·재실행을 기록하고 모델·threshold·승인 기준을 바꿔 새로운 독립 평가처럼 재사용하지 않는다. Test는 잠근 후보의 승인 확인과 고정 정책 진단에만 사용하며 최고 Test 점수로 모델을 바꾸지 않는다. 짧은 세션과 모든 메시지를 유지한 Test 창도 저장 후 원본과 대조한다.

Test 전 후보는 RF였지만 Test FPR 16.01%가 상한 10%를 넘었다. 평가 실행은 완료했으나 배포 승인은 보류했다. LSTM으로 교체하거나 threshold를 다시 탐색하지 않았고 FastAPI는 계속 dummy다.

완료 산출물은 `artifacts/operational_final_evaluation/v2/`, 보고서는 `reports/operational_final_evaluation.json/.md/.predictions.csv`다. `v1/`은 Test 전 수치 검사 실패 기록이다. 기존 결과를 덮어쓰지 않으며 실행 코드 0과 모델 승인 성공을 구분한다. 설명·범위·재실행 제한은 `docs/operational_final_evaluation.md`에서 확인한다.

이후 정상 묶음 오탐 진단과 새 독립 holdout 설계까지 완료했다. 현재 Test로 새 후보의 threshold를 고르지 않는다.

## RF 정상 묶음 오탐 진단

```powershell
python -X utf8 .\evaluation\analyze_operational_rf_errors.py
```

기존 RF leaf=5·0.3을 고정하고 Train/Validation만 재예측한다. 기존 Validation 513행의 확률·지표를 대조하고 시나리오별 FP/FN, 첫 메시지와 짧은 파싱 간격, 같은 누적 수신량, prefix 평균·파싱 바이트 합과 입력 분포를 기록한다. Test는 기존 최종 JSON 보고서에서 승인 보류 근거만 인용하며 Test 원본·최종 예측 CSV는 열지 않는다.

`data_collection/recovery_design.py`로 새로운 6개 캠페인의 역할·독립 seed·정상 묶음 9조합·로컬 안전 한도·제한된 후보를 검증한다. 새 데이터 수집·학습·threshold 탐색은 하지 않는다. 보정 효과가 확인됐다는 보고서가 아니다.

결과는 `reports/operational_rf_diagnosis.json/.md/.predictions.csv/.errors.csv`와 `artifacts/operational_rf_diagnosis/v1/`에 저장한다. 기존 결과가 있으면 새 `--artifact-dir`와 `--report-prefix`를 지정한다. 설명과 다음 수집 단계는 `docs/operational_rf_diagnosis.md`에 있다.

## 보정 RF의 새 Validation 비교

`compare_operational_recovery.py`는 보정 학습 실행기가 호출하는 비교 모듈입니다. 전체·정상 묶음 FPR·Recall의 세 제약, 사전 순위, 첫 메시지/후속 메시지, 시나리오·burst prefix와 같은 행의 수정/신규 오분류를 기록합니다. 별도 Test 평가나 추가 모델 학습을 시작하지 않습니다.

```powershell
python -X utf8 .\training\run_operational_recovery_experiment.py
```

기존 고정 RF·0.3, 새 원본 RF·0.3, 후보별 기본 0.5·제약 통과 최적 정책·전체 선정 정책을 구분합니다. 같은 Validation에서 rf_raw7·0.7이 선정됐지만 FP 감소와 FN 증가가 함께 있어 전체 개선을 단정하지 않습니다. `reports/operational_recovery_comparison.json/.md`와 같은 행 비교 CSV는 개발 비교이며 독립 Test 결과가 아닙니다.

설명과 다음 비용 검증은 `docs/operational_recovery_experiment.md`에 있습니다.

## 선정 보정 RF 비용 검증

```powershell
python -X utf8 .\evaluation\run_operational_recovery_cost.py
```

이미 선정한 원본 7개 RF·0.7만 재검증합니다. Validation 2,737건과 같은 요청 300개로 독립 프로세스 3회·워밍업 20회·배치 10회·계산 스레드 1개를 측정합니다. 기존 비용 worker와 고정 RF runtime은 변경 없이 재사용하며 모델·scaler fit, 추가 후보·threshold 탐색, Test CSV·수집 레코드 접근은 하지 않습니다.

전체·정상 묶음 FPR과 Recall의 기존 Validation 결과, p95 25ms·peak RSS 512MiB·필수 파일 50MiB를 대조합니다. 비용 통과 때만 `candidate_lock.json`을 만들고 독립 Test 확인은 다음 단계로 남깁니다. 실패 때 다른 후보로 대체하지 않습니다. FastAPI는 아직 dummy이며 미승인 후보 묶음은 실제 배포 자료가 아닙니다.

결과는 `reports/operational_recovery_cost.json/.md`, 산출물은 `artifacts/operational_message/recovery_v1/cost_v1/`입니다. 설명과 재측정 제한은 `docs/operational_recovery_cost.md`를 확인합니다.

## 잠근 RF의 독립 Test 확인

```powershell
python -X utf8 .\evaluation\run_operational_recovery_test.py
```

비용 완료 manifest·후보 잠금의 예상 해시를 확인하고 기존 Validation을 재현한 뒤 평가 protocol·공용 holdout 접근 예약을 남깁니다. 그 뒤에만 새 Test 5,407건을 읽어 고정 RF·0.7 하나로 전체·정상 묶음 FPR·Recall과 시나리오·캠페인·첫 메시지·burst 진단을 계산합니다. 학습·추가 후보·threshold 탐색·비용 재측정은 하지 않습니다.

실제 결과는 FP 68·FN 531, 전체 FPR 1.72%·정상 묶음 FPR 5.18%·Recall 63.20%로 로컬 advisory 연동 기준을 통과했습니다. 잘못된 길이 메시지 72건 중 71건을 놓친 한계를 함께 공개하며 운영·자동 차단 승인은 하지 않습니다. 통과 때만 별도 승인 묶음을 만들고 완료 manifest 없는 부분 산출물은 사용할 수 없습니다.

`reports/operational_recovery_test.json/.md/.predictions.csv`와 `artifacts/operational_message/recovery_v1/test_v1/`에 저장합니다. 같은 정책 재실행은 노출된 Test 재현 검사로 표시하고 변경 정책의 같은 Test 재사용을 거부합니다. 상세 설명은 `docs/operational_recovery_test.md`입니다. 다음은 승인 모델 FastAPI 연결이며 남은 큰 단계는 3개입니다.

## 실제 RF FastAPI HTTP 검사

```powershell
python -X utf8 .\evaluation\verify_approved_rf_api.py
```

승인 파일을 검증하고 실제 Uvicorn을 소유한 별도 프로세스로 실행합니다. 수작업 snapshot 3개의 HTTP 응답을 직접 RF 점수·0.7 판단과 대조하고 summary 미지원·잘못된 입력 거부·수집 비활성을 확인한 뒤 정상 종료합니다. 데이터셋·공식 Test를 사용하거나 모델을 학습하지 않습니다. 실제 검사는 성공했고 `reports/operational_rf_api.json/.md`와 `artifacts/inference_api/rf_v1/http_smoke_v1/`에 기록했습니다. 이는 탐지 성능 평가나 C++ 종단 부하 검사가 아닙니다.

실제 서비스 실행은 `python -X utf8 .\scripts\run_inference_api.py` 또는 배치 더블클릭입니다. 승인 검증·실패 정책·기존 dummy 경로와의 구분은 `docs/operational_rf_api.md`를 확인합니다. 남은 큰 단계는 TCP 종단 검증과 포트폴리오 마무리 2개입니다.

## 향후 일반 평가 명령

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
