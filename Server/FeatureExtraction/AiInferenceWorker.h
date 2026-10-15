#pragma once

#include "AsyncFeatureQueue.h"
#include "InferenceResultStore.h"

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

namespace feature_extraction
{
struct AiInferenceWorkerConfig
{
    std::wstring host = L"127.0.0.1";       // FastAPI 서버 주소
    std::uint16_t port = 8000;              // FastAPI 서버 포트
    std::wstring path = L"/predict";        // 추론 요청 경로
    bool use_tls = false;                   // 로컬 최소 동작 버전에서는 HTTP를 사용한다.

    int connect_timeout_ms = 1000;          // 연결 제한 시간
    int send_timeout_ms = 1000;             // 송신 제한 시간
    int receive_timeout_ms = 1000;          // 응답 수신 제한 시간
    bool message_only = false;             // 기존 수집 경로는 보존하고 실제 RF 실행에서만 요약을 제외한다.
    std::string expected_model_version;     // 비어 있지 않으면 승인 RF의 점수·정책까지 검사한다.
    int shutdown_drain_ms = 2000;           // 종료 후 남은 큐를 배출할 예산이며 진행 중 HTTP의 강제 취소 시간은 아니다.
};

class AiInferenceWorker
{
public:
    explicit AiInferenceWorker(
        std::shared_ptr<AsyncFeatureQueue> featureQueue,
        AiInferenceWorkerConfig config = {},
        std::shared_ptr<InferenceResultStore> resultStore = nullptr);
    ~AiInferenceWorker();

    AiInferenceWorker(const AiInferenceWorker&) = delete;
    AiInferenceWorker& operator=(const AiInferenceWorker&) = delete;

    bool Start();
    void Stop();

    bool IsRunning() const;
    std::uint64_t SentCount() const;
    std::uint64_t FailedCount() const;
    std::uint64_t StoredResultCount() const;
    std::uint64_t ParseFailedCount() const;
    std::uint64_t SkippedSummaryCount() const;
    std::uint64_t ShutdownDiscardedCount() const;
    std::uint64_t LateResponseCount() const;
    std::shared_ptr<InferenceResultStore> GetResultStore() const;

private:
    void WorkerLoop();
    void ProcessItem(const FeatureQueueItem& item);

    std::string BuildJson(const FeatureQueueItem& item) const;
    std::string BuildMessageJson(const FeatureQueueItem& item) const;
    std::string BuildSessionJson(const FeatureQueueItem& item) const;

    bool PostJson(const std::string& json, std::string& outResponse, unsigned long& statusCode) const;
    bool ParseInferenceResponse(
        const std::string& response,
        const FeatureQueueItem& item,
        AiInferenceResult& outResult) const;

    static std::string EscapeJson(const std::string& value);
    static std::string FormatTimestamp(std::chrono::system_clock::time_point timePoint);

private:
    std::shared_ptr<AsyncFeatureQueue> m_featureQueue;
    std::shared_ptr<InferenceResultStore> m_resultStore;
    AiInferenceWorkerConfig m_config;

    std::atomic<bool> m_running{ false };
    std::thread m_workerThread;
    std::mutex m_lifecycleMutex;            // 시작·종료·join의 동시 호출을 직렬화한다.
    std::atomic<std::uint64_t> m_sentCount{ 0 };
    std::atomic<std::uint64_t> m_failedCount{ 0 };
    std::atomic<std::uint64_t> m_storedResultCount{ 0 };
    std::atomic<std::uint64_t> m_parseFailedCount{ 0 };
    std::atomic<std::uint64_t> m_skippedSummaryCount{ 0 };
    std::atomic<std::uint64_t> m_shutdownDiscardedCount{ 0 };
    std::atomic<std::uint64_t> m_lateResponseCount{ 0 };
    std::chrono::steady_clock::time_point m_stopDeadline{};
    std::atomic<bool> m_stopRequested{ false };
};
}
