# 운영 데이터 수집 전 오류 및 입력 검증 보강

## 변경 범위

TCP 수신/송신 구조, observer, bounded queue 및 별도 HTTP worker 책임은 유지한다. 실제 모델 학습이나 inference는 이번 변경에 포함하지 않는다. 정상 입력은 기존 dummy 응답을 반환한다.

운영 feature 개수와 순서는 메시지 7개, 세션 종료 요약 8개로 유지한다. 오류 계수 및 종료 정책이 달라지므로 JSON의 `feature_contract_id`를 필수로 추가한다.

| 항목 | 현재 값 |
| --- | --- |
| HTTP 구조 버전 | schema_version=v1 |
| 메시지 의미 버전 | server_application_message_v2 |
| 종료 요약 의미 버전 | server_application_session_summary_v2 |
| 모델 상태 | dummy-v0 |

구조 버전과 의미 버전은 다르다. 의미 버전이 없거나 과거 v1인 요청은 422로 거부한다. 이전 C++ 실행 파일이나 이전 FastAPI 프로세스를 섞지 않도록 서버 재빌드와 API 재시작을 함께 수행한다. `/health`에서 두 의미 버전을 확인할 수 있다.

## C++ 오류 경로

`Session::ReportProtocolError`는 기존 observer로 오류를 전달하는 protected helper다. HTTP나 feature 계산은 하지 않는다. 파생 `ServerSession`도 동일 형식으로 업무 payload 실패를 알릴 수 있다.

| 파일/함수 | 보강한 오류 |
| --- | --- |
| Session::PostRecv | WSARecv의 즉시 실패 |
| Session::PostSend | 직접 WSASend의 즉시 실패 |
| Session::postNextSend | 큐 기반 WSASend의 즉시 실패 |
| Session::PartialSend | 부분 송신 재요청의 즉시 실패 |
| Session::QueuePacketToWorker | deferred send의 PostQueuedCompletionStatus 실패 |
| Session::ParsePackets | packet ID가 enum 표현 범위인 0~65535를 벗어나는 경우 |
| ServerSession::HANDLE_CP_CHAT | payload 역직렬화 실패 및 남은 바이트 |
| ServerSession::OnPacketReceived | 구현하지 않은 메시지 ID |

기존 IOCP 완료 실패와 프레임 크기 오류 hook은 유지한다. `WSA_IO_PENDING`은 실패로 집계하지 않는다. 즉시 요청 실패는 보통 별도 완료 이벤트가 오지 않으므로 close 전에 오류를 기록한다.

알 수 없는 ID는 payload 유무와 관계없이 한 번 기록하고 연결을 닫는다. 기존처럼 handler 이후 남은 바이트 검사까지 진행하면 같은 거부를 두 번 집계할 수 있어 close 후 즉시 반환한다. 이 동작은 이전에 payload 없는 unknown ID를 그대로 통과시키던 경우보다 엄격하다.

packet ID는 wire에서 int32이지만 enum은 uint16이다. 먼저 범위를 확인해 65536이 0으로 잘려 CS_CHAT로 처리되는 경우를 막는다.

## 종료 snapshot 정책

`FeatureCollector::OnNetworkEvent`는 `is_closed` 상태를 확인한 뒤에만 공통 시각과 counter를 변경한다. 처음 처리한 SessionClosed 시점의 snapshot은 고정하고, 이후 다음 이벤트는 모두 모델 입력에서 제외한다.

- 늦은 RecvCompleted/SendCompleted
- 늦은 ProtocolError/PacketParsed
- 중복 SessionClosed
- 같은 ID의 SessionStarted

제외한 횟수는 `IgnoredAfterCloseEventCount()`로 조회한다. 모델 feature에 새 counter를 추가하지 않는다. `Clear()`는 보관 상태와 해당 운영 통계를 함께 초기화한다.

이 정책은 **close 이벤트 처리 시점**의 요약이다. 모든 OS I/O 완료가 끝날 때까지 기다린 최종 flow 요약이 아니며 실제로 완료됐지만 뒤늦게 observer에 도착한 바이트는 제외될 수 있다.

락을 해제한 뒤 기존 TryPush를 유지해 HTTP나 큐 공간 대기가 I/O 경로에 들어가지 않도록 한다. 따라서 동시 producer의 queue_sequence_id가 이벤트 생성 시간의 완전한 순서를 증명하지는 않는다. 데이터 수집 시 세션/메시지 번호와 관측 시점을 함께 보관한다.

RemoveSession/Clear는 더 이상 해당 I/O가 도착하지 않는 시점에 호출해야 한다. 영구 tombstone이나 자동 세션 보관 상한은 이번에 추가하지 않았으며 삭제 후 임의 이벤트가 들어오는 경우까지 보호한다고 주장하지 않는다.

## Python 요청 검증

