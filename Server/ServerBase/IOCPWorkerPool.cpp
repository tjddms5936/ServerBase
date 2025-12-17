#include "pch.h"
#include "IOCPWorkerPool.h"
#include "Session.h"

IOCPWorkerPool::IOCPWorkerPool(IocpCore* pCore, int32 i32ThreadCount) :
	m_pCore(pCore), m_i32ThreadCount(i32ThreadCount)
{

}

IOCPWorkerPool::~IOCPWorkerPool()
{
	Stop();
}

void IOCPWorkerPool::Start()
{
	m_bRunning = true;

	for (int i = 0; i < m_i32ThreadCount; ++i)
	{
		/*std::thread th([this]()
			{
				this->WorkerThreadLoop();
			});
		m_vThreads.emplace_back(th);*/

		// 위처럼 한걸 다음처럼 간결화 할 수 있음.
		m_vThreads.emplace_back([this, i]()
			{
				this->WorkerThreadLoop(i);
			});
	}

	std::cout << "[IOCPWorkerPool] Started with " << m_i32ThreadCount << " threads.\n";
}

void IOCPWorkerPool::Stop()
{
	m_bRunning = false;

	// IOCP 깨우기: dummy I/O를 발생시켜 GetQueuedCompletionStatus를 탈출하게 함
	for (int i = 0; i < m_i32ThreadCount; ++i)
	{
		// 이벤트를 발생시키면 → GQCS()가 리턴됨
		// 그때 m_running이 false니까 → while문 탈출
		::PostQueuedCompletionStatus(m_pCore->GetHandle(), 0, 0, nullptr);
	}

	for (auto& t : m_vThreads)
	{
		if (t.joinable())
			t.join();
	}

	m_vThreads.clear();
}

void IOCPWorkerPool::WorkerThreadLoop(int32 i32_WorkerID)
{
	constexpr uint32 IocpPollTimeoutMs = 10; // 큐 확인을 위해 짧은 타임아웃 사용

	while (m_bRunning)
	{
		stIocpCompletion completion;
		if (TryPopLocal(i32_WorkerID, completion))
		{
			DispatchOrForward(i32_WorkerID, completion.pEvent, completion.dwBytes);
			continue;
		}

		DWORD bytes = 0;
		ULONG_PTR completionKey = 0;
		IocpEvent* pEvent = nullptr;

		if (!m_pCore->Dequeue(bytes, completionKey, pEvent, IocpPollTimeoutMs))
			continue;

		DispatchOrForward(i32_WorkerID, pEvent, bytes);

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
	while (TryPopLocal(i32_WorkerID, completion))
	{
		DispatchOrForward(i32_WorkerID, completion.pEvent, completion.dwBytes);
	}

	std::cout << "[IOCPWorker] Exit.\n";
}

bool IOCPWorkerPool::TryPopLocal(int32 _i32WorkerID, stIocpCompletion& outItem)
{
	stWorkerQueue& queue = m_vWorkerQueues[_i32WorkerID];
	
	unique_lock<mutex> lock(queue.m_mutex);
	if (queue.dqPending.empty())
		return false;

	outItem = queue.dqPending.front();
	queue.dqPending.pop_front();
	return false;
}

void IOCPWorkerPool::EnqueueToWorker(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes)
{
	if (_i32WorkerID < 0 || _i32WorkerID >= m_i32ThreadCount)
	{
		// 잘못된 워커 번호면 현재 워커가 그냥 처리
		shared_ptr<IocpObject> pOwner = _pEvent->GetOwner();
		pOwner->Dispatch(_pEvent, _dwNumOfBytes);
		return;
	}

	stWorkerQueue& queue = m_vWorkerQueues[_i32WorkerID];
	{
		lock_guard<mutex> lock(queue.m_mutex);
		queue.dqPending.push_back({ _pEvent, _dwNumOfBytes });
	}
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
		EnqueueToWorker(_i32WorkerID, _pEvent, _dwNumOfBytes);
		return;
	}

	pOwner->Dispatch(_pEvent, _dwNumOfBytes);
}
