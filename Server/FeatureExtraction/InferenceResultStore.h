#pragma once

#include "InferenceResult.h"

#include <cstdint>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace feature_extraction
{
class InferenceResultStore
{
public:
    void Update(const AiInferenceResult& result);
    bool TryGetLatest(std::uint64_t sessionId, AiInferenceResult& outResult) const;
    bool TryGetSessionState(std::uint64_t sessionId, SessionInferenceState& outState) const;
    std::vector<SessionInferenceState> SnapshotAll() const;
    std::size_t SessionCount() const;
    void Clear();

private:
    mutable std::mutex m_mutex; // worker thread와 조회 thread가 동시에 접근할 수 있으므로 보호한다.
    std::unordered_map<std::uint64_t, SessionInferenceState> m_sessions;
};
}
