# 운영 feature와 UNSW-NB15 입력 정합성 검사

이 보고서는 입력 의미와 schema 호환성을 검사한다. 모델을 학습하거나 실제 추론 서버에 연결하지 않는다.

- UNSW 원본 입력: 42개; 공식 train의 헤더만 확인
- 운영 메시지 입력: 7개
- 운영 세션 종료 요약 입력: 8개
- 현재 FastAPI의 필드 이름·순서·수치 타입과 운영 명세의 일치 검사 통과
- C++ 수집 의미는 코드 검토 기반이며, 자동 검사로 TCP 도착 시각이나 I/O 완료 순서까지 증명한 것은 아님

## 모델 schema 호환성

### message

schema 호환: 아니오

- 모델의 원본 입력 이름 또는 순서가 운영 입력과 다릅니다.
- 모델 schema의 feature_contract_id 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 wire_schema_version 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 capture_layer 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 observation_scope 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 event_type 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 feature_units 정의가 없거나 운영 명세와 다릅니다.

### session_summary

schema 호환: 아니오

- 모델의 원본 입력 이름 또는 순서가 운영 입력과 다릅니다.
- 모델 schema의 feature_contract_id 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 wire_schema_version 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 capture_layer 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 observation_scope 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 event_type 정의가 없거나 운영 명세와 다릅니다.
- 모델 schema의 feature_units 정의가 없거나 운영 명세와 다릅니다.

schema가 맞아도 탐지 성능, threshold 적합성, 분포 차이 검증은 별도로 필요하다. 이 검사에서 비호환으로 나와도 기본 실행은 보고서를 정상 생성하고 종료한다. `--require-compatible` 옵션은 검사 대상 `message`의 schema가 비호환 또는 미존재이면 종료 코드 2를 반환한다. `--event-type`으로 메시지 또는 종료 요약 모델을 각각 검사한다.

## 운영 입력 명세

### message: server_application_message_v2

실시간 애플리케이션 메시지 이상 탐지의 입력 후보

관측 시점: Session::ParsePackets에서 헤더 파싱 후, OnPacketReceived 호출 전

| 순서 | 입력 | 타입 | 단위 | 의미 |
| --- | --- | --- | --- | --- |
| 1 | packet_size | integer | byte | 헤더를 포함한 수신 애플리케이션 프레임 크기. IP/TCP 패킷 크기가 아니다. |
| 2 | payload_size | integer | byte | packet_size에서 현재 PacketHeader 크기 8바이트를 제외한 값. 문자열 본문 크기와도 다를 수 있다. |
| 3 | connection_duration_ms | number | ms | SessionStarted부터 현재 이벤트까지 steady_clock으로 계산한 시간. TCP handshake 시간은 제외된다. |
| 4 | message_interval_ms | number | ms | 이전 PacketParsed와 현재 PacketParsed의 처리 시각 차이. 첫 메시지는 0이며 패킷 도착 간격이 아니다. |
| 5 | bytes_received_total | integer | byte | 현재까지 관측한 RecvCompleted의 바이트 합. 애플리케이션 스트림 바이트이며 TCP/IP 헤더 및 재전송을 직접 계수하지 않는다. |
| 6 | bytes_sent_total | integer | byte | 현재까지 관측한 SendCompleted의 바이트 합. 원격 애플리케이션 수신이나 TCP ACK 확인을 뜻하지 않는다. |
| 7 | error_count_total | integer | count | 메시지 snapshot 이전까지 전달된 ProtocolError 이벤트 수. 활성 I/O 요청/완료 및 업무 payload 오류를 포함하되 현재 메시지의 이후 오류와 종료 후 이벤트는 반영하지 않는다. |

### session_summary: server_application_session_summary_v2

종료 시점 세션 요약의 사후 이상 분석용 입력 후보

관측 시점: Session::CloseSocket의 SessionClosed 이벤트 처리 시점

