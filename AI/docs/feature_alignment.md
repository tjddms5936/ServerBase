# 학습 입력과 운영 feature 정합성 설계

## 결론과 적용 범위

UNSW-NB15 baseline은 공개 네트워크 데이터의 오프라인 연구 실험으로 유지한다. 현재 C++ 서버의 애플리케이션 메시지/소켓 완료 이벤트 입력을 UNSW 모델에 그대로 전달하지 않는다. 누락된 42개 원본 입력을 0으로 채우거나 비슷한 이름으로 치환하지 않는다.

현재 운영 경로의 1차 목표는 **이 TCP 서버 프로토콜의 메시지 행동 이상 탐지**다. 공개 데이터 연구 실험과 운영 입력 기반 실험은 서로 다른 데이터 영역이며, 성능을 하나의 숫자로 합치지 않는다. 통제된 실험에서 만든 이상 행동 탐지는 실제 침입이나 UNSW의 9개 공격 유형 탐지를 증명하지 않는다.

초기 정합성 검토 이후 오류 경로, 종료 snapshot 및 API 검증을 보강했다. 현재 의미 버전은 v2이며 입력은 여전히 메시지 7개와 종료 요약 8개다. `DummyModelService`는 계속 dummy 응답을 반환한다. 공통 검증 함수는 현재 `/predict`에도 적용된다. 보강한 코드와 실행 검증은 `runtime_hardening.md`에 정리한다.

## 왜 같은 값으로 취급할 수 없는가

