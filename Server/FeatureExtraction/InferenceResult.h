#pragma once

#include <chrono>
#include <cstdint>
#include <string>

namespace feature_extraction
{
struct AiInferenceResult
{
    std::uint64_t session_id = 0;           // 추론 결과가 속한 세션 ID
    std::uint64_t queue_sequence_id = 0;    // 큐에서 부여한 feature 순번

    std::string request_id;                 // Python 서버가 응답한 요청 ID
    std::string event_type;                 // C++ 서버가 보낸 feature 타입
    std::string received_event_type;        // Python 서버가 확인한 feature 타입

    std::string label;                      // 추론 라벨
    bool is_attack = false;                 // 공격 여부
    double confidence = 0.0;                // 추론 신뢰도
    std::string model_version;              // 응답한 모델 버전

    std::string raw_response;               // 디버깅용 원본 응답 JSON
    std::chrono::system_clock::time_point received_wall_time{};
};

struct SessionInferenceState
{
    std::uint64_t session_id = 0;           // 세션 ID
    bool has_latest_result = false;         // 최신 결과 존재 여부
    AiInferenceResult latest_result;        // 세션의 최신 추론 결과

    std::uint64_t total_result_count = 0;   // 세션에 저장된 전체 결과 수
    std::uint64_t attack_result_count = 0;  // 공격으로 판단된 결과 수

    std::chrono::system_clock::time_point first_result_wall_time{};
    std::chrono::system_clock::time_point last_result_wall_time{};
};
}
