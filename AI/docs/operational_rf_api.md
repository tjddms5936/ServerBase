# 승인 묶음 검증과 FastAPI 실제 RF 추론

## 이번 단계의 목적

이전 단계에서 고정 RF·threshold 0.7이 독립 Test의 사전 기준을 통과했다. 이번에는 학습이나 성능 재평가 없이 승인된 모델을 FastAPI에 로딩해 실제 메시지 판단을 반환한다.

전체 흐름은 다음과 같다.

```text
기동 시: 고정 설정 → 완료 평가·승인 묶음·판정·후보 잠금 검증 → RF 한 번 로딩 → 준비 완료
요청 시: v2 JSON 검증 → 원본 feature 7개 순서 고정 → RF 확률 → 0.7 비교 → 응답
```

승인 파일이 없거나 해시·의미·버전이 다르면 기동에 실패한다. 실제 RF 기동 실패를 dummy나 정상 판단으로 숨기지 않는다. 원본 모델·학습·비용·Test 산출물은 수정하지 않는다.

## 파일 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_rf_serving.json` | 승인 평가·후보 잠금 경로, 완료/승인 manifest의 고정 해시, 서비스 버전, 동시 추론 한도와 payload 로그 여부를 지정한다. |
| `inference_api/approved_bundle.py` | 승인 연결과 파일 10개를 검증하고 같은 검증 바이트에서 모델을 로딩한다. |
| `inference_api/rf_service.py` | message 원본 7개를 RF에 전달하고 이상 점수·0.7 판단·응답을 만든다. |
| `inference_api/rf_main.py` | 실제 RF용 앱을 생성한다. import 시점이 아닌 lifespan 시작 때 모델을 로딩한다. |
| `inference_api/main.py` | 공통 앱 생성·입력 오류·수집·health·추론 경로를 관리한다. 기존 dummy 앱도 보존한다. |
| `inference_api/schemas.py` | 기존 C++ 응답 6개 필드를 유지하면서 이상 점수·threshold·범위와 health 정보를 추가한다. 입력 v2 의미는 바꾸지 않는다. |
| `scripts/run_inference_api.py` | 승인 RF 앱을 127.0.0.1에서 실행한다. 자동 검사에서는 소유한 stdin의 quit으로 정상 종료한다. |
| `run_fastapi.bat` | 더블클릭 실행을 실제 RF 진입점으로 연결한다. 문자 인코딩 문제를 피하도록 ASCII·CRLF를 유지한다. |
| `requirements-inference.txt` | 승인 당시 과학 계산·API 런타임 버전을 고정한다. 기존 requirements는 보존한다. |
| `tests/test_approved_rf_api.py` | 임시 합성 모델로 승인 검증·준비·추론·실패·포화·기존 C++ 필드 호환을 검사한다. |
| `evaluation/verify_approved_rf_api.py` | 실제 Uvicorn과 로컬 HTTP를 실행해 직접 모델 계산과 응답을 대조하고 정상 종료한다. |

## 무엇을 검증하는가

1. 설정에 고정한 `evaluation_manifest.json` 해시와 정상 완료·승인 상태.
2. 완료 기록의 평가 protocol·Test 판정·승인 묶음 6개, 총 8개 파일의 해시·크기·허용 경로.
3. 설정에 고정한 `deployment_manifest.json` 해시와 로컬 한정·운영/자동 차단 금지 상태.
4. Test 판정·후보 잠금의 해시·정책 서명·Test 예상 해시·기존 성능/비용 기준 일치.
5. 모델·원본 schema·모델 입력 schema·label map·판정 정책의 5개 구성과 파일 크기 예산.
6. feature 의미 버전·입력 순서·타입·단위·관측 계층·결측 입력 거부와 raw7 변환.
7. `normal=0`, `controlled_anomaly=1`, 양성 클래스 1, `score >= 0.7` 정책.
8. 승인 당시 numpy/scikit-learn 버전과 Python 주·부 버전.
9. scaler 없는 Pipeline·RF 종류·클래스·입력 열·트리 수·leaf·seed·n_jobs 설정.

기동에서 읽는 파일은 완료 평가 기록 1개·그 산출물 8개·후보 잠금 1개다. 그 안에 기록된 Train/Test CSV·예측 CSV·수집 JSONL·과거 소스 파일을 다시 열지 않는다. 모델 로딩 후 요청마다 파일을 읽거나 모델을 다시 로딩하지 않는다.

joblib은 pickle 기반이므로 신뢰하는 로컬 산출물만 사용한다. 해시는 파일 연결·변조를 확인하지만 외부 인증기관의 서명이나 악성 파일의 안전성을 보장하지 않는다. 공격자가 설정과 고정 해시까지 바꿀 수 있는 상황을 해결하는 보안 체계는 아니다. 사용자 업로드나 원격 URL의 모델을 로딩하는 기능은 없다.

## 실제 경로와 기존 dummy 경로

