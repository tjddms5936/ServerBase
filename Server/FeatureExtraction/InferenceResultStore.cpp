#include "InferenceResultStore.h"

namespace feature_extraction
{
void InferenceResultStore::Update(const AiInferenceResult& result)
{
    std::lock_guard<std::mutex> lock(m_mutex);

    SessionInferenceState& state = m_sessions[result.session_id];
    if (!state.has_latest_result)
    {
        state.session_id = result.session_id;
        state.first_result_wall_time = result.received_wall_time;
    }

    state.has_latest_result = true;
    state.latest_result = result;
    state.total_result_count += 1;
    if (result.is_attack)
        state.attack_result_count += 1;

    state.last_result_wall_time = result.received_wall_time;
}

bool InferenceResultStore::TryGetLatest(std::uint64_t sessionId, AiInferenceResult& outResult) const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    const auto it = m_sessions.find(sessionId);
    if (it == m_sessions.end() || !it->second.has_latest_result)
        return false;

    outResult = it->second.latest_result;
    return true;
}

bool InferenceResultStore::TryGetSessionState(std::uint64_t sessionId, SessionInferenceState& outState) const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    const auto it = m_sessions.find(sessionId);
    if (it == m_sessions.end())
        return false;

    outState = it->second;
    return true;
}

std::vector<SessionInferenceState> InferenceResultStore::SnapshotAll() const
{
    std::lock_guard<std::mutex> lock(m_mutex);

    std::vector<SessionInferenceState> snapshot;
    snapshot.reserve(m_sessions.size());
    for (const auto& pair : m_sessions)
        snapshot.push_back(pair.second);

    return snapshot;
}

std::size_t InferenceResultStore::SessionCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_sessions.size();
}

void InferenceResultStore::Clear()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sessions.clear();
}
}
