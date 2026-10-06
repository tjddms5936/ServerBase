# 운영 feature 비동기 수집

## 이번 단계의 목적

C++에서 이미 추출한 v2 feature를 Python에서 기록하는 단계다. C++ `FeatureCollector`는 이벤트를 feature로 만드는 역할이고, 새 Python `AsyncJsonlCollector`는 그 JSON을 학습 후보 원본으로 보관하는 역할이다. C++ 소켓 처리, HTTP 전송 worker, 응답 형식은 변경하지 않는다. 실제 모델 학습이나 추론 연결은 아직 하지 않는다.

```text
C++ Session → FeatureCollector → AsyncFeatureQueue → AiInferenceWorker
                                                       │ HTTP POST
                                                       ▼
FastAPI 입력 검증 → feature vector → dummy 응답
       │ 검증한 snapshot 복사 / put_nowait
       ▼
bounded 메모리 큐 → 별도 JSONL writer thread → features.jsonl
                                             run_manifest.json
                                             collection_summary.json
```

파일 생성과 manifest 읽기는 API 시작 시 한 번 수행한다. 요청 처리 중에는 snapshot 복사와 짧은 큐 접근만 한다. JSONL 직렬화, 파일 쓰기, 주기적 flush는 writer에서 수행한다. 큐가 꽉 차면 기다리지 않고 버리며, 저장 실패도 정상적인 dummy 응답을 바꾸지 않는다. 이는 lock-free나 하드 실시간 보장은 아니다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `data_collection/__init__.py` | 별도 Python 수집 패키지 선언 |
| `data_collection/config.py` | 실행 설정, ID·label 검증, 독립 시나리오 manifest 읽기 |
| `data_collection/collector.py` | bounded queue, writer thread, 통계, 종료 시 큐 배출과 flush |
| `configs/collection_scenarios.json` | 트래픽 발생 전에 정의하는 시나리오와 정답 |
| `scripts/run_collection_api.py` | 빌드 파일 해시·새 run ID를 구성하고 단일 worker API 실행 |
| `inference_api/main.py` | API 시작/종료 수명주기, 검증된 입력 큐 전달, 수집 상태 조회 |
| `tests/test_data_collection.py` | 동시성, 포화, 파일 오류, label 독립성, API 수명주기 검사 |
| `tests/collection_e2e_api.py` | 테스트 소유 API를 정상 종료하기 위한 표준 입력 제어. 일반 API에는 제어 경로를 추가하지 않음 |
| `tests/run_collection_e2e.py` | 실제 C++/HTTP/JSONL 연동 및 종료 flush 검사 |
| `reports/data_collection_e2e.json` | 연동 확인 보고서. 학습 데이터나 모델 성능 지표가 아님 |

## 실행 단위와 독립 정답

수집 실행 하나는 **C++ 프로세스 실행 하나와 시나리오 하나**에 대응한다. 서버를 재시작하거나 시나리오를 바꾸면 API 수집도 종료하고 새 run으로 시작한다. 같은 `run_id`가 이미 있으면 덮어쓰거나 이어 쓰지 않고 시작을 거부한다. `run_id`는 경로 구분자를 허용하지 않는 최대 96자의 영문·숫자·밑줄·하이픈 ID다.

세션 식별 키는 `(run_id, session_id)`, 메시지 식별 후보는 `(run_id, session_id, request_index)`다. C++의 `session_id`는 프로세스 재시작 후 재사용될 수 있으므로 단독 키로 쓰면 안 된다. `collection_sequence_id`는 수집 큐 진입 시도 순번이며 실제 네트워크 발생 순서가 아니다. 모델 입력에 run/session/시나리오 ID나 수집 순번을 포함하지 않는다.

정답은 `collection_scenarios.json`에 미리 정의한다.

- `normal_chat`: label 0, 정상 채팅 프레임으로만 구성한 실행.
- `malformed_chat_payload`: label 1, 잘못된 채팅 본문으로만 구성한 통제된 이상 실행.

이 label은 `error_count`, FastAPI의 dummy `label`, 추후 모델 예측으로 생성하지 않는다. 시작 시 manifest 내용과 SHA-256을 `run_manifest.json`에 복사해, 원본 manifest를 나중에 바꿔도 수집 당시 정의가 남는다. label은 feature JSON 안이 아니라 `selected_scenario`에 보관하며 각 레코드는 이 정의를 참조한다.

