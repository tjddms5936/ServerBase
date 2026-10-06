#include "../FeatureExtraction/FeatureCollector.h"
#include "../FeatureExtraction/AsyncFeatureQueue.h"

#include <iostream>
#include <stdexcept>
#include <thread>

using namespace feature_extraction;

namespace
{
void Check(bool condition, const char* message)
{
    if (!condition)
        throw std::runtime_error(message);
}

NetworkEvent Event(NetworkEventType type, int milliseconds)
{
    NetworkEvent event;
    event.type = type;
    event.session_id = 1;
    event.socket_handle = 123;
    event.logic_worker_id = 0;
    event.monotonic_time = std::chrono::steady_clock::time_point{} + std::chrono::milliseconds(milliseconds);
    event.wall_time = std::chrono::system_clock::time_point{} + std::chrono::milliseconds(milliseconds);
    event.bytes_transferred = 48;
    event.packet_id = 0;
    event.packet_size = 24;
    event.payload_size = 16;
    event.error_message = "test error";
    return event;
}

void TestNormalAndFrozenSnapshot()
{
    FeatureCollector collector;
    auto queue = std::make_shared<AsyncFeatureQueue>(16);
    collector.SetFeatureQueue(queue);
    collector.OnNetworkEvent(Event(NetworkEventType::SessionStarted, 1000));
    collector.OnNetworkEvent(Event(NetworkEventType::RecvCompleted, 1100));
    collector.OnNetworkEvent(Event(NetworkEventType::PacketParsed, 1120));
    auto sent = Event(NetworkEventType::SendCompleted, 1130);
    sent.bytes_transferred = 20;
    collector.OnNetworkEvent(sent);
    collector.OnNetworkEvent(Event(NetworkEventType::PacketParsed, 1150));
    collector.OnNetworkEvent(Event(NetworkEventType::ProtocolError, 1200));
    collector.OnNetworkEvent(Event(NetworkEventType::SessionClosed, 1250));

    SessionFeatureSnapshot before;
    Check(collector.GetSessionSnapshot(1, before), "session missing");
    Check(before.is_closed && before.is_started, "session flags");
    Check(before.connection_duration_ms == 250.0, "duration");
    Check(before.bytes_received_total == 48 && before.bytes_sent_total == 20, "byte totals");
    Check(before.request_count == 2 && before.error_count == 1, "message/error counters");
    Check(before.avg_message_interval_ms == 30.0, "mean interval");

    FeatureQueueItem first, second, summary;
    Check(queue->TryPop(first) && queue->TryPop(second) && queue->TryPop(summary), "three snapshots expected");
    Check(first.type == FeatureQueueItemType::MessageSnapshot && first.message.message_interval_ms == 0, "first interval");
    Check(second.message.request_index == 2 && second.message.message_interval_ms == 30, "second interval");
    Check(second.message.error_count_total == 0, "future error leaked into message");
    Check(summary.type == FeatureQueueItemType::SessionSummary && summary.session.error_count == 1, "close summary");

    // 종료 뒤 어떤 이벤트가 와도 모델 입력과 마지막 메시지는 변경하지 않는다.
    for (auto type : {NetworkEventType::RecvCompleted, NetworkEventType::SendCompleted,
        NetworkEventType::ProtocolError, NetworkEventType::PacketParsed,
        NetworkEventType::SessionClosed, NetworkEventType::SessionStarted})
        collector.OnNetworkEvent(Event(type, 2000));

    SessionFeatureSnapshot after;
    Check(collector.GetSessionSnapshot(1, after), "frozen session missing");
    Check(after.is_closed && after.connection_duration_ms == before.connection_duration_ms, "close reopened or duration changed");
    Check(after.bytes_received_total == before.bytes_received_total && after.bytes_sent_total == before.bytes_sent_total, "late bytes changed snapshot");
    Check(after.error_count == before.error_count && after.request_count == before.request_count, "late counters changed snapshot");
    Check(after.last_event_wall_time == before.last_event_wall_time, "late timestamp changed snapshot");
    Check(after.connection_close_wall_time == before.connection_close_wall_time, "duplicate close changed snapshot");
    Check(queue->Size() == 0 && collector.IgnoredAfterCloseEventCount() == 6, "late events queued or not counted");

    std::vector<std::thread> threads;
    for (int i = 0; i < 8; ++i)
        threads.emplace_back([&collector]() {
            for (int j = 0; j < 50; ++j)
                collector.OnNetworkEvent(Event(NetworkEventType::ProtocolError, 3000));
        });
    for (auto& thread : threads)
        thread.join();
    Check(collector.IgnoredAfterCloseEventCount() == 406, "concurrent late counter");
    MessageFeatureSnapshot message;
    Check(collector.GetLatestMessageSnapshot(1, message) && message.request_index == 2, "late message replaced latest");
    collector.Clear();
    Check(collector.GetAllSessionSnapshots().empty() && collector.IgnoredAfterCloseEventCount() == 0, "clear statistics");
}

void TestBoundedDropStillClosesSession()
{
    FeatureCollector collector;
    auto queue = std::make_shared<AsyncFeatureQueue>(1);
    collector.SetFeatureQueue(queue);
    collector.OnNetworkEvent(Event(NetworkEventType::SessionStarted, 1000));
    collector.OnNetworkEvent(Event(NetworkEventType::PacketParsed, 1100));
    collector.OnNetworkEvent(Event(NetworkEventType::SessionClosed, 1200));
    SessionFeatureSnapshot session;
    Check(collector.GetSessionSnapshot(1, session) && session.is_closed, "full queue prevented close");
    collector.OnNetworkEvent(Event(NetworkEventType::SessionClosed, 1300));
    Check(queue->Size() == 1 && collector.IgnoredAfterCloseEventCount() == 1, "duplicate summary entered full queue");
}

void TestEmptyAndSingleMessageMean()
{
    for (int messageCount = 0; messageCount <= 1; ++messageCount)
    {
        FeatureCollector collector;
        collector.OnNetworkEvent(Event(NetworkEventType::SessionStarted, 1000));
        if (messageCount)
            collector.OnNetworkEvent(Event(NetworkEventType::PacketParsed, 1100));
        collector.OnNetworkEvent(Event(NetworkEventType::SessionClosed, 1200));
        SessionFeatureSnapshot session;
        Check(collector.GetSessionSnapshot(1, session), "short session missing");
        Check(session.avg_message_interval_ms == 0, "short session mean");
    }
}
}

int main()
{
    try
    {
        TestNormalAndFrozenSnapshot();
        TestBoundedDropStillClosesSession();
        TestEmptyAndSingleMessageMean();
        std::cout << "PASS: collector snapshots, late/concurrent events, bounded drop, short-session intervals\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
