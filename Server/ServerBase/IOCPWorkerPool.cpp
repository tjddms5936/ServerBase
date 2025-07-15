#include "pch.h"
#include "IOCPWorkerPool.h"

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
		m_vThreads.emplace_back([this]()
			{
				this->WorkerThreadLoop();
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

void IOCPWorkerPool::WorkerThreadLoop()
{
	while (m_bRunning)
	{
		// 내부적으로 GQCS() 대기 중
		bool success = m_pCore->Dispatch(); // 기존의 Dispatch 호출해줌
		if (!success)
		{
			// 로그만 찍고 계속 루프
			std::cout << "[IOCPWorker] Dispatch returned false.\n";
		}
	}

	std::cout << "[IOCPWorker] Exit.\n";
}