**label 1은 실제 공격이 검증되었다는 뜻이 아니다.** 현재 label은 시나리오 수준 의도다. 한 실행에 정상·비정상 트래픽을 섞으면 이 방식으로 메시지별 정답을 표현할 수 없다. 그런 실험에는 독립적인 세션/메시지별 manifest를 추가해야 한다. 현재 메시지는 payload 검증 전 snapshot이므로 이후 발생한 오류가 메시지 입력에 반영되지 않는 것이 정상이다.

## 실행 방법

먼저 기존 8000 포트의 FastAPI와 4000 포트의 TCP 서버를 종료한다. AI 폴더에서 다음을 실행한다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\run_collection_api.py --scenario-id normal_chat
```

스크립트는 기존 `AI/.deps`도 인식한다. 의존성이 없다면 먼저 `python -m pip install --target .deps -r requirements.txt`를 실행한다. API 시작 후 화면에 표시된 정확한 `Server.exe`를 실행하고 정상 클라이언트를 연결한다. 기본 빌드는 `Server/BIN/Debug/Server.exe`이며 다른 빌드는 `--server-exe`로 지정한다. 기존 `run_fastapi.bat`는 별도 수집 설정을 지정하지 않는 한 수집하지 않는다.

잘못된 payload 시나리오는 전용 클라이언트가 필요하다. 일반 DummyClient를 실행하면 정상 트래픽에 비정상 정답을 붙이게 되므로 대신 아래 연동 테스트로 확인한다.

```powershell
python .\tests\run_collection_e2e.py
```

종료 순서:

1. 클라이언트를 종료하고 C++ 서버 콘솔에서 `quit`를 입력한다.
2. HTTP 전송 worker가 종료될 때까지 기다린다.
3. API 콘솔에서 `Ctrl+C`를 눌러 큐 배출·최종 flush를 수행한다.
4. `collection_summary.json`의 `complete`, 오류·유실 수를 확인한다.

한 수집 실행에서는 uvicorn worker를 1개만 사용한다. `--reload`나 여러 API worker는 같은 run 경로를 공유하므로 지원하지 않는다. 실행 중 C++ 프로세스 재시작이나 여러 C++ 서버의 혼합은 지원하지 않으며 자동 탐지하지 않는다. 빌드 SHA-256도 지정한 실행 파일의 해시이지 원격 송신자의 신원을 인증하는 값은 아니다. 현재 API는 127.0.0.1에서 통제된 테스트용으로만 사용한다.

고급 설정은 `NDA_COLLECTION_CONFIG` 환경변수로 JSON 파일 경로를 지정한다. 경로 필드는 그 설정 파일 위치 기준으로 해석한다. 필수 필드는 `run_id`, `scenario_id`, `server_build_id`(`sha256:` + 64자리 소문자 해시), `scenario_manifest`, `dataset_purpose`다. 선택 필드는 `output_dir`, `queue_capacity`(기본 1024), `flush_interval_s`(0.5초), `shutdown_timeout_s`(10초), `verbose_payload_logging`(false)다. 수집 실행에서는 기본적으로 요청 본문 출력도 꺼 반복 콘솔 출력 비용을 줄인다. 설정·manifest 오류나 이미 존재하는 run은 시작 시 실패하므로 운영 도중의 파일 오류와 구분한다.

## 저장 형식

```text
data/raw/operational/<run_id>/
  features.jsonl             한 줄에 message 또는 session_summary 원본 하나
  run_manifest.json          실행 설정, 시나리오 정답, 빌드 및 의미 schema 해시/복사본
  collection_summary.json    종료 시 저장·유실·오류 통계
```

각 JSONL 줄은 다음 envelope를 갖는다. 원본 v2 JSON은 `payload` 안에 변경 없이 보관한다. message와 session_summary는 원래 `payload.event_type`과 의미 버전을 유지한다.

```json
{
  "record_schema_version": "v1",
  "collection_sequence_id": 1,
  "received_at_utc": "2026-10-06T00:00:00+00:00",
  "run_id": "run_example",
  "scenario_id": "normal_chat",
  "server_build_id": "sha256:실제_64자리_해시",
  "session_id": 1,
  "request_index": 1,
  "label_reference": "run_manifest.json:selected_scenario",
  "payload": {}
}
```

위 `payload` 빈 객체와 해시 문자열은 envelope 설명용 자리 표시자다. 실제 저장에는 API가 검증한 전체 JSON과 실제 해시가 들어간다. 종료 요약의 `request_index`는 `null`이다. `payload.timestamp`는 C++ snapshot 시각이고 `received_at_utc`는 API 수집 시각이다. 예측 결과는 저장하거나 정답으로 사용하지 않는다. 원본 데이터는 `.gitignore` 규칙으로 Git에서 제외한다.

## 통계와 종료 정책

`GET http://127.0.0.1:8000/collection/status`는 메모리 통계만 조회한다. 수집을 끈 실행은 `enabled: false`다. API 추론 건강 상태와 수집 실패 상태는 별도로 확인한다.

