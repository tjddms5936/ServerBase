#pragma once

#include <chrono>
#include <cstdint>
#include <string>

namespace feature_extraction
{
struct SessionFeatureSnapshot
{
    std::uint64_t session_id = 0;           // 세션 고유 ID
    std::uintptr_t socket_handle = 0;       // 디버깅용 소켓 값
    std::int32_t logic_worker_id = -1;      // 마지막 이벤트를 처리한 논리 워커

    bool is_started = false;                // 세션 시작 여부
    bool is_closed = false;                 // 세션 종료 여부

    std::chrono::system_clock::time_point connection_start_wall_time{};
    std::chrono::system_clock::time_point last_event_wall_time{};
    std::chrono::system_clock::time_point connection_close_wall_time{};

    double connection_duration_ms = 0.0;    // 연결 지속 시간

    std::uint64_t bytes_received_total = 0; // 누적 수신 바이트
    std::uint64_t bytes_sent_total = 0;     // 누적 송신 바이트
    std::uint64_t recv_event_count = 0;     // 수신 완료 이벤트 횟수
    std::uint64_t send_event_count = 0;     // 송신 완료 이벤트 횟수
    std::uint64_t request_count = 0;        // 파싱된 패킷 수
    std::uint64_t error_count = 0;          // 프로토콜/입출력 오류 수

    double last_message_interval_ms = 0.0;  // 직전 메시지와 현재 메시지 사이 간격
    double avg_message_interval_ms = 0.0;   // 평균 메시지 간격

    std::int32_t last_packet_id = -1;       // 마지막 패킷 ID
    std::int32_t last_packet_size = 0;      // 마지막 패킷 크기
    std::int32_t last_payload_size = 0;     // 마지막 페이로드 크기

    std::uint32_t last_error_code = 0;      // 마지막 오류 코드
    std::string last_error_message;         // 마지막 오류 설명
};

struct MessageFeatureSnapshot
{
    std::uint64_t session_id = 0;           // 어떤 세션의 메시지인지
    std::uintptr_t socket_handle = 0;
    std::int32_t logic_worker_id = -1;

    std::chrono::system_clock::time_point message_wall_time{};

    std::uint64_t request_index = 0;        // 세션 내 몇 번째 메시지인지
    std::int32_t packet_id = -1;            // 패킷 ID
    std::int32_t packet_size = 0;           // 헤더 포함 패킷 크기
    std::int32_t payload_size = 0;          // 페이로드 크기

    double connection_duration_ms = 0.0;    // 메시지 시점의 연결 지속 시간
    double message_interval_ms = 0.0;       // 이전 메시지와의 시간 차이

    std::uint64_t bytes_received_total = 0; // 해당 시점까지 누적 수신 바이트
    std::uint64_t bytes_sent_total = 0;     // 해당 시점까지 누적 송신 바이트
    std::uint64_t error_count_total = 0;    // 해당 시점까지 누적 오류 수
};
}