| 순서 | 입력 | 타입 | 단위 | 의미 |
| --- | --- | --- | --- | --- |
| 1 | connection_duration_ms | number | ms | SessionStarted부터 현재 이벤트까지 steady_clock으로 계산한 시간. TCP handshake 시간은 제외된다. |
| 2 | bytes_received_total | integer | byte | 현재까지 관측한 RecvCompleted의 바이트 합. 애플리케이션 스트림 바이트이며 TCP/IP 헤더 및 재전송을 직접 계수하지 않는다. |
| 3 | bytes_sent_total | integer | byte | 현재까지 관측한 SendCompleted의 바이트 합. 원격 애플리케이션 수신이나 TCP ACK 확인을 뜻하지 않는다. |
| 4 | recv_event_count | integer | count | 양수 바이트를 수신해 관측한 RecvCompleted 횟수. TCP 패킷 수나 메시지 수가 아니다. |
| 5 | send_event_count | integer | count | 관측한 SendCompleted 횟수. 부분 송신 완료도 각각 포함하며 TCP 패킷 수가 아니다. |
| 6 | request_count | integer | count | PacketParsed 이벤트 수. 완성된 프레임과 헤더가 파싱된 횟수이며 payload 검증 성공 횟수는 아니다. |
| 7 | error_count | integer | count | 종료 시점까지 전달된 ProtocolError 이벤트 수. I/O 요청/완료 실패, 잘못된 프레임/ID 및 CS_CHAT payload 실패를 포함하고 종료 후 이벤트는 제외한다. |
| 8 | avg_message_interval_ms | number | ms | 메시지 간격 합 / (request_count - 1). 메시지가 0개 또는 1개이면 0이다. |

## UNSW 전체 입력 대조

related_server_features는 비교 후보이지 동등한 변환식이 아니다. 현재 명세에는 자동 치환 가능한 동등 입력을 등록하지 않았다.

| UNSW 입력 | 상태 | 관련 서버 입력 | 이유 |
| --- | --- | --- | --- |
| dur | not_equivalent | connection_duration_ms | UNSW 기록 지속 시간과 세션 시작 이후 prefix/종료 시각은 경계가 다르다. 설명 CSV만으로 시간 단위와 추출 공식을 완전히 확정할 수 없어 단위 변환만으로 동등하게 취급하지 않는다. |
| proto | requires_transport_metadata | 없음 | 현재 수집 경로는 TCP이나 v1 입력에는 proto가 없다. TCP 상수 하나를 추가해도 나머지 입력의 호환성은 해결되지 않는다. |
| service | requires_flow_protocol_extractor | 없음 | 응용 서비스 분류와 추출기별 연결 상태 정의가 필요하다. 자체 메시지 ID나 is_closed를 대입할 수 없다. |
| state | requires_flow_protocol_extractor | 없음 | 응용 서비스 분류와 추출기별 연결 상태 정의가 필요하다. 자체 메시지 ID나 is_closed를 대입할 수 없다. |
| spkts | not_equivalent | recv_event_count, send_event_count, request_count | 방향별 네트워크 패킷 수는 IOCP 완료 횟수나 파싱한 애플리케이션 프레임 수와 다르다. |
| dpkts | not_equivalent | recv_event_count, send_event_count, request_count | 방향별 네트워크 패킷 수는 IOCP 완료 횟수나 파싱한 애플리케이션 프레임 수와 다르다. |
| sbytes | not_equivalent | bytes_received_total, bytes_sent_total | 거래/흐름 바이트와 애플리케이션 스트림 바이트는 계수 계층, source/destination 방향, 관측 경계가 다르거나 미확인이다. |
| dbytes | not_equivalent | bytes_received_total, bytes_sent_total | 거래/흐름 바이트와 애플리케이션 스트림 바이트는 계수 계층, source/destination 방향, 관측 경계가 다르거나 미확인이다. |
| rate | not_equivalent | connection_duration_ms, bytes_received_total, bytes_sent_total, request_count | 분모와 계수 대상을 일치시켜야 한다. 소켓 바이트율이나 메시지율을 계산해도 UNSW 흐름의 rate/load를 재현한 것이 아니다. |
| sttl | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| dttl | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| sload | not_equivalent | connection_duration_ms, bytes_received_total, bytes_sent_total, request_count | 분모와 계수 대상을 일치시켜야 한다. 소켓 바이트율이나 메시지율을 계산해도 UNSW 흐름의 rate/load를 재현한 것이 아니다. |
| dload | not_equivalent | connection_duration_ms, bytes_received_total, bytes_sent_total, request_count | 분모와 계수 대상을 일치시켜야 한다. 소켓 바이트율이나 메시지율을 계산해도 UNSW 흐름의 rate/load를 재현한 것이 아니다. |
| sloss | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| dloss | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| sinpkt | not_equivalent | message_interval_ms, avg_message_interval_ms | 방향별 패킷 간 도착 시간과 프레임 파싱 처리 간격은 다르며 송신 방향 파싱 간격도 수집하지 않는다. |
| dinpkt | not_equivalent | message_interval_ms, avg_message_interval_ms | 방향별 패킷 간 도착 시간과 프레임 파싱 처리 간격은 다르며 송신 방향 파싱 간격도 수집하지 않는다. |
| sjit | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| djit | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| swin | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| stcpb | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| dtcpb | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| dwin | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| tcprtt | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| synack | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| ackdat | requires_network_telemetry | 없음 | TCP/IP 헤더, 패킷 도착 시각, handshake 및 손실/재전송 추적이 필요하다. 현재 애플리케이션 observer 이벤트만으로 재구성할 수 없다. |
| smean | not_equivalent | packet_size, payload_size | 흐름의 방향별 평균 패킷 크기와 개별 애플리케이션 프레임 크기는 집계 및 계층이 다르다. |
| dmean | not_equivalent | packet_size, payload_size | 흐름의 방향별 평균 패킷 크기와 개별 애플리케이션 프레임 크기는 집계 및 계층이 다르다. |
| trans_depth | requires_application_protocol_parser | 없음 | HTTP/FTP 등 해당 프로토콜 의미를 분석해야 한다. 자체 채팅 프로토콜의 payload_size나 request_count로 대체할 수 없다. |
| response_body_len | requires_application_protocol_parser | 없음 | HTTP/FTP 등 해당 프로토콜 의미를 분석해야 한다. 자체 채팅 프로토콜의 payload_size나 request_count로 대체할 수 없다. |
| ct_srv_src | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_state_ttl | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_dst_ltm | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_src_dport_ltm | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_dst_sport_ltm | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_dst_src_ltm | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| is_ftp_login | requires_application_protocol_parser | 없음 | HTTP/FTP 등 해당 프로토콜 의미를 분석해야 한다. 자체 채팅 프로토콜의 payload_size나 request_count로 대체할 수 없다. |
| ct_ftp_cmd | requires_application_protocol_parser | 없음 | HTTP/FTP 등 해당 프로토콜 의미를 분석해야 한다. 자체 채팅 프로토콜의 payload_size나 request_count로 대체할 수 없다. |
| ct_flw_http_mthd | requires_application_protocol_parser | 없음 | HTTP/FTP 등 해당 프로토콜 의미를 분석해야 한다. 자체 채팅 프로토콜의 payload_size나 request_count로 대체할 수 없다. |
| ct_src_ltm | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| ct_srv_dst | requires_cross_flow_state | 없음 | IP/port/service/state/TTL 정보와 여러 연결에 걸친 이력 집계 및 추출기 규칙이 필요하다. session 내부 counter와는 다른 값이다. |
| is_sm_ips_ports | requires_endpoint_metadata | 없음 | 양 끝 IP와 port 비교가 필요하다. 현재 feature JSON에는 endpoint 정보가 없고 배포 train CSV에도 원본 endpoint가 없다. |

