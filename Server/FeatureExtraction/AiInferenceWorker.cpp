#include "AiInferenceWorker.h"

#include <Windows.h>
#include <winhttp.h>

#include <cctype>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <ctime>
#include <utility>
#include <vector>

#pragma comment(lib, "winhttp.lib")

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

void SkipWhitespace(const std::string& json, std::size_t& pos)
{
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos])))
        ++pos;
}

std::size_t FindJsonValueStart(const std::string& json, const std::string& key)
{
    const std::string quotedKey = "\"" + key + "\"";
    const std::size_t keyPos = json.find(quotedKey);
    if (keyPos == std::string::npos)
        return std::string::npos;

    const std::size_t colonPos = json.find(':', keyPos + quotedKey.size());
    if (colonPos == std::string::npos)
        return std::string::npos;

    std::size_t valuePos = colonPos + 1;
    SkipWhitespace(json, valuePos);
    return valuePos;
}

bool TryExtractJsonString(const std::string& json, const std::string& key, std::string& outValue)
{
    std::size_t pos = FindJsonValueStart(json, key);
    if (pos == std::string::npos || pos >= json.size() || json[pos] != '"')
        return false;

    ++pos;
    outValue.clear();
    while (pos < json.size())
    {
        const char ch = json[pos++];
        if (ch == '"')
            return true;

        if (ch == '\\' && pos < json.size())
        {
            const char escaped = json[pos++];
            switch (escaped)
            {
            case '"': outValue.push_back('"'); break;
            case '\\': outValue.push_back('\\'); break;
            case 'n': outValue.push_back('\n'); break;
            case 'r': outValue.push_back('\r'); break;
            case 't': outValue.push_back('\t'); break;
            default: outValue.push_back(escaped); break;
            }
        }
        else
        {
            outValue.push_back(ch);
        }
    }

    return false;
}

bool TryExtractJsonBool(const std::string& json, const std::string& key, bool& outValue)
{
    const std::size_t pos = FindJsonValueStart(json, key);
    if (pos == std::string::npos)
        return false;

    if (json.compare(pos, 4, "true") == 0)
    {
        outValue = true;
        return true;
    }

    if (json.compare(pos, 5, "false") == 0)
    {
        outValue = false;
        return true;
    }

    return false;
}

bool TryExtractJsonDouble(const std::string& json, const std::string& key, double& outValue)
{
    std::size_t pos = FindJsonValueStart(json, key);
    if (pos == std::string::npos)
        return false;

    const std::size_t start = pos;
    while (pos < json.size())
    {
        const char ch = json[pos];
        if (!std::isdigit(static_cast<unsigned char>(ch)) && ch != '-' && ch != '+' && ch != '.' && ch != 'e' && ch != 'E')
            break;
        ++pos;
    }

    if (pos == start)
        return false;

    try
    {
        outValue = std::stod(json.substr(start, pos - start));
        return true;
    }
    catch (...)
    {
        return false;
    }
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

    if (!m_resultStore)
        m_resultStore = std::make_shared<InferenceResultStore>();
}

AiInferenceWorker::~AiInferenceWorker()
{
    Stop();
}

bool AiInferenceWorker::Start()
{
    if (m_workerThread.joinable())
        return false;

    bool expected = false;
    if (!m_running.compare_exchange_strong(expected, true))
        return false;

    m_workerThread = std::thread([this]()
        {
            WorkerLoop();
        });
    return true;
}

void AiInferenceWorker::Stop()
{
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

std::shared_ptr<InferenceResultStore> AiInferenceWorker::GetResultStore() const
{
    return m_resultStore;
}

void AiInferenceWorker::WorkerLoop()
{
    while (true)
    {
        FeatureQueueItem item;
        if (!m_featureQueue->WaitPop(item))
            break;

        ProcessItem(item);
    }

    m_running.store(false);
}

void AiInferenceWorker::ProcessItem(const FeatureQueueItem& item)
{
    const std::string json = BuildJson(item);

    std::string response;
    if (PostJson(json, response))
    {
        m_sentCount += 1;

        AiInferenceResult result;
        if (ParseInferenceResponse(response, item, result))
        {
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
    AiInferenceResult result;
    result.session_id = ExtractSessionId(item);
    result.queue_sequence_id = item.queue_sequence_id;
    result.event_type = GetFeatureEventType(item);
    result.raw_response = response;
    result.received_wall_time = std::chrono::system_clock::now();

    if (!TryExtractJsonString(response, "request_id", result.request_id))
        return false;

    if (!TryExtractJsonString(response, "label", result.label))
        return false;

    if (!TryExtractJsonBool(response, "is_attack", result.is_attack))
        return false;

    if (!TryExtractJsonDouble(response, "confidence", result.confidence))
        return false;

    TryExtractJsonString(response, "model_version", result.model_version);
    TryExtractJsonString(response, "received_event_type", result.received_event_type);

    outResult = result;
    return true;
}
bool AiInferenceWorker::PostJson(const std::string& json, std::string& outResponse) const
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
        WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME,
        WINHTTP_NO_PROXY_BYPASS,
        0);
    if (!session)
        return false;

    // 타임아웃 설정
    ::WinHttpSetTimeouts(
        session,
        m_config.connect_timeout_ms,
        m_config.connect_timeout_ms,
        m_config.send_timeout_ms,
        m_config.receive_timeout_ms);

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

    DWORD statusCode = 0;
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
