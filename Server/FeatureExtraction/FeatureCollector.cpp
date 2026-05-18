#include "FeatureCollector.h"
#include "AsyncFeatureQueue.h"

namespace feature_extraction
{
void FeatureCollector::OnNetworkEvent(const NetworkEvent& event)
{
    std::shared_ptr<AsyncFeatureQueue> featureQueue;
    FeatureQueueItem queueItem;
    bool shouldEnqueue = false;

    {
        std::lock_guard<std::mutex> lock(m_mutex);

        SessionState& state = GetOrCreateSessionLocked(event);
        UpdateCommonEventFieldsLocked(state, event);

        if (event.type == NetworkEventType::SessionStarted)
        {
            HandleSessionStartedLocked(state, event);
            UpdateConnectionDurationLocked(state, event);
            featureQueue = m_featureQueue;
        }
        else
        {
            UpdateConnectionDurationLocked(state, event);

            switch (event.type)
            {
            case NetworkEventType::SessionClosed:
                HandleSessionClosedLocked(state, event);
                queueItem.type = FeatureQueueItemType::SessionSummary;
                queueItem.session = state.session;
                shouldEnqueue = true;
                break;
            case NetworkEventType::RecvCompleted:
                HandleRecvCompletedLocked(state, event);
                break;
            case NetworkEventType::SendCompleted:
                HandleSendCompletedLocked(state, event);
                break;
            case NetworkEventType::PacketParsed:
                HandlePacketParsedLocked(state, event);
                queueItem.type = FeatureQueueItemType::MessageSnapshot;
                queueItem.message = state.latest_message;
                shouldEnqueue = true;
                break;
            case NetworkEventType::ProtocolError:
                HandleProtocolErrorLocked(state, event);
                break;
            default:
                break;
            }

            featureQueue = m_featureQueue;
        }
    }

    if (featureQueue && shouldEnqueue)
        featureQueue->TryPush(queueItem);
}

void FeatureCollector::SetFeatureQueue(std::shared_ptr<AsyncFeatureQueue> featureQueue)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_featureQueue = featureQueue;
}

bool FeatureCollector::GetSessionSnapshot(std::uint64_t sessionId, SessionFeatureSnapshot& outSnapshot) const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    const auto it = m_sessions.find(sessionId);
    if (it == m_sessions.end())
        return false;

    outSnapshot = it->second.session;
    return true;
}

bool FeatureCollector::GetLatestMessageSnapshot(std::uint64_t sessionId, MessageFeatureSnapshot& outSnapshot) const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    const auto it = m_sessions.find(sessionId);
    if (it == m_sessions.end() || !it->second.has_latest_message)
        return false;

    outSnapshot = it->second.latest_message;
    return true;
}

std::vector<SessionFeatureSnapshot> FeatureCollector::GetAllSessionSnapshots() const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    std::vector<SessionFeatureSnapshot> snapshots;
    snapshots.reserve(m_sessions.size());

    for (const auto& item : m_sessions)
    {
        snapshots.push_back(item.second.session);
    }

    return snapshots;
}

void FeatureCollector::RemoveSession(std::uint64_t sessionId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sessions.erase(sessionId);
}

void FeatureCollector::Clear()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sessions.clear();
}

FeatureCollector::SessionState& FeatureCollector::GetOrCreateSessionLocked(const NetworkEvent& event)
{
    SessionState& state = m_sessions[event.session_id];

    if (state.session.session_id == 0)
    {
        state.session.session_id = event.session_id;
        state.session.socket_handle = event.socket_handle;
        state.session.logic_worker_id = event.logic_worker_id;
        state.session.connection_start_wall_time = event.wall_time;
        state.connection_start_time = event.monotonic_time;
        state.has_start_time = true;
    }

    return state;
}

void FeatureCollector::HandleSessionStartedLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.is_started = true;
    state.session.is_closed = false;
    state.session.connection_start_wall_time = event.wall_time;
    state.connection_start_time = event.monotonic_time;
    state.has_start_time = true;
}

void FeatureCollector::HandleSessionClosedLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.is_closed = true;
    state.session.connection_close_wall_time = event.wall_time;
    UpdateConnectionDurationLocked(state, event);
}

void FeatureCollector::HandleRecvCompletedLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.recv_event_count += 1;
    state.session.bytes_received_total += event.bytes_transferred;
}

void FeatureCollector::HandleSendCompletedLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.send_event_count += 1;
    state.session.bytes_sent_total += event.bytes_transferred;
}

void FeatureCollector::HandlePacketParsedLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.request_count += 1;
    state.session.last_packet_id = event.packet_id;
    state.session.last_packet_size = event.packet_size;
    state.session.last_payload_size = event.payload_size;

    double intervalMs = 0.0;
    if (state.has_last_message_time)
    {
        intervalMs = ToMilliseconds(event.monotonic_time - state.last_message_time);
        state.message_interval_sum_ms += intervalMs;
    }

    state.session.last_message_interval_ms = intervalMs;
    if (state.session.request_count > 1)
    {
        state.session.avg_message_interval_ms =
            state.message_interval_sum_ms / static_cast<double>(state.session.request_count - 1);
    }

    state.last_message_time = event.monotonic_time;
    state.has_last_message_time = true;

    MessageFeatureSnapshot message;
    message.session_id = state.session.session_id;
    message.socket_handle = state.session.socket_handle;
    message.logic_worker_id = event.logic_worker_id;
    message.message_wall_time = event.wall_time;
    message.request_index = state.session.request_count;
    message.packet_id = event.packet_id;
    message.packet_size = event.packet_size;
    message.payload_size = event.payload_size;
    message.connection_duration_ms = state.session.connection_duration_ms;
    message.message_interval_ms = intervalMs;
    message.bytes_received_total = state.session.bytes_received_total;
    message.bytes_sent_total = state.session.bytes_sent_total;
    message.error_count_total = state.session.error_count;

    state.latest_message = message;
    state.has_latest_message = true;
}

void FeatureCollector::HandleProtocolErrorLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.error_count += 1;
    state.session.last_error_code = event.error_code;
    state.session.last_error_message = event.error_message;

    if (event.packet_id >= 0)
        state.session.last_packet_id = event.packet_id;

    if (event.packet_size > 0)
        state.session.last_packet_size = event.packet_size;

    if (event.payload_size > 0)
        state.session.last_payload_size = event.payload_size;
}

void FeatureCollector::UpdateCommonEventFieldsLocked(SessionState& state, const NetworkEvent& event)
{
    state.session.socket_handle = event.socket_handle;
    state.session.logic_worker_id = event.logic_worker_id;
    state.session.last_event_wall_time = event.wall_time;
}

void FeatureCollector::UpdateConnectionDurationLocked(SessionState& state, const NetworkEvent& event)
{
    if (!state.has_start_time)
        return;

    state.session.connection_duration_ms = ToMilliseconds(event.monotonic_time - state.connection_start_time);
}

double FeatureCollector::ToMilliseconds(std::chrono::steady_clock::duration duration)
{
    return std::chrono::duration<double, std::milli>(duration).count();
}
}