| 진입점 | 용도 | 판단 |
| --- | --- | --- |
| `inference_api.rf_main:app` | 실제 승인 모델 서비스 | message는 RF·0.7, summary는 미지원 |
| `scripts/run_inference_api.py` | 실제 RF 실행·자동 검사 제어 | 위 RF 앱과 동일 |
| `run_fastapi.bat` | 사용자의 더블클릭 실행 | 기본 실제 RF 앱 |
| `inference_api.main:app` | 기존 수집·회귀 테스트 보존 | 명시적 dummy |

기존 수집기는 `inference_api.main:app`을 계속 사용하므로 과거 독립 정답·수집 응답 계약을 변경하지 않는다. 실제 RF가 실패했을 때 자동으로 이 경로로 바뀌는 것은 아니다. `/health`의 `model_kind=random_forest`와 `model_version=rf-raw7-recovery-v1-t0.7`로 올바른 앱인지 확인한다.

기존 승인 manifest의 `fastapi_integration_implemented=false`는 Test 단계 당시의 기록으로 그대로 둔다. 현재 API 구현과 검증 결과는 새 API 문서·보고서로 관리한다.

## 메시지 요청과 응답

기존 `server_application_message_v2` JSON을 그대로 사용한다. 숫자 문자열·bool·음수·NaN·Infinity·누락/추가 feature·중복 값 불일치·프레임 크기 불일치·잘못된 시각은 추론 전에 422로 거부한다. packet_size는 IP 패킷이 아닌 애플리케이션 프레임 크기다.

입력 순서는 다음과 같다.

```text
packet_size, payload_size, connection_duration_ms, message_interval_ms,
bytes_received_total, bytes_sent_total, error_count_total
```

JSON 키 순서·세션 ID·queue_sequence_id를 모델 predictor로 사용하지 않는다. 별도 scaler나 Python 세션 이력도 추가하지 않는다. C++가 계산한 현재 snapshot의 누적값을 기존 학습과 같은 float64 순서로 전달한다.

성공 응답은 기존 C++ 필드를 유지한다.

```json
{
  "request_id": "102",
  "label": "controlled_anomaly",
  "is_attack": true,
  "confidence": 0.8634681690815189,
  "model_version": "rf-raw7-recovery-v1-t0.7",
  "received_event_type": "message",
  "anomaly_score": 0.8634681690815189,
  "decision_threshold": 0.7,
  "decision_scope": "local_advisory"
}
```

- `anomaly_score`: RF의 양성 클래스 점수다. 항상 이 값을 0.7과 비교한다.
- `is_attack`: 점수가 0.7 이상이면 true다. 클래스별 argmax나 기본 0.5를 사용하지 않는다.
- `label`: true면 controlled_anomaly, 아니면 normal이다. 실제 외부 공격 종류를 확정한 이름은 아니다.
- `confidence`: 반환한 클래스의 원래 점수다. 이상이면 score, 정상이면 1-score다. threshold가 0.7이므로 정상 판단의 confidence가 0.5보다 작을 수도 있다.
- 두 점수 모두 보정된 실제 위험 확률이나 안전 보장이 아니다. confidence만으로 자동 차단하지 않는다.

## 요약·실패·동시성 처리

승인 RF는 메시지 모델이지 세션 종료 요약 모델이 아니다. `session_summary`는 입력 schema 검증과 선택적 수집까지만 하고 HTTP 422와 `unsupported_event_type`을 반환한다. RF를 호출하거나 가짜 정상 판단을 만들지 않는다.

현재 C++ worker는 비2xx 응답을 결과 저장에 사용하지 않으므로 마지막 성공 메시지 판단을 요약의 dummy 정상값으로 덮어쓰지 않는다. 다만 기존 구현에서는 요약 요청도 실패 카운터에 들어간다. 정상적인 미지원과 실제 전송 장애의 분리·필터링은 다음 TCP 통합 단계에서 검토한다. 이번에는 C++를 수정하지 않았다.

실제 모델 미준비는 503 `model_not_ready`, 계산 실패는 503 `inference_failed`다. 정상이나 dummy 판단으로 대체하지 않는다. 기동 검증 실패는 Uvicorn 기동 자체를 중단하므로 연결이 열리지 않을 수 있다.

RF 계산은 작업 스레드로 보내 이벤트 루프를 막지 않는다. 앱 프로세스당 동시 RF 계산은 1개이며 추가 계산 요청은 대기열을 무한히 늘리지 않고 503 `inference_busy`와 Retry-After를 반환한다. 계산 중에도 health는 응답한다. 요청 취소 때도 실제 계산이 끝날 때까지 슬롯을 유지한다.

실행기는 Uvicorn worker 1개·HTTP 동시 연결/작업 상한 32개·graceful shutdown 15초를 사용한다. RF의 n_jobs=1과 별개로 운영체제의 전체 스레드 수가 1개라는 뜻은 아니다. 현재 C++ worker는 503을 자동 재시도하지 않으며 미판단 상태·결과 시점은 다음 통합 단계에서 확인한다.

## 실행 방법

