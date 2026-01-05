#include "pch.h"
#include "IOCPWorkerPool.h"
#include "Session.h"

IOCPWorkerPool::IOCPWorkerPool(IocpCore* pCore, int32 i32IoThreadCnt, int32 i32LogicThreadCount) :
	m_pCore(pCore), m_i32IoThreadCount(i32IoThreadCnt), m_i32LogicThreadCount(i32LogicThreadCount)
{
	m_vWorkerQueues.resize(m_i32LogicThreadCount);

	for (int i = 0; i < m_i32LogicThreadCount; ++i)
		m_vWorkerQueues[i] = make_unique<stWorkerQueue>();
}

IOCPWorkerPool::~IOCPWorkerPool()
{
	Stop();
}

void IOCPWorkerPool::Start()
{
	m_bRunning = true;

	for (int i = 0; i < m_i32LogicThreadCount; ++i)
	{
		/*std::thread th([this]()
			{
				this->WorkerThreadLoop();
			});
		m_vThreads.emplace_back(th);*/

		// 위처럼 한걸 다음처럼 간결화 할 수 있음.
		m_vLogicThreads.emplace_back([this, i]()
			{
				this->LogicWorkerThreadLoop(i);
			});
	}

	for (int i = 0; i < m_i32IoThreadCount; ++i)
	{
		m_vIoThreads.emplace_back([this, i]()
			{
				this->IoWorkerThreadLoop(i);
			});
	}

	std::cout << "[IOCPWorkerPool] Started with " << m_i32LogicThreadCount << " Logic threads.\n";
	std::cout << "[IOCPWorkerPool] Started with " << m_i32IoThreadCount << " IO threads.\n";
}

void IOCPWorkerPool::Stop()
{
	m_bRunning = false;

	// IOCP 깨우기: dummy I/O를 발생시켜 GetQueuedCompletionStatus를 탈출하게 함
	for (int i = 0; i < m_i32IoThreadCount; ++i)
	{
		// 이벤트를 발생시키면 → GQCS()가 리턴됨
		// 그때 m_running이 false니까 → while문 탈출
		::PostQueuedCompletionStatus(m_pCore->GetHandle(), 0, 0, nullptr);
	}

	for (auto& queuePtr : m_vWorkerQueues)
	{
		auto& queue = *queuePtr;
		unique_lock<mutex> lock(queue.m_mutex);
		queue.m_cv.notify_all();
	}

	for (auto& t : m_vIoThreads)
	{
		if (t.joinable())
			t.join();
	}

	for (auto& t : m_vLogicThreads)
	{
		if (t.joinable())
			t.join();
	}

	m_vIoThreads.clear();
	m_vLogicThreads.clear();
}

void IOCPWorkerPool::IoWorkerThreadLoop(int32 i32_IoWorkerID)
{
	constexpr uint32 IocpPollTimeoutMs = 10; // 큐 확인을 위해 짧은 타임아웃 사용

	while (m_bRunning)
	{	
		DWORD bytes = 0;
		ULONG_PTR completionKey = 0;
		IocpEvent* pEvent = nullptr;

		if (!m_pCore->Dequeue(bytes, completionKey, pEvent, IocpPollTimeoutMs))
			continue;

		// Accept이벤트인 경우 IO 스레드 ID 저장
		if (pEvent && pEvent->GetType() == IocpEvent::Type::Accept)
		{
			pEvent->m_stIoData.SetIoThreadID(i32_IoWorkerID);
		}

		int32 targetWorker = SelectLogicWorker(pEvent);
		EnqueueToWorker(targetWorker, pEvent, bytes);
	}

	std::cout << "[IOCPWorker] IO thread Exit.\n";
}

