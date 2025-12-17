#pragma once
#include "IocpCore.h"

struct stIocpCompletion
{
	IocpEvent* pEvent = nullptr;
	DWORD dwBytes = 0;
};

class IOCPWorkerPool
{
public:
	IOCPWorkerPool(IocpCore* pCore, int32 i32ThreadCount);
	~IOCPWorkerPool();

	void Start();
	void Stop();

private:
	struct stWorkerQueue
	{
		mutex m_mutex;
		deque<stIocpCompletion> dqPending;
	};

	void WorkerThreadLoop(int32 i32_WorkerID);
	bool TryPopLocal(int32 _i32WorkerID, stIocpCompletion& outItem);
	void EnqueueToWorker(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes);
	void DispatchOrForward(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes);

private:
	IocpCore* m_pCore;
	int32 m_i32ThreadCount;
	vector<thread> m_vThreads;
	atomic<bool> m_bRunning = false; // atomic<bool>은 값을 변경하는 건 1스레드여도, 읽는 스레드가 2개 이상이면 무조건 atomic이 맞다.

	vector<stWorkerQueue> m_vWorkerQueues;
};

