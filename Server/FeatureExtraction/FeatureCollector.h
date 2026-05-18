#pragma once

#include "FeatureSnapshot.h"
#include "INetworkEventObserver.h"

#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace feature_extraction
{
class AsyncFeatureQueue;

class FeatureCollector : public INetworkEventObserver
{
public:
    FeatureCollector() = default;
    ~FeatureCollector() override = default;

    FeatureCollector(const FeatureCollector&) = delete;
    FeatureCollector& operator=(const FeatureCollector&) = delete;

    void OnNetworkEvent(const NetworkEvent& event) override;
    void SetFeatureQueue(std::shared_ptr<AsyncFeatureQueue> featureQueue);

    bool GetSessionSnapshot(std::uint64_t sessionId, SessionFeatureSnapshot& outSnapshot) const;
    bool GetLatestMessageSnapshot(std::uint64_t sessionId, MessageFeatureSnapshot& outSnapshot) const;
    std::vector<SessionFeatureSnapshot> GetAllSessionSnapshots() const;

    void RemoveSession(std::uint64_t sessionId);
    void Clear();

private:
    struct SessionState
    {
        SessionFeatureSnapshot session;
        MessageFeatureSnapshot latest_message;

        bool has_start_time = false;
        bool has_last_message_time = false;
        bool has_latest_message = false;

        std::chrono::steady_clock::time_point connection_start_time{};
        std::chrono::steady_clock::time_point last_message_time{};

        double message_interval_sum_ms = 0.0;
    };

    SessionState& GetOrCreateSessionLocked(const NetworkEvent& event);

    void HandleSessionStartedLocked(SessionState& state, const NetworkEvent& event);
    void HandleSessionClosedLocked(SessionState& state, const NetworkEvent& event);
    void HandleRecvCompletedLocked(SessionState& state, const NetworkEvent& event);
    void HandleSendCompletedLocked(SessionState& state, const NetworkEvent& event);
    void HandlePacketParsedLocked(SessionState& state, const NetworkEvent& event);
    void HandleProtocolErrorLocked(SessionState& state, const NetworkEvent& event);

    void UpdateCommonEventFieldsLocked(SessionState& state, const NetworkEvent& event);
    void UpdateConnectionDurationLocked(SessionState& state, const NetworkEvent& event);

    static double ToMilliseconds(std::chrono::steady_clock::duration duration);

private:
    mutable std::mutex m_mutex;
    std::unordered_map<std::uint64_t, SessionState> m_sessions;
    std::shared_ptr<AsyncFeatureQueue> m_featureQueue;
};
}