| 항목 | 의미 |
| --- | --- |
| `attempted` | 검증된 snapshot의 수집 호출 수 |
| `enqueued` | 큐에 들어간 레코드 수 |
| `written` | 파일 객체에 한 줄 전체 쓰기가 성공한 수. 아직 버퍼에 있을 수 있음 |
| `flushed` | 성공한 flush 시점까지의 written 수. 전원 차단 시 영속성은 보장하지 않음 |
| `dropped_full` | bounded queue가 가득 차 버린 수 |
| `dropped_closed` | 수집 종료 후 호출되어 버린 수 |
| `dropped_failed` | writer 실패가 확인된 뒤 새로 들어와 버린 수 |
| `failed_records` | 한 줄 쓰기에 실패한 레코드 및 실패 후 비운 대기 레코드 수 |
| `pending`, `in_flight`, `unfinished` | 대기, 처리 중, 아직 쓰기 성공/실패가 확정되지 않은 수 |
| `queue_high_watermark` | qsize로 관측한 최대 대기량. 동시 소비 때문에 실제 순간 최대값보다 작을 수 있음 |
| `write_error`, `summary_error` | 저장 경로 오류 또는 최종 통계 파일 오류 |
| `shutdown_complete`, `shutdown_timed_out` | 오류 없이 파일을 닫았는지, 종료 대기 제한 시간을 넘었는지 |

정상적인 집계 관계는 `attempted = enqueued + dropped_full + dropped_closed + dropped_failed`다. `enqueued = written + failed_records + unfinished`다. 쓰기 실패가 일부 줄이나 버퍼 flush에서 생기면 파일 끝에 불완전한 줄이 남거나 written이 flushed보다 클 수 있으므로 해당 run을 완전한 학습 데이터로 간주하지 않는다.

종료 시 먼저 신규 수집을 막고 이미 들어간 큐를 끝까지 배출한다. 마지막에 flush·close를 수행하고 통계를 기록한다. `complete: true`는 **수집기에 들어온 시도에 대해** 유실·쓰기 오류·종료 시간 초과가 없었다는 뜻이다. C++ 큐 포화, HTTP 전송 실패, API 입력 422로 거부된 요청처럼 수집기 이전의 유실까지 없었다는 보장은 아니다. C++ 자체 통계와 별도 송신 기대 수 검증이 필요하다.

`Ctrl+C`에 의한 정상 종료와 달리 작업 관리자 강제 종료·프로세스 충돌·전원 차단은 배출이나 summary 저장을 보장하지 않는다. flush는 `fsync`와 다르며 디스크 영속성을 확약하지 않는다. 파일 I/O가 멈춰 worker 종료 제한 시간을 넘으면 정상 종료로 주장하지 않는다. 이때 daemon writer를 안전하게 강제 중단할 수 없고 summary가 없을 수도 있으므로 누락된 summary도 불완전 run으로 처리한다. 큐는 레코드 **개수**로 제한하며 HTTP 본문 크기나 저장 디스크 총량을 제한하는 기능은 아직 없다.

## 검증과 다음 단계

```powershell
python -m unittest discover -s tests -p "test_*.py"
python .\tests\run_collection_e2e.py
```

연동 테스트는 정상/비정상 실행 각각에서 C++와 API를 새로 시작하고, 독립 정답·JSONL 두 건·정상 종료를 확인한다. 원본은 임시 폴더에만 생성 후 제거하며 `dataset_purpose: integration_test`로 표시한다. 이 네 건을 모델 학습 데이터로 사용하지 않는다.

다음 작업은 **정상·통제된 이상 트래픽 생성기와 다중 run 수집 계획**이다. 메시지 수·크기·간격·동시 연결 수를 변화시키고 run/session 단위 분리, label/의미 버전/종료 요약 누락·중복 검사 규칙을 마련한다. `integration_test` 실행과 불완전 run을 제외하고 여러 수집 실행의 분포를 확인한 뒤 운영 LR/RF를 학습한다. 특히 오류 수 하나로 정답이 구분되는 단순한 사후 지름길과 종료 요약을 실시간 메시지 판단 입력에 섞는 누출을 점검해야 한다. 공개 UNSW 모델은 계속 별도 오프라인 연구 결과로 유지한다.
