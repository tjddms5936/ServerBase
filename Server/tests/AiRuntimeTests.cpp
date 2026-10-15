#include "../FeatureExtraction/AiRuntimeSupport.h"

#include <future>
#include <stdexcept>
#include <thread>

using namespace feature_extraction;

namespace
{
void Check(bool value, const char* message)
{
    if (!value) throw std::runtime_error(message);
}

void TestQueueCloseAndCapacity()
{
    AsyncFeatureQueue queue(1);
    FeatureQueueItem item;
    Check(queue.TryPush(item), "first enqueue");
    Check(!queue.TryPush(item) && queue.DroppedCount() == 1, "bounded drop");
    auto producer = std::async(std::launch::async, [&]() { return queue.WaitPush(item); });
    Check(producer.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout, "producer must wait");
    queue.Close();
    Check(!producer.get(), "close must wake producer");
    Check(queue.WaitPop(item) && item.queue_sequence_id == 1, "close must preserve accepted item");
    Check(!queue.WaitPop(item), "closed empty queue must stop consumer");
    AsyncFeatureQueue empty(1);
    auto consumer = std::async(std::launch::async, [&]() { FeatureQueueItem value; return empty.WaitPop(value); });
    empty.Close();
    Check(!consumer.get(), "close must wake consumer");
}

void TestConcurrentProducerBound()
{
    AsyncFeatureQueue queue(7);
    std::vector<std::thread> producers;
    for (int i = 0; i < 8; ++i)
        producers.emplace_back([&]() { for (int j = 0; j < 100; ++j) queue.TryPush(FeatureQueueItem{}); });
    for (auto& producer : producers) producer.join();
    Check(queue.Size() == 7 && queue.DroppedCount() == 793, "concurrent capacity accounting");
    FeatureQueueItem item;
    std::uint64_t last = 0;
    while (queue.TryPop(item)) Check(item.queue_sequence_id == ++last, "accepted sequence order");
}

void TestWorkerLifecycleAndSummary()
{
    auto queue = std::make_shared<AsyncFeatureQueue>(2);
    AiInferenceWorkerConfig config;
    config.message_only = true;
    AiInferenceWorker worker(queue, config);
    FeatureQueueItem item;
    item.type = FeatureQueueItemType::SessionSummary;
    queue->TryPush(item);
    Check(worker.Start() && !worker.Start(), "start ownership");
    std::thread first([&]() { worker.Stop(); });
    std::thread second([&]() { worker.Stop(); });
    first.join(); second.join();
    Check(!worker.IsRunning() && !worker.Start(), "closed worker must not restart");
    Check(worker.SkippedSummaryCount() == 1 && worker.FailedCount() == 0 && worker.StoredResultCount() == 0,
        "summary must not perform HTTP or create result");
}

void TestInvalidConfigurationAndMissingResult()
{
    bool rejected = false;
    try { AsyncFeatureQueue queue(0); } catch (const std::invalid_argument&) { rejected = true; }
    Check(rejected, "zero capacity");
    rejected = false;
    try
    {
        AiInferenceWorkerConfig config;
        config.receive_timeout_ms = 0;
        AiInferenceWorker worker(std::make_shared<AsyncFeatureQueue>(1), config);
    }
    catch (const std::invalid_argument&) { rejected = true; }
    Check(rejected, "unbounded timeout");
    InferenceResultStore store;
    AiInferenceResult result;
    Check(!store.TryGetLatest(123, result), "missing inference must not mean normal");
    result.session_id = 123;
    result.request_index = 1;
    result.request_id = "1";
    store.Update(result);
    Check(store.TryGetLatest(123, result) && result.request_index == 1, "last successful result");
    Check(!store.TryGetLatest(456, result), "session isolation");
}
}

int main()
{
    try
    {
        TestQueueCloseAndCapacity();
        TestConcurrentProducerBound();
        TestWorkerLifecycleAndSummary();
        TestInvalidConfigurationAndMissingResult();
        std::cout << "PASS: 4 runtime groups (queue, concurrency, lifecycle, missing result)\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
