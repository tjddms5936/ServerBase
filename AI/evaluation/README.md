# 평가

이 폴더는 오프라인 모델 평가와 추론 지연시간 benchmark 스크립트를 저장하는 곳입니다.

현재 스크립트:

```text
analyze_logistic_errors.py
compare_baselines.py
audit_feature_alignment.py
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