`inference_api/payload_validation.py`에 공통 검증을 분리했다. 명세는 프로세스 시작 때 한 번 읽는다. `schemas.py`의 after validator가 필드 검증 후 관계를 검사하고 오프라인 audit도 같은 함수를 사용한다.

검증 항목:

- v2 feature_contract_id와 이벤트 종류 일치
- 숫자 문자열, bool, 정수 위치의 float 자동 보정 금지
- NaN/Infinity 및 음수, C++ 정수 범위 초과 거부
- 정의되지 않은 필드 및 필수 field 누락 거부
- features와 session/message에 반복된 값 일치
- packet_size = payload_size + 8
- 첫 메시지 request_index=1의 간격은 0
- 종료 요약은 시작과 종료가 모두 관측된 상태
- 메시지 0~1개 세션의 평균 간격은 0
- 시간대가 명시된 UTC timestamp 및 양수 session_id

잘못된 입력은 모델 서비스 호출 전에 HTTP 422를 반환한다. NaN/Infinity가 validation 오류 내용에 포함돼도 오류 응답의 JSON 직렬화가 실패하지 않도록 해당 값만 문자열로 표시한다. 실제 입력 값을 정상 수치로 보정하거나 추론에 사용하는 것은 아니다.

이 검증은 형식과 수집 정의를 확인하는 것이지 정상/이상 label의 정확성, monotonic 시각 순서, 재생 요청 방지나 실제 침입 탐지를 보장하지 않는다. 세션 간 시각/누적치 검사는 이후 데이터 수집/품질 검사에서 수행한다.

## 테스트와 실행

Python 의존성 및 테스트:

```powershell
python -m pip install --target .deps -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```

기존 25개와 API 테스트 10개를 포함해 총 35개를 확인한다. 정상 두 이벤트의 dummy 응답과 잘못된 입력 422, 모델 미호출, non-finite 오류 응답 직렬화, 일반 앱의 테스트 전용 경로 미노출을 검사한다.

C++ FeatureCollector 테스트는 `Server/tests/FeatureCollectorTests.vcxproj`를 Debug x64로 빌드해 실행한다. 실제 collector와 queue 코드를 직접 컴파일하며 정상 counter/간격, 종료 후 값 고정, 8개 동시 스레드의 늦은 이벤트 계수, 큐 포화 및 짧은 세션을 검사한다.

실제 연동 테스트는 AI 폴더에서 실행한다.

```powershell
python .\tests\run_runtime_e2e.py
```

127.0.0.1의 4000/8000 포트가 비어 있어야 한다. Debug x64 Server.exe와 테스트 FastAPI 프로세스를 시작하고 로컬 TCP 클라이언트로 다음을 검사한다.

| 시나리오 | 메시지 snapshot | 종료 오류 수 |
| --- | --- | --- |
| 정상 CS_CHAT | 1 | 0 |
| 잘못된 CS_CHAT payload | 1 | 1 |
| CS_CHAT trailing bytes | 1 | 1 |
| unknown ID + 빈 payload | 1 | 1 |
| unknown ID + payload | 1 | 1 |
| 헤더보다 작은 frame size | 0 | 1 |
| 수신 버퍼보다 큰 frame size | 0 | 1 |
| enum 범위 초과 ID | 0 | 1 |

메시지는 업무 payload 검증보다 먼저 생성되므로 payload 실패의 message.error_count_total은 0이고 종료 summary.error_count는 1이다. 이 차이를 미래 정보 누수로 보정하지 않는다. 잘못된 크기/ID가 프레임 파싱 전에 거부되면 메시지 snapshot 자체가 없다는 coverage도 확인한다.

`tests/e2e_api.py`의 조회 경로는 테스트 프로세스에만 추가한다. 일반 `inference_api.main:app`에는 없다. 테스트는 직접 만든 프로세스만 정리하고 C++에는 quit으로 graceful shutdown을 요청한다. 결과는 `reports/runtime_hardening_e2e.json`에 저장하며 학습 데이터나 모델 성능 지표로 사용하지 않는다.

즉시 WSARecv/WSASend 요청 실패를 강제로 유발하는 장애 주입은 이번 실제 연동 테스트에 포함하지 않는다. 해당 hook은 빌드와 코드 검토로 확인했으며 종료 후 동시성은 별도 C++ 테스트로 검사한다.

## 다음 단계

v2 JSON을 비동기 bounded 수집 큐를 통해 JSONL로 기록하는 모듈을 구현한다. 원본 snapshot과 run_id, scenario_id, 서버 빌드 식별자, 의미 버전, session/request 식별자를 보관하고 label은 독립 manifest에서 관리한다. 수집 유실과 종료 flush 정책을 함께 구현한 뒤 정상/통제된 이상 데이터를 여러 실행으로 모아 운영 LR/RF 학습을 진행한다.
