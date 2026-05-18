#include "AsyncFeatureQueue.h"

#include <stdexcept>

namespace feature_extraction
{
AsyncFeatureQueue::AsyncFeatureQueue(std::size_t capacity)
    : m_capacity(capacity)
{
    if (m_capacity == 0)
        throw std::invalid_argument("AsyncFeatureQueue capacity must be greater than zero");
}

bool AsyncFeatureQueue::TryPush(const FeatureQueueItem& item)
{
    std::lock_guard<std::mutex> lock(m_mutex);

    if (m_closed || IsFullLocked())
    {
        m_droppedCount += 1;
        return false;
    }

    m_queue.push_back(MakeQueuedItemLocked(item));
    m_notEmpty.notify_one();
    return true;
}

bool AsyncFeatureQueue::WaitPush(const FeatureQueueItem& item)
{
    std::unique_lock<std::mutex> lock(m_mutex);

    m_notFull.wait(lock, [this]()
        {
            return m_closed || !IsFullLocked();
        });

    if (m_closed)
        return false;

    m_queue.push_back(MakeQueuedItemLocked(item));
    m_notEmpty.notify_one();
    return true;
}

bool AsyncFeatureQueue::TryPop(FeatureQueueItem& outItem)
{
    std::lock_guard<std::mutex> lock(m_mutex);

    if (m_queue.empty())
        return false;

    outItem = m_queue.front();
    m_queue.pop_front();
    m_notFull.notify_one();
    return true;
}

bool AsyncFeatureQueue::WaitPop(FeatureQueueItem& outItem)
{
    std::unique_lock<std::mutex> lock(m_mutex);

    m_notEmpty.wait(lock, [this]()
        {
            return m_closed || !m_queue.empty();
        });

    if (m_queue.empty())
        return false;

    outItem = m_queue.front();
    m_queue.pop_front();
    m_notFull.notify_one();
    return true;
}

void AsyncFeatureQueue::Close()
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_closed = true;
    }

    m_notEmpty.notify_all();
    m_notFull.notify_all();
}

void AsyncFeatureQueue::Clear()
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_queue.clear();
    }

    m_notFull.notify_all();
}

bool AsyncFeatureQueue::IsClosed() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_closed;
}

std::size_t AsyncFeatureQueue::Size() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_queue.size();
}

std::size_t AsyncFeatureQueue::Capacity() const
{
    return m_capacity;
}

std::uint64_t AsyncFeatureQueue::DroppedCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_droppedCount;
}

bool AsyncFeatureQueue::IsFullLocked() const
{
    return m_queue.size() >= m_capacity;
}

FeatureQueueItem AsyncFeatureQueue::MakeQueuedItemLocked(const FeatureQueueItem& item)
{
    FeatureQueueItem queuedItem = item;
    queuedItem.queue_sequence_id = ++m_nextSequenceId;
    return queuedItem;
}
}
