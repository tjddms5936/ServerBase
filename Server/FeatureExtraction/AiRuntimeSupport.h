#pragma once

#include "AiInferenceWorker.h"
#include "FeatureCollector.h"
#include <Windows.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Data.Json.h>
#include <filesystem>
#include <iostream>

#pragma comment(lib, "windowsapp.lib")

namespace feature_extraction
{
struct AiRuntimeOptions
{
    AiInferenceWorkerConfig worker;
    std::size_t queue_capacity = 1024;
    std::wstring report_path;
};

inline AiRuntimeOptions ParseAiRuntimeOptions(int argc, wchar_t** argv)
{
    AiRuntimeOptions options;
    for (int i = 1; i < argc; ++i)
    {
        const std::wstring name = argv[i];
        if (name == L"--ai-rf")
        {
            options.worker.message_only = true;
            options.worker.expected_model_version = "rf-raw7-recovery-v1-t0.7";
            continue;
        }
        if (i + 1 >= argc)
            throw std::invalid_argument("Missing AI option value");
        const std::wstring value = argv[++i];
        if (name == L"--ai-report") { options.report_path = value; continue; }
        std::size_t end = 0;
        const int number = std::stoi(value, &end);
        if (end != value.size() || number <= 0)
            throw std::invalid_argument("AI option must be a positive integer");
        if (name == L"--ai-port" && number <= 65535) options.worker.port = static_cast<std::uint16_t>(number);
        else if (name == L"--ai-queue-capacity" && number <= 100000) options.queue_capacity = number;
        else if (name == L"--ai-timeout-ms" && number <= 60000)
            options.worker.connect_timeout_ms = options.worker.send_timeout_ms = options.worker.receive_timeout_ms = number;
        else if (name == L"--ai-drain-ms" && number <= 60000) options.worker.shutdown_drain_ms = number;
        else throw std::invalid_argument("Unknown AI option or out-of-range value");
    }
    return options;
}

inline void WriteAiRuntimeReport(const AiRuntimeOptions& options, const FeatureCollector& collector,
    const AsyncFeatureQueue& queue, const AiInferenceWorker& worker, double shutdownMs)
{
    if (options.report_path.empty()) return;
    // 종료 시 별도 진단 파일로만 내보내며 네트워크 I/O 경로에는 파일 쓰기를 추가하지 않는다.
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    using namespace winrt::Windows::Data::Json;
    try
    {
        JsonObject root;
        const auto number = [&](const wchar_t* key, double value) { root.Insert(key, JsonValue::CreateNumberValue(value)); };
        number(L"sent", static_cast<double>(worker.SentCount()));
        number(L"failed", static_cast<double>(worker.FailedCount()));
        number(L"stored", static_cast<double>(worker.StoredResultCount()));
        number(L"parse_failed", static_cast<double>(worker.ParseFailedCount()));
        number(L"late_response", static_cast<double>(worker.LateResponseCount()));
        number(L"receive_timeout_ms", options.worker.receive_timeout_ms);
        number(L"skipped_summary", static_cast<double>(worker.SkippedSummaryCount()));
        number(L"shutdown_discarded", static_cast<double>(worker.ShutdownDiscardedCount()));
        number(L"queue_dropped", static_cast<double>(queue.DroppedCount()));
        number(L"queue_size", static_cast<double>(queue.Size()));
        number(L"queue_capacity", static_cast<double>(queue.Capacity()));
        number(L"worker_stop_ms", shutdownMs);
        JsonArray sessions;
        std::size_t unstarted = 0;
        for (const auto& snapshot : collector.GetAllSessionSnapshots())
        {
            if (!snapshot.is_started) { ++unstarted; continue; }
            JsonObject session;
            // 식별자는 부동소수점 변환으로 정밀도를 잃지 않게 문자열로 내보낸다.
            session.Insert(L"session_id", JsonValue::CreateStringValue(std::to_wstring(snapshot.session_id)));
            session.Insert(L"request_count", JsonValue::CreateNumberValue(static_cast<double>(snapshot.request_count)));
            session.Insert(L"closed", JsonValue::CreateBooleanValue(snapshot.is_closed));
            AiInferenceResult result;
            const bool found = worker.GetResultStore()->TryGetLatest(snapshot.session_id, result);
            session.Insert(L"has_successful_result", JsonValue::CreateBooleanValue(found));
            session.Insert(L"covers_latest_message", JsonValue::CreateBooleanValue(found && result.request_index == snapshot.request_count));
            if (found)
            {
                session.Insert(L"last_success_request_index", JsonValue::CreateNumberValue(static_cast<double>(result.request_index)));
                session.Insert(L"queue_sequence_id", JsonValue::CreateStringValue(std::to_wstring(result.queue_sequence_id)));
                session.Insert(L"http_round_trip_ms", JsonValue::CreateNumberValue(result.http_round_trip_ms));
                const auto epochMs = std::chrono::duration_cast<std::chrono::milliseconds>(result.received_wall_time.time_since_epoch()).count();
                session.Insert(L"received_unix_ms", JsonValue::CreateNumberValue(static_cast<double>(epochMs)));
                session.Insert(L"response", JsonObject::Parse(winrt::to_hstring(result.raw_response)));
            }
            sessions.Append(session);
        }
        root.Insert(L"sessions", sessions);
        number(L"unstarted_session_count", static_cast<double>(unstarted));
        const std::string body = winrt::to_string(root.Stringify());
        HANDLE file = ::CreateFileW(options.report_path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot create new AI report");
        DWORD written = 0;
        const BOOL ok = ::WriteFile(file, body.data(), static_cast<DWORD>(body.size()), &written, nullptr);
        ::CloseHandle(file);
        if (!ok || written != body.size()) throw std::runtime_error("Cannot write AI report");
    }
    catch (...) { winrt::uninit_apartment(); throw; }
    winrt::uninit_apartment();
}
}
