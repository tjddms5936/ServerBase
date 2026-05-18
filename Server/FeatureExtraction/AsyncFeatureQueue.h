#pragma once

#include "FeatureSnapshot.h"

#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>

namespace feature_extraction
{
enum class FeatureQueueItemType
{
    MessageSnapshot,    // 패킷 하나에서 만들어진 메시지 단위 특징
    SessionSummary,     // 세션 종료 시점의 세션 단위 요약 특징
};

struct FeatureQueueItem
{
    FeatureQueueItemType type = FeatureQueueItemType::MessageSnapshot;
    std::uint64_t queue_sequence_id = 0;    // 큐에 들어간 순서를 추적하기 위한 번호

    MessageFeatureSnapshot message;
    SessionFeatureSnapshot session;
};

class AsyncFeatureQueue
{
public:
    explicit AsyncFeatureQueue(std::size_t capacity);
    ~AsyncFeatureQueue() = default;

    AsyncFeatureQueue(const AsyncFeatureQueue&) = delete;
    AsyncFeatureQueue& operator=(const AsyncFeatureQueue&) = delete;

    bool TryPush(const FeatureQueueItem& item);
    bool WaitPush(const FeatureQueueItem& item);

    bool TryPop(FeatureQueueItem& outItem);
    bool WaitPop(FeatureQueueItem& outItem);

    void Close();
    void Clear();

    bool IsClosed() const;
    std::size_t Size() const;
    std::size_t Capacity() const;
    std::uint64_t DroppedCount() const;

private:
    bool IsFullLocked() const;
    FeatureQueueItem MakeQueuedItemLocked(const FeatureQueueItem& item);

private:
    const std::size_t m_capacity;
    mutable std::mutex m_mutex;
    std::condition_variable m_notEmpty;
    std::condition_variable m_notFull;
    std::deque<FeatureQueueItem> m_queue;
    bool m_closed = false;
    std::uint64_t m_nextSequenceId = 0;
    std::uint64_t m_droppedCount = 0;
};
}
