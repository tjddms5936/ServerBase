#pragma once

#include <chrono>
#include <cstdint>
#include <string>

namespace feature_extraction
{
enum class NetworkEventType
{
    SessionStarted,     // 세션이 시작되고 첫 수신 요청을 걸기 직전
    SessionClosed,      // 소켓이 닫힐 때
    RecvCompleted,      // 수신 완료 후 실제 바이트를 받은 시점
    SendCompleted,      // 송신 완료 후 실제 바이트를 보낸 시점
    PacketParsed,       // 수신 버퍼에서 완성된 패킷 하나를 파싱한 시점
    ProtocolError,      // 잘못된 패킷 크기, 역직렬화 실패, 남은 바이트, 입출력 실패 등
};

struct NetworkEvent
{
    NetworkEventType type = NetworkEventType::ProtocolError;

    std::uint64_t session_id = 0;           // 서버 내부 세션 식별자
    std::uintptr_t socket_handle = 0;       // 디버깅용 소켓 핸들 값
    std::int32_t logic_worker_id = -1;      // 이벤트를 처리한 논리 워커 스레드 번호

    std::chrono::system_clock::time_point wall_time = std::chrono::system_clock::now();         // 로그와 직렬화 타임스탬프에 사용할 실제 시간
    std::chrono::steady_clock::time_point monotonic_time = std::chrono::steady_clock::now();    // 간격과 지속 시간 계산에 사용할 단조 시간

    std::uint32_t bytes_transferred = 0;    // 수신 또는 송신 완료 바이트 수

    std::int32_t packet_id = -1;            // 프로토콜 패킷 번호
    std::int32_t packet_size = 0;           // 헤더를 포함한 패킷 크기
    std::int32_t payload_size = 0;          // 페이로드 크기

    std::uint32_t error_code = 0;           // 입출력 또는 윈속 오류 코드
    std::string error_message;              // 프로토콜 오류 설명
};
}
