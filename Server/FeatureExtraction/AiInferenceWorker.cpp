#include "AiInferenceWorker.h"

#include <Windows.h>
#include <winhttp.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Data.Json.h>

#include <cctype>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <ctime>
#include <utility>
#include <vector>

#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "windowsapp.lib")

namespace feature_extraction
{
namespace
{
std::uint64_t ExtractSessionId(const FeatureQueueItem& item)
{
    if (item.type == FeatureQueueItemType::SessionSummary)
        return item.session.session_id;

    return item.message.session_id;
}

std::string GetFeatureEventType(const FeatureQueueItem& item)
{
    if (item.type == FeatureQueueItemType::SessionSummary)
        return "session_summary";

    return "message";
}

}
AiInferenceWorker::AiInferenceWorker(
    std::shared_ptr<AsyncFeatureQueue> featureQueue,
    AiInferenceWorkerConfig config,
    std::shared_ptr<InferenceResultStore> resultStore)
    : m_featureQueue(featureQueue),
      m_resultStore(std::move(resultStore)),
      m_config(std::move(config))
{
    if (!m_featureQueue)
        throw std::invalid_argument("AiInferenceWorker featureQueue must not be null");
    if (m_config.connect_timeout_ms <= 0 || m_config.send_timeout_ms <= 0 ||
        m_config.receive_timeout_ms <= 0 || m_config.shutdown_drain_ms < 0)
        throw std::invalid_argument("AiInferenceWorker timeouts must be positive and drain must be nonnegative");

    if (!m_resultStore)
        m_resultStore = std::make_shared<InferenceResultStore>();
}

AiInferenceWorker::~AiInferenceWorker()
{
    Stop();
}

bool AiInferenceWorker::Start()
{
    std::lock_guard<std::mutex> lock(m_lifecycleMutex);
    if (m_workerThread.joinable() || m_featureQueue->IsClosed())
        return false;

    bool expected = false;
    if (!m_running.compare_exchange_strong(expected, true))
        return false;

    try { m_workerThread = std::thread([this]()
        {
            WorkerLoop();
        }); }
    catch (...) { m_running.store(false); return false; }
    return true;
}

void AiInferenceWorker::Stop()
{
    std::lock_guard<std::mutex> lock(m_lifecycleMutex);
    // 마감 시각을 먼저 기록하고 원자 플래그로 공개해 worker와의 데이터 경합을 피한다.
    if (!m_stopRequested.load())
    {
        m_stopDeadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(m_config.shutdown_drain_ms);
        m_stopRequested.store(true);
    }
    const bool wasRunning = m_running.exchange(false);
    if (wasRunning || m_workerThread.joinable())
        m_featureQueue->Close();

    if (m_workerThread.joinable())
        m_workerThread.join();
}

bool AiInferenceWorker::IsRunning() const
{
    return m_running.load();
}

std::uint64_t AiInferenceWorker::SentCount() const
{
    return m_sentCount.load();
}

std::uint64_t AiInferenceWorker::FailedCount() const
{
    return m_failedCount.load();
}

std::uint64_t AiInferenceWorker::StoredResultCount() const
{
    return m_storedResultCount.load();
}

std::uint64_t AiInferenceWorker::ParseFailedCount() const
{
    return m_parseFailedCount.load();
}

std::uint64_t AiInferenceWorker::SkippedSummaryCount() const { return m_skippedSummaryCount.load(); }
std::uint64_t AiInferenceWorker::ShutdownDiscardedCount() const { return m_shutdownDiscardedCount.load(); }
std::uint64_t AiInferenceWorker::LateResponseCount() const { return m_lateResponseCount.load(); }

std::shared_ptr<InferenceResultStore> AiInferenceWorker::GetResultStore() const
{
    return m_resultStore;
}

void AiInferenceWorker::WorkerLoop()
{
    // Windows SDK의 구조화된 JSON 파서를 worker 스레드에서 사용한다.
    try { winrt::init_apartment(winrt::apartment_type::multi_threaded); }
    catch (...)
    {
        m_failedCount += 1;
        m_featureQueue->Close();
        m_running.store(false);
        return;
    }
    while (true)
    {
        FeatureQueueItem item;
        if (!m_featureQueue->WaitPop(item))
            break;

        if (m_stopRequested.load() && std::chrono::steady_clock::now() >= m_stopDeadline)
            m_shutdownDiscardedCount += 1;
        else
        {
            try { ProcessItem(item); }
            catch (...) { m_failedCount += 1; } // AI 부가 경로의 오류가 TCP 서버를 종료시키지 않게 한다.
        }
    }

    m_running.store(false);
    winrt::uninit_apartment();
}

void AiInferenceWorker::ProcessItem(const FeatureQueueItem& item)
{
    if (m_config.message_only && item.type == FeatureQueueItemType::SessionSummary)
    {
        m_skippedSummaryCount += 1;
        return;
    }
    const std::string json = BuildJson(item);

    std::string response;
    unsigned long statusCode = 0;
    const auto started = std::chrono::steady_clock::now();
    const bool sent = PostJson(json, response, statusCode);
    const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    // OS의 timeout 통지가 늦어져도 예산을 넘긴 판단을 최신 성공으로 저장하지 않는다.
    const double responseBudget = static_cast<double>(m_config.connect_timeout_ms) + m_config.send_timeout_ms + m_config.receive_timeout_ms;
    if (sent && elapsed > responseBudget)
    {
        m_lateResponseCount += 1;
        m_failedCount += 1;
        return;
    }
    if (sent)
    {
        m_sentCount += 1;

        AiInferenceResult result;
        if (ParseInferenceResponse(response, item, result))
        {
            result.request_index = item.message.request_index;
            result.http_round_trip_ms = elapsed;
            m_resultStore->Update(result);
            m_storedResultCount += 1;

            std::cout << "[AiInferenceWorker] inference stored. seq="
                << item.queue_sequence_id
                << ", session=" << result.session_id
                << ", label=" << result.label
                << ", attack=" << (result.is_attack ? "true" : "false")
                << ", confidence=" << result.confidence << std::endl;
        }
        else
        {
            m_parseFailedCount += 1;
            std::cerr << "[AiInferenceWorker] response parse failed. seq="
                << item.queue_sequence_id << ", response=" << response << std::endl;
        }
    }
    else
    {
        // 호환 모드에서도 명시적인 요약 미지원만 장애와 분리한다.
        if (statusCode == 422 && item.type == FeatureQueueItemType::SessionSummary)
        {
            try
            {
                const auto body = winrt::Windows::Data::Json::JsonObject::Parse(winrt::to_hstring(response));
                if (body.GetNamedObject(L"detail").GetNamedString(L"code") == L"unsupported_event_type")
                {
                    m_skippedSummaryCount += 1;
                    return;
                }
            }
            catch (...) {}
        }
        m_failedCount += 1;
        std::cerr << "[AiInferenceWorker] failed to send feature. seq="
            << item.queue_sequence_id << std::endl;
    }
}

std::string AiInferenceWorker::BuildJson(const FeatureQueueItem& item) const
{
    if (item.type == FeatureQueueItemType::SessionSummary)
        return BuildSessionJson(item);

    return BuildMessageJson(item);
}

std::string AiInferenceWorker::BuildMessageJson(const FeatureQueueItem& item) const
{
    const MessageFeatureSnapshot& message = item.message;

    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{";
    out << "\"schema_version\":\"v1\",";
    out << "\"feature_contract_id\":\"" << kMessageFeatureContractId << "\",";
    out << "\"event_type\":\"message\",";
    out << "\"queue_sequence_id\":" << item.queue_sequence_id << ",";
    out << "\"timestamp\":\"" << FormatTimestamp(message.message_wall_time) << "\",";
    out << "\"session\":{";
    out << "\"session_id\":" << message.session_id << ",";
    out << "\"socket_handle\":" << message.socket_handle << ",";
    out << "\"logic_worker_id\":" << message.logic_worker_id << ",";
    out << "\"connection_duration_ms\":" << message.connection_duration_ms << ",";
    out << "\"bytes_received_total\":" << message.bytes_received_total << ",";
    out << "\"bytes_sent_total\":" << message.bytes_sent_total << ",";
    out << "\"error_count_total\":" << message.error_count_total;
    out << "},";
    out << "\"message\":{";
    out << "\"request_index\":" << message.request_index << ",";
    out << "\"packet_id\":" << message.packet_id << ",";
    out << "\"packet_size\":" << message.packet_size << ",";
    out << "\"payload_size\":" << message.payload_size << ",";
    out << "\"message_interval_ms\":" << message.message_interval_ms;
    out << "},";
    out << "\"features\":{";
    out << "\"packet_size\":" << message.packet_size << ",";
    out << "\"payload_size\":" << message.payload_size << ",";
    out << "\"connection_duration_ms\":" << message.connection_duration_ms << ",";
    out << "\"message_interval_ms\":" << message.message_interval_ms << ",";
    out << "\"bytes_received_total\":" << message.bytes_received_total << ",";
    out << "\"bytes_sent_total\":" << message.bytes_sent_total << ",";
    out << "\"error_count_total\":" << message.error_count_total;
    out << "}";
    out << "}";
    return out.str();
}

std::string AiInferenceWorker::BuildSessionJson(const FeatureQueueItem& item) const
{
    const SessionFeatureSnapshot& session = item.session;

    const auto timestamp = session.connection_close_wall_time.time_since_epoch().count() == 0
        ? session.last_event_wall_time
        : session.connection_close_wall_time;

    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{";
    out << "\"schema_version\":\"v1\",";
    out << "\"feature_contract_id\":\"" << kSessionSummaryFeatureContractId << "\",";
    out << "\"event_type\":\"session_summary\",";
    out << "\"queue_sequence_id\":" << item.queue_sequence_id << ",";
    out << "\"timestamp\":\"" << FormatTimestamp(timestamp) << "\",";
    out << "\"session\":{";
    out << "\"session_id\":" << session.session_id << ",";
    out << "\"socket_handle\":" << session.socket_handle << ",";
    out << "\"logic_worker_id\":" << session.logic_worker_id << ",";
    out << "\"is_started\":" << (session.is_started ? "true" : "false") << ",";
    out << "\"is_closed\":" << (session.is_closed ? "true" : "false") << ",";
    out << "\"connection_duration_ms\":" << session.connection_duration_ms << ",";
    out << "\"bytes_received_total\":" << session.bytes_received_total << ",";
    out << "\"bytes_sent_total\":" << session.bytes_sent_total << ",";
    out << "\"recv_event_count\":" << session.recv_event_count << ",";
    out << "\"send_event_count\":" << session.send_event_count << ",";
    out << "\"request_count\":" << session.request_count << ",";
    out << "\"error_count\":" << session.error_count << ",";
    out << "\"avg_message_interval_ms\":" << session.avg_message_interval_ms << ",";
    out << "\"last_error_code\":" << session.last_error_code << ",";
    out << "\"last_error_message\":\"" << EscapeJson(session.last_error_message) << "\"";
    out << "},";
    out << "\"features\":{";
    out << "\"connection_duration_ms\":" << session.connection_duration_ms << ",";
    out << "\"bytes_received_total\":" << session.bytes_received_total << ",";
    out << "\"bytes_sent_total\":" << session.bytes_sent_total << ",";
    out << "\"recv_event_count\":" << session.recv_event_count << ",";
    out << "\"send_event_count\":" << session.send_event_count << ",";
    out << "\"request_count\":" << session.request_count << ",";
    out << "\"error_count\":" << session.error_count << ",";
    out << "\"avg_message_interval_ms\":" << session.avg_message_interval_ms;
    out << "}";
    out << "}";
    return out.str();
}

bool AiInferenceWorker::ParseInferenceResponse(const std::string& response, const FeatureQueueItem& item, AiInferenceResult& outResult) const
{
    try
    {
    AiInferenceResult result;
    result.session_id = ExtractSessionId(item);
    result.queue_sequence_id = item.queue_sequence_id;
    result.event_type = GetFeatureEventType(item);
    result.raw_response = response;
    result.received_wall_time = std::chrono::system_clock::now();

    const auto body = winrt::Windows::Data::Json::JsonObject::Parse(winrt::to_hstring(response));
    result.request_id = winrt::to_string(body.GetNamedString(L"request_id"));
    result.label = winrt::to_string(body.GetNamedString(L"label"));
    result.is_attack = body.GetNamedBoolean(L"is_attack");
    result.confidence = body.GetNamedNumber(L"confidence");
    result.model_version = winrt::to_string(body.GetNamedString(L"model_version", L""));
    result.received_event_type = winrt::to_string(body.GetNamedString(L"received_event_type", L""));
    if (result.request_id != std::to_string(item.queue_sequence_id) || result.label.empty() ||
        !std::isfinite(result.confidence) || result.confidence < 0 || result.confidence > 1 ||
        (!result.received_event_type.empty() && result.received_event_type != result.event_type))
        return false;
    if (!m_config.expected_model_version.empty())
    {
        result.anomaly_score = body.GetNamedNumber(L"anomaly_score");
        result.decision_threshold = body.GetNamedNumber(L"decision_threshold");
        result.decision_scope = winrt::to_string(body.GetNamedString(L"decision_scope"));
        result.has_anomaly_score = true;
        const double expectedConfidence = result.is_attack ? result.anomaly_score : 1 - result.anomaly_score;
        if (result.model_version != m_config.expected_model_version || result.received_event_type != "message" ||
            !std::isfinite(result.anomaly_score) || result.anomaly_score < 0 || result.anomaly_score > 1 ||
            result.decision_threshold != 0.7 || result.decision_scope != "local_advisory" ||
            result.is_attack != (result.anomaly_score >= 0.7) ||
            result.label != (result.is_attack ? "controlled_anomaly" : "normal") ||
            std::abs(result.confidence - expectedConfidence) > 1e-10)
            return false;
    }

    outResult = result;
    return true;
    }
    catch (...) { return false; }
}
bool AiInferenceWorker::PostJson(const std::string& json, std::string& outResponse, unsigned long& statusCode) const
{
    /*
        생성 순서
        session
        └── connection
            └── request

        해제는 역순
    */

    // 브라우저 실행
    HINTERNET session = ::WinHttpOpen(
        L"NetworkDetectionAI/0.1",
        WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME,
        WINHTTP_NO_PROXY_BYPASS,
        0);
    if (!session)
        return false;

    // 타임아웃 설정
    if (!::WinHttpSetTimeouts(
        session,
        m_config.connect_timeout_ms,
        m_config.connect_timeout_ms,
        m_config.send_timeout_ms,
        m_config.receive_timeout_ms))
    {
        ::WinHttpCloseHandle(session);
        return false;
    }

    // 사이트 주소 입력
    HINTERNET connection = ::WinHttpConnect(
        session,
        m_config.host.c_str(),
        static_cast<INTERNET_PORT>(m_config.port),
        0);
    if (!connection)
    {
        ::WinHttpCloseHandle(session);
        return false;
    }

    // POST 요청 작성창 열기
    const DWORD flags = m_config.use_tls ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET request = ::WinHttpOpenRequest(
        connection,
        L"POST",
        m_config.path.c_str(),
        nullptr,
        WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES,
        flags);
    if (!request)
    {
        ::WinHttpCloseHandle(connection);
        ::WinHttpCloseHandle(session);
        return false;
    }

    // 응답 본문 읽기 제한과 전체 응답 헤더 대기 제한은 서로 달라 명시적으로 고정한다.
    DWORD headerTimeout = static_cast<DWORD>(m_config.receive_timeout_ms);
    DWORD redirectPolicy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
    if (!::WinHttpSetTimeouts(request, m_config.connect_timeout_ms, m_config.connect_timeout_ms,
            m_config.send_timeout_ms, m_config.receive_timeout_ms) ||
        !::WinHttpSetOption(request, WINHTTP_OPTION_RECEIVE_RESPONSE_TIMEOUT, &headerTimeout, sizeof(headerTimeout)) ||
        !::WinHttpSetOption(request, WINHTTP_OPTION_REDIRECT_POLICY, &redirectPolicy, sizeof(redirectPolicy)))
    {
        ::WinHttpCloseHandle(request);
        ::WinHttpCloseHandle(connection);
        ::WinHttpCloseHandle(session);
        return false;
    }

    // 실제 전송 버튼 클릭
    const std::wstring headers = L"Content-Type: application/json\r\nAccept: application/json\r\n";
    BOOL ok = ::WinHttpSendRequest(
        request,
        headers.c_str(),
        static_cast<DWORD>(headers.length()),
        const_cast<char*>(json.data()),
        static_cast<DWORD>(json.size()),
        static_cast<DWORD>(json.size()),
        0);

    // FastAPI 서버 응답이 올 때까지 기다림
    if (ok)
        ok = ::WinHttpReceiveResponse(request, nullptr);

    statusCode = 0;
    DWORD statusSize = sizeof(statusCode);
    if (ok)
    {
        ok = ::WinHttpQueryHeaders(
            request,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX,
            &statusCode,
            &statusSize,
            WINHTTP_NO_HEADER_INDEX);
    }

    if (ok)
    {
        DWORD availableSize = 0;
        do
        {
            // 응답 body 크기 확인
            availableSize = 0;
            if (!::WinHttpQueryDataAvailable(request, &availableSize))
            {
                ok = FALSE;
                break;
            }

            // 읽을 데이터 없으면 종료
            if (availableSize == 0)
                break;

            // 신뢰하지 못하는 응답이 worker 메모리를 계속 늘리지 못하게 제한한다.
            if (availableSize > 65536 || outResponse.size() + availableSize > 65536)
            {
                ok = FALSE;
                break;
            }

            // 응답 데이터 읽기
            std::vector<char> buffer(availableSize + 1);
            DWORD bytesRead = 0;
            if (!::WinHttpReadData(request, buffer.data(), availableSize, &bytesRead))
            {
                ok = FALSE;
                break;
            }

            // outResponse에 추가
            outResponse.append(buffer.data(), bytesRead);
        } while (availableSize > 0);
    }

    ::WinHttpCloseHandle(request);
    ::WinHttpCloseHandle(connection);
    ::WinHttpCloseHandle(session);

    return ok && statusCode >= 200 && statusCode < 300;
}

std::string AiInferenceWorker::EscapeJson(const std::string& value)
{
    std::ostringstream out;
    for (char ch : value)
    {
        switch (ch)
        {
        case '\\':
            out << "\\\\";
            break;
        case '"':
            out << "\\\"";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            out << ch;
            break;
        }
    }
    return out.str();
}

std::string AiInferenceWorker::FormatTimestamp(std::chrono::system_clock::time_point timePoint)
{
    const auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(
        timePoint.time_since_epoch()) % 1000;
    const std::time_t time = std::chrono::system_clock::to_time_t(timePoint);

    std::tm utcTime{};
    gmtime_s(&utcTime, &time);

    std::ostringstream out;
    out << std::put_time(&utcTime, "%Y-%m-%dT%H:%M:%S");
    out << "." << std::setw(3) << std::setfill('0') << millis.count() << "Z";
    return out.str();
}
}