## 상태별 개수

- not_equivalent: 12개
- requires_application_protocol_parser: 5개
- requires_cross_flow_state: 8개
- requires_endpoint_metadata: 1개
- requires_flow_protocol_extractor: 2개
- requires_network_telemetry: 13개
- requires_transport_metadata: 1개

## 수집 및 배포 전 제약

- 같은 수신 완료에 여러 메시지가 들어오면 누적 수신 바이트는 동일할 수 있고 파싱 간격은 네트워크 도착 간격과 다르다.
- PacketParsed는 payload 및 업무 처리 검증보다 먼저 발생한다. 유효한 업무 요청 수라고 해석하면 안 된다.
- 오류 수는 명시적으로 계측한 ProtocolError 이벤트 수이며 모든 업무 예외나 네트워크 손실을 뜻하지 않는다.
- 종료 이후 모든 이벤트는 snapshot에 반영하지 않고 IgnoredAfterCloseEventCount 운영 통계로만 센다. 모든 I/O 완료를 기다린 최종 flow 레코드는 아니다.
- 큐 포화나 HTTP 실패로 snapshot이 유실될 수 있다. queue_sequence_id만으로 큐에 들어오기 전 유실을 모두 식별할 수 없다.
- FastAPI는 v2 의미 버전, 엄격한 수치 범위, 반복 값, UTC 시각 및 프레임 크기 관계를 검증한다. 이 검증이 정상/공격 정답을 보장하지는 않는다.
- RemoveSession/Clear는 I/O 이벤트가 더 오지 않는 시점에 관리자가 호출해야 한다. 삭제한 ID의 tombstone을 영구 보관하지는 않는다.
- 세션 식별자는 프로세스 재시작 시 다시 시작할 수 있다. 학습 데이터 수집 시 별도 run_id가 필요하다.

## 다음 단계

오류 계수, 종료 snapshot 고정 및 FastAPI 공통 검증을 적용한 v2 contract로 정상/통제된 이상 시나리오를 수집하고 별도 애플리케이션 모델을 학습한다. UNSW baseline의 42개 입력을 0으로 채우거나 이름만 바꿔 현재 FastAPI에 연결하지 않는다.

세부 설계: `docs/feature_alignment.md`; 보강 코드와 테스트: `docs/runtime_hardening.md`