UNSW 공식 안내는 패킷 캡처와 Argus/Bro 및 별도 알고리즘을 이용한 feature 생성을 설명한다. 현재 서버는 네트워크 패킷 캡처가 아니라 `WSARecv`/`WSASend` 완료와 자체 프레임 파싱을 관측한다. [UNSW 공식 데이터셋 안내](https://research.unsw.edu.au/projects/unsw-nb15-dataset)

예를 들어 TCP는 바이트 스트림이므로 하나의 애플리케이션 프레임이 여러 수신 완료로 나뉘거나, 여러 프레임이 한 수신 완료에 포함될 수 있다. 따라서 `recv_event_count`, `request_count`, UNSW의 `spkts`는 서로 다른 개념이다.

| 비교 항목 | UNSW 입력 | 현재 서버 입력 | 동등하지 않은 이유 |
| --- | --- | --- | --- |
| 시간 | dur | connection_duration_ms | 흐름/기록 경계와 SessionStarted 이후 관측 경계가 다름 |
| 방향별 바이트 | sbytes, dbytes | bytes_received_total, bytes_sent_total | 계층, source/destination 방향, 관측 시점 및 계수 규칙이 다름 또는 미확인 |
| 패킷 수 | spkts, dpkts | recv_event_count, send_event_count | TCP 패킷과 IOCP 완료는 일대일 관계가 아님 |
| 간격 | sinpkt, dinpkt | message_interval_ms | 패킷 도착 간격과 애플리케이션 파싱 처리 간격이 다름 |
| 크기 | smean, dmean | packet_size, payload_size | 흐름 평균 패킷 크기와 개별 메시지 크기가 다름 |
| 서비스/상태 | service, state | packet_id, is_closed | 서비스 판별 및 추출기 상태와 자체 프로토콜 상태가 다름 |
| 주변 연결 | ct_* | session별 counter | 여러 흐름의 이력 집계와 단일 세션 누적치가 다름 |

서버 입장에서 received는 클라이언트에서 서버로, sent는 서버에서 클라이언트로 향한다. UNSW의 source를 무조건 클라이언트라고 가정하지 않는다. 제공받은 train/test CSV에는 원본 IP/port와 시간/세션 식별자가 없어 임의의 행을 현재 서버 세션이나 시간 순서로 재구성할 수 없다.

설명 CSV는 원본 feature 이름과 배포 train의 이름이 일부 다르다. 검사 도구는 `smeansz -> smean`, `dmeansz -> dmean`, `res_bdy_len -> response_body_len`, `Sintpkt -> sinpkt`, `Dintpkt -> dinpkt`, 공백과 대소문자 차이를 정규화한다. `rate` 등 설명 파일에 없는 열은 설명 미존재로 남긴다. 의미 변환이나 모델 입력 변환을 수행하는 이름 변경은 아니다.

`dur` 설명은 기록 전체 지속 시간이라고만 되어 있어 이 파일만으로 단위 및 추출 공식을 모두 확정할 수 없다. 추출기 문서를 확인하기 전 단위 변환을 확정하지 않는다. 시간을 맞춰도 흐름 경계가 달라 호환성이 해결되는 것은 아니다.

## 운영 입력 명세

기계가 읽는 명세는 `configs/operational_feature_schema.json`이다. HTTP `schema_version=v1`과 feature 이름, 순서는 유지한다. 오류 계수와 종료 정책 변경을 구분하는 `feature_contract_id`는 이제 C++ JSON에 포함하며 FastAPI가 v2 값만 허용한다. v1 의미 버전 또는 해당 필드가 없는 과거 입력은 거부하고 학습 데이터와 섞지 않는다.

### 메시지: server_application_message_v2

- 관측 범위: `session_prefix_at_frame_parsed`
- 생성 시점: `Session::ParsePackets`에서 완성된 프레임 및 헤더 파싱 후 `OnPacketReceived` 호출 전
- 학습/추론 입력 순서: 아래 7개로 고정

| 순서 | 입력 | 단위 | 계산과 의미 |
| --- | --- | --- | --- |
| 1 | packet_size | byte | 자체 PacketHeader를 포함한 애플리케이션 프레임 크기 |
| 2 | payload_size | byte | packet_size - sizeof(PacketHeader); 현재 헤더는 int32 두 개로 8바이트 |
| 3 | connection_duration_ms | ms | 현재 단조 시각 - SessionStarted 단조 시각 |
| 4 | message_interval_ms | ms | 현재와 직전 PacketParsed의 단조 시각 차이; 첫 메시지는 0 |
| 5 | bytes_received_total | byte | 이 시점까지 관측한 RecvCompleted의 bytes_transferred 합 |
| 6 | bytes_sent_total | byte | 이 시점까지 관측한 SendCompleted의 bytes_transferred 합 |
| 7 | error_count_total | count | 이 시점까지 전달된 ProtocolError 이벤트 수 |

`packet_size`의 이름은 현재 wire 호환성을 위해 유지하지만 의미는 application frame size다. payload에는 문자열 길이 필드나 정수 필드 등 프로토콜 직렬화 내용도 포함될 수 있다. HTTP 본문 길이나 TCP/IP 패킷 크기로 해석하지 않는다.

여러 프레임을 한 수신 완료에서 파싱하면 누적 수신 바이트가 같고 간격은 거의 0일 수 있다. 이는 현재 정의에서 유효한 관측이다. 프레임별 바이트율을 계산할 때 전체 누적치를 현재 프레임의 바이트로 취급하지 않는다.

현재 프레임이 업무 처리 단계에서 실패해도 이미 생성한 message snapshot에는 그 실패가 반영되지 않는다. 학습 시 해당 시점에 가용한 입력만 사용하고 미래의 error_count나 종료 summary를 끌어오지 않는다.

### 세션 종료 요약: server_application_session_summary_v2

- 관측 범위: `session_snapshot_at_socket_close`
- 생성 시점: `SessionClosed` 처리 시점; 현재는 주기적으로 전송하지 않음
- 종료 시점 판단이므로 메시지 모델의 실시간 판단과 구분

| 순서 | 입력 | 단위 | 계산과 의미 |
| --- | --- | --- | --- |
| 1 | connection_duration_ms | ms | 시작부터 socket close 이벤트까지 |
| 2 | bytes_received_total | byte | 종료 snapshot 시점까지 관측한 수신 완료 바이트 합 |
| 3 | bytes_sent_total | byte | 종료 snapshot 시점까지 관측한 송신 완료 바이트 합 |
| 4 | recv_event_count | count | 양수 수신 완료 이벤트 수 |
| 5 | send_event_count | count | 송신 완료 이벤트 수; 부분 송신 포함 |
| 6 | request_count | count | PacketParsed 이벤트 수; 업무 요청 성공 수가 아님 |
| 7 | error_count | count | 관측한 ProtocolError 이벤트 수 |
| 8 | avg_message_interval_ms | ms | 간격 합 / (request_count - 1); 메시지가 0~1개이면 0 |

송신 완료는 원격 애플리케이션 수신이나 TCP ACK 확인을 의미하지 않는다. close 이후 지연된 이벤트는 snapshot을 바꾸지 않고 `IgnoredAfterCloseEventCount` 운영 통계로만 센다. 따라서 summary를 모든 I/O가 끝난 최종 flow 레코드라고 표현하지 않는다. RemoveSession/Clear는 더 이상 이벤트가 도착하지 않는 시점에 호출한다.

메시지와 종료 요약은 입력 개수뿐 아니라 관측 범위가 다르다. 하나의 모델에 섞지 않고 별도 데이터와 별도 산출물을 사용한다. 1차 운영 학습은 메시지 모델이며 종료 요약은 우선 기록/분석 용도로 사용한다.

### 메타데이터와 값 처리

- session_id, socket_handle, logic_worker_id, queue_sequence_id, timestamp, request_index, packet_id, 오류 문구는 기본 모델 입력에서 제외한다.
- 이후 패킷 종류가 필요하면 별도 실험과 새 contract로 추가한다. 현재 숫자 ID를 공격의 순서나 크기처럼 사용하지 않는다.
- session_id는 프로세스 재시작 후 충돌할 수 있으므로 수집 파일에서 run_id와 함께 사용한다.
- 시간 간격은 steady_clock으로 계산하고 로그 timestamp는 UTC wall clock으로 기록한다. 두 시계의 절대값을 혼합하지 않는다.
- C++ 직렬화는 소수점 3자리이므로 학습 데이터도 전송된 JSON의 값으로 수집한다.
- 모든 입력은 유한한 0 이상 수치이며 count/byte 입력은 정수다. 누락/잘못된 값은 거부하고 0으로 보정하지 않는다.
- 첫 메시지 간격 0, 메시지 0~1개 세션의 평균 간격 0은 명시적 규칙이므로 결측값과 구분한다.
- features와 session/message에 반복된 값은 일치해야 한다. 현재 FastAPI는 이 관계와 프레임 크기, 첫 간격, 짧은 세션의 평균 간격까지 검사한다.
- 정의되지 않은 필드, 문자열/bool 숫자 보정, NaN/Infinity, C++ 정수 범위를 벗어난 값은 HTTP 422로 거부한다.

## 호환성 검사와 재현 방법

```powershell
python .\evaluation\audit_feature_alignment.py
python -m unittest discover -s tests -p "test_*.py"
```

검사 도구는 다음을 수행한다.

1. 원본 train CSV의 헤더에서 id와 정답을 제외한 42개 입력을 읽는다. 공식 test와 학습 데이터 행은 읽지 않는다.
2. 42개 입력이 정합성 명세에 빠짐없이 한 번씩 분류되는지 검사한다.
3. 현재 FastAPI feature 모델의 필드 이름과 타입, transformer의 입력 순서가 명세와 일치하는지 확인한다.
4. 설명 CSV의 인코딩과 이름 차이를 처리하고 모델 schema를 비교한다.
5. JSON/한글 보고서와 근거 코드의 SHA-256을 남긴다. 코드를 자동 해석해서 C++ 의미를 증명하는 검사는 아니다.

현재 UNSW 모델은 원본 입력 42개를 필요로 하며, validation 전처리 후 모델 입력은 192개다. 운영의 7개 또는 8개는 원본 입력 단계부터 맞지 않는다. 전처리 차원만 맞추는 것은 해결책이 아니다.

운영 모델 schema에는 원본 feature의 정확한 순서와 함께 아래 항목이 필요하다.

```json
{
  "feature_contract_id": "server_application_message_v2",
  "wire_schema_version": "v1",
  "capture_layer": "application_socket_completion_and_message_parser",
  "observation_scope": "session_prefix_at_frame_parsed",
  "event_type": "message"
}
```

추가로 `raw_input_features`와 각 입력의 `feature_units`를 저장한다. 이름이 같아도 단위, 관측 범위, 의미 버전이 다르면 schema 호환으로 판정하지 않는다. schema가 호환이어도 모델 성능, 전처리 방식, 수집 분포와 threshold 검증은 별도 필요하다.

```powershell
python .\evaluation\audit_feature_alignment.py --require-compatible
python .\evaluation\audit_feature_alignment.py --model-schema .\artifacts\operational_message\feature_schema.json --event-type message --require-compatible
```

현재 UNSW schema를 대상으로 첫 명령을 실행하면 종료 코드 2가 정상적인 비호환 결과다. 두 번째 명령은 미래 운영 모델 산출물의 경로 예시이며 아직 그 모델은 생성하지 않았다. 기본 명령은 비호환 사실을 보고서에 남기고 정상 종료한다.

## 배포 경로 선택

| 경로 | 장점 | 비용과 제한 | 이번 결정 |
| --- | --- | --- | --- |
| 애플리케이션 입력으로 별도 학습 | 기존 서버 책임과 이벤트 구조 유지, 현재 입력으로 수집 가능 | 자체 정상/이상 데이터와 분할 설계 필요, 실제 침입 탐지 주장 제한 | 1차 운영 경로 |
| 패킷 캡처 및 호환 flow 추출기 추가 | 공개 flow 데이터 기반 모델과 연결할 가능성 | 추출기 규칙/버전, 방향, timeout, 온라인 prefix와 완료 flow 차이까지 검증 필요 | 추후 별도 확장 |
| 빠진 UNSW 입력을 0 또는 비슷한 값으로 채움 | 겉으로는 입력 차원을 맞출 수 있음 | 의미와 분포가 어긋나 baseline 성능의 근거가 사라짐 | 사용하지 않음 |

UNSW PCAP을 자체 메시지 파서에 넣으면 같은 학습 데이터가 생긴다는 가정도 하지 않는다. 대상 애플리케이션 프로토콜이 다르다. 공개 데이터의 패킷 수준 연구를 이어가려면 별도 수집/추출 경로에서 검증한다.

## 다음 구현의 수정 후보와 우선순위

### P0: 완료한 오류 경로 및 검증 보강

| 파일/함수 | 현재 상태 | 제안 |
| --- | --- | --- |
| ServerBase/Session.cpp: PostRecv, PostSend, postNextSend, PartialSend, QueuePacketToWorker | 즉시 요청 실패의 오류 알림 추가 | 오류 알림 후 기존 close 유지 |
| Server/ServerSession.cpp: HANDLE_CP_CHAT, OnPacketReceived | payload 실패 및 unknown ID 오류 알림 추가 | unknown ID는 한 번 기록하고 즉시 close |
| ServerBase/Session.cpp: ParsePackets | ID 범위 확인 추가 | enum으로 변환하며 ID가 잘리는 경우 차단 |
| FeatureExtraction/FeatureCollector.cpp: OnNetworkEvent | close 시점 snapshot 고정 | 종료 뒤 이벤트는 별도 운영 통계로만 계수 |
| inference_api/schemas.py, payload_validation.py, main.py | strict 수치 및 snapshot 관계 검증 적용 | 잘못된 입력은 모델 호출 전 HTTP 422 |

오류 계수 규칙이 바뀌어 `server_application_message_v2`와 `server_application_session_summary_v2`로 의미 버전을 올렸다. 단위와 feature 개수는 같지만 변경 전 v1 데이터와 무조건 합치지 않는다. 빌드된 C++ 서버와 재시작한 FastAPI의 버전을 함께 맞춘다.

### P1: 같은 입력 정의로 데이터 수집과 운영 모델 학습

1. Python에 비동기/별도 worker 기반 JSONL 수집기를 추가하고 요청 경로에서 파일 I/O 비용을 제한한다. 수집 큐 유실도 측정한다.
2. 수신 원본 JSON, run_id, scenario_id, session_id, request_index, collector contract 버전, 서버 빌드 식별자를 함께 기록한다.
3. label은 독립적인 시나리오 manifest에서 가져온다. error_count나 기존 dummy 판단으로 정답을 만들지 않는다.
4. 정상 시나리오는 다양한 메시지 크기/주기/연결 지속 시간을 포함한다. 이상 시나리오는 로컬 테스트의 과도한 메시지 빈도, 비정상 크기, malformed frame 등으로 한정한다.
5. malformed frame이 헤더 파싱 전에 거부되면 message snapshot이 없을 수 있다. 해당 유형은 summary/오류 기록의 coverage를 따로 평가하며 메시지 모델이 탐지했다고 주장하지 않는다.
6. run/session 단위로 train/validation/test를 분리한다. 같은 세션의 누적 snapshot이 양쪽에 들어가지 않도록 한다. 가능하면 후속 실행을 test로 보관한다.
7. 운영 입력으로 LR/RF를 직접 학습하고, 운영 validation에서 목표 FPR와 threshold를 새로 선정한다. UNSW에서 고른 threshold를 그대로 가져오지 않는다.
8. 이 데이터에서도 Recall/FPR, 실제 정상 요청의 오탐, 종단 지연시간, 메모리와 수집 유실을 비교한다. 합성 시나리오 범위 밖 일반화는 미검증으로 남긴다.

### P2: 실제 모델 연결과 sequence 실험

- feature_contract_id, feature 순서/단위, 전처리기, label map, threshold를 모델 산출물에 함께 저장하고 FastAPI 로딩 시 검증한다.
- 메시지 모델만 우선 /predict의 message 경로에 연결한다. 종료 요약은 별도 모델이 생기기 전 dummy/분석 상태를 명확히 표시한다.
- 일정한 시간 창 또는 순서 창을 실제 run/session 시계열에서 만들고 LSTM과 비교한다. 세션 경계를 넘거나 임의 UNSW 행을 이어 붙이지 않는다.
- 연결, 파싱, bounded queue, HTTP worker 책임은 기존 모듈에 유지한다. AI 통신이나 학습 로직을 Session I/O 처리에 넣지 않는다.

## 확인한 근거

- ServerBase/Session.cpp: Start, OnRecv_v2, OnSend2, ParsePackets, CloseSocket
- ServerBase/Protocol.h: PacketHeader
- FeatureExtraction/FeatureCollector.cpp: 각 이벤트 handler와 간격/누적 계산
- FeatureExtraction/AiInferenceWorker.cpp: BuildMessageJson, BuildSessionJson
- Server/ServerSession.cpp: payload 및 업무 처리 실패 경로
- inference_api/schemas.py, feature_transformer.py: 현재 JSON과 입력 순서
- 로컬 UNSW_NB15_features.csv 및 train CSV 헤더
- [UNSW 공식 데이터셋 안내](https://research.unsw.edu.au/projects/unsw-nb15-dataset)