void IOCPWorkerPool::LogicWorkerThreadLoop(int32 i32_LogicWorkerID)
{
	constexpr uint32 IocpPollTimeoutMs = 10; // 큐 확인을 위해 짧은 타임아웃 사용

	while (m_bRunning)
	{
		stIocpCompletion completion;
		if (WaitPopLocal(i32_LogicWorkerID, completion))
			continue;

		DispatchOrForward(i32_LogicWorkerID, completion.pEvent, completion.dwBytes);

		//// 내부적으로 GQCS() 대기 중
		//bool success = m_pCore->Dispatch(); // 기존의 Dispatch 호출해줌
		//if (!success)
		//{
		//	// 로그만 찍고 계속 루프
		//	std::cout << "[IOCPWorker] Dispatch returned false.\n";
		//}
	}

	// 남은 로컬 큐 정리
	stIocpCompletion completion;
	while (WaitPopLocal(i32_LogicWorkerID, completion))
	{
		DispatchOrForward(i32_LogicWorkerID, completion.pEvent, completion.dwBytes);
	}

	std::cout << "[IOCPWorker] Logic thread Exit.\n";
}

bool IOCPWorkerPool::WaitPopLocal(int32 _i32WorkerID, stIocpCompletion& outItem)
{
	stWorkerQueue& queue = *m_vWorkerQueues[_i32WorkerID];
	
	unique_lock<mutex> lock(queue.m_mutex);
	queue.m_cv.wait(lock, [this, &queue]()
		{
			return !m_bRunning || !queue.dqPending.empty();
		});


	if (queue.dqPending.empty())
		return false;

	outItem = queue.dqPending.front();
	queue.dqPending.pop_front();
	return false;
}

void IOCPWorkerPool::EnqueueToWorker(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes)
{
	if (_i32WorkerID < 0 || _i32WorkerID >= m_i32LogicThreadCount)
	{
		// 잘못된 워커 번호면 현재 워커가 그냥 처리
		shared_ptr<IocpObject> pOwner = _pEvent->GetOwner();
		pOwner->Dispatch(_pEvent, _dwNumOfBytes);
		return;
	}

	stWorkerQueue& queue = *m_vWorkerQueues[_i32WorkerID];
	{
		lock_guard<mutex> lock(queue.m_mutex);
		queue.dqPending.push_back({ _pEvent, _dwNumOfBytes });
	}
	queue.m_cv.notify_one();
}

void IOCPWorkerPool::DispatchOrForward(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes)
{
	shared_ptr<IocpObject> pOwner = _pEvent->GetOwner();
	shared_ptr<Session> pSession = dynamic_pointer_cast<Session>(pOwner);

	if (!pSession)
	{
		pOwner->Dispatch(_pEvent, _dwNumOfBytes);
		return;
	}

	int32 bound = pSession->GetBoundWorkerID();
	if (bound == -1)
	{
		if (pSession->TryBindWorker(_i32WorkerID))
			bound = _i32WorkerID;
		else
			bound = pSession->GetBoundWorkerID();
	}

	if (bound == -1)
		bound = _i32WorkerID;

	if (bound != _i32WorkerID)
	{
		EnqueueToWorker(bound, _pEvent, _dwNumOfBytes);
		return;
	}

	pOwner->Dispatch(_pEvent, _dwNumOfBytes);
}

int32 IOCPWorkerPool::SelectLogicWorker(IocpEvent* _pEvent)
{
	if (_pEvent == nullptr || m_i32LogicThreadCount <= 0)
		return 0;

	shared_ptr<IocpObject> pOwner = _pEvent->GetOwner();
	shared_ptr<Session> pSession = dynamic_pointer_cast<Session>(pOwner);

	if (pSession)
	{
		int32 bound = pSession->GetBoundWorkerID();
		if (bound >= 0)
			return bound;

		int32 candidate = static_cast<int32>(m_ui32LogicRoundRobin.fetch_add(1) % m_i32LogicThreadCount);
		if (pSession->TryBindWorker(candidate))
			return candidate;

		bound = pSession->GetBoundWorkerID();
		if (bound >= 0)
			return bound;
		
		return candidate;
	}

	return static_cast<int32>(m_ui32LogicRoundRobin.fetch_add(1) % m_i32LogicThreadCount);
}
