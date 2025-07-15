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

		// ��ó�� �Ѱ� ����ó�� ����ȭ �� �� ����.
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

	// IOCP �����: dummy I/O�� �߻����� GetQueuedCompletionStatus�� Ż���ϰ� ��
	for (int i = 0; i < m_i32ThreadCount; ++i)
	{
		// �̺�Ʈ�� �߻���Ű�� �� GQCS()�� ���ϵ�
		// �׶� m_running�� false�ϱ� �� while�� Ż��
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
		// ���������� GQCS() ��� ��
		bool success = m_pCore->Dispatch(); // ������ Dispatch ȣ������
		if (!success)
		{
			// �α׸� ��� ��� ����
			std::cout << "[IOCPWorker] Dispatch returned false.\n";
		}
	}

	std::cout << "[IOCPWorker] Exit.\n";
}