마우스로 실행할 때는 AI 폴더의 `run_fastapi.bat`을 더블클릭한다. 기존 Server 폴더의 실행 배치는 AI 배치로 연결되므로 Server 소스 변경은 필요 없다. 종료는 해당 콘솔에서 Ctrl+C다.

명령어로 실행할 때는 AI 폴더에서 다음을 사용한다.

```powershell
python -m pip install --target .deps -r requirements-inference.txt
python -X utf8 .\scripts\run_inference_api.py
```

이미 설치된 라이브러리의 버전이 달라 기동에 실패하면 실행 중인 Python 프로세스를 종료한 뒤 고정 의존성을 재설치한다.

```powershell
python -m pip install --upgrade --target .deps -r requirements-inference.txt
```

승인 모델·평가 산출물은 Git 제외 대상이므로 코드만 복제한 새 PC에서는 배치 실행만으로 모델이 생기지 않는다. 학습·비용·Test 승인 단계의 로컬 산출물이 필요하다. 파일이 없을 때 dummy로 대신 실행하지 않는다.

기본 주소는 `http://127.0.0.1:8000/health`, 요청 명세는 `http://127.0.0.1:8000/docs`다. 8000이 사용 중이면 기존 본인 서버를 종료하거나 `--port 8001`로 별도 API를 실행한다. 이때 C++의 목적지 포트까지 자동으로 바뀌지는 않는다.

## 실제 HTTP 자동 검사

```powershell
python -X utf8 .\evaluation\verify_approved_rf_api.py
```

사용 가능한 로컬 포트에서 검사기가 소유한 Uvicorn을 실행한다. 수작업 snapshot 3개의 응답을 직접 모델 계산과 비교하고, summary 미지원·잘못된 입력 거부·수집 비활성 상태를 확인한 뒤 stdin quit으로 정상 종료한다. 다른 서버를 종료하지 않는다.

결과는 `reports/operational_rf_api.json/.md`, 로그는 `artifacts/inference_api/rf_v1/http_smoke_v1/`이다. 기존 결과는 덮어쓰지 않으므로 재실행에는 새로운 `--artifact-dir`와 `--report-prefix`를 지정한다. 검사용 서버는 끝나면 종료된다. 직접 사용할 서버는 배치 파일로 실행한다.

이 검사는 전송·계산·응답의 기능 확인이다. 모델이 만든 판단을 정답으로 삼아 성능을 평가하는 작업이 아니다. 데이터셋·공식 Test·holdout 사용 이력을 변경하지 않고 새로운 accuracy·일반화 결과도 주장하지 않는다.

## 실제 검증 결과

새 RF API 테스트 20개를 포함해 전체 357개 테스트가 통과했다. 기존 dummy API·수집 경로도 그대로 통과했다. 실제 승인 묶음의 파일 10개를 검증했고, HTTP 검사에서는 승인 파일과 새 구현·설정 등 21개의 보호 해시를 확인했다.

실제 Uvicorn 검사 결과는 다음과 같다.

| 수작업 요청 | HTTP | 판단 | 이상 점수 |
| --- | ---: | --- | ---: |
| 간격이 있는 snapshot | 200 | normal | 0.000000 |
| 빠른 연속 snapshot | 200 | controlled_anomaly | 0.863468 |
| 첫 메시지 snapshot | 200 | normal | 0.633246 |
| 종료 요약 | 422 | 판단하지 않음 | 해당 없음 |
| 숫자 문자열 feature | 422 | 판단하지 않음 | 해당 없음 |

세 성공 응답은 직접 RF 계산·threshold·label·confidence와 정확히 일치했다. 첫 메시지 예시는 이상 점수가 0.5보다 커도 0.7보다 작으면 정상으로 분류한다는 고정 정책을 보여 준다. 이 snapshot에 독립 정답을 붙여 탐지 성능을 주장하지 않는다.

검사 중 수집 기능은 비활성이었고 종료 명령 후 소유한 Uvicorn 프로세스의 종료 코드는 0이었다. 기존 학습·Test CSV·수집 JSONL에 접근하거나 모델을 재학습하지 않았다. 검사용 서버는 이미 종료했으며 사용자는 배치 파일로 직접 실행한다.

## 한계와 다음 작업

기존 Test의 FN 531건, 잘못된 길이 FN 71/72건, 첫 메시지 Recall 37.04% 한계는 그대로다. 실제 RF를 API에 연결했다고 모델의 탐지력이 개선된 것은 아니다. 자동 차단·외부 서비스 공개·운영 안전 보장은 하지 않는다.

다음은 실제 C++ TCP 요청과 모델 결과 저장을 연결하고 장애·지연·큐 포화·종료·부하를 검증하는 단계다. 아직 이 문서의 HTTP 검사만으로 C++ 종단 동작이나 시스템 p95를 검증했다고 주장하지 않는다.

이 단계가 완료되면 남은 큰 작업 묶음은 **2개**다.

1. 실제 C++ TCP 종단 통합과 장애·부하 검증.
2. 재현 절차·실험 보고서·최종 GitHub README·면접 자료 마무리.
