#pragma once
#include "IocpCore.h"

class IOCPWorkerPool
{
public:
	IOCPWorkerPool(IocpCore* pCore, int32 i32IoThreadCnt,int32 i32LogicThreadCount);
	~IOCPWorkerPool();

	void Start();
	void Stop();
	static int32 GetCurrentLogicWorkerID();

private:
	struct stIocpCompletion
	{
		IocpEvent* pEvent = nullptr;
		DWORD dwBytes = 0;
	};

	struct stWorkerQueue
	{
		mutex m_mutex;
		deque<stIocpCompletion> dqPending;
		condition_variable m_cv;
	};


	void IoWorkerThreadLoop(int32 i32_IoWorkerID);
	void LogicWorkerThreadLoop(int32 i32_LogicWorkerID);
	bool WaitPopLocal(int32 _i32WorkerID, stIocpCompletion& outItem);
	void EnqueueToWorker(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes);
	void DispatchOrForward(int32 _i32WorkerID, IocpEvent* _pEvent, DWORD _dwNumOfBytes);
	int32 SelectLogicWorker(IocpEvent* _pEvent);

private:
	IocpCore* m_pCore;
	int32 m_i32IoThreadCount;
	int32 m_i32LogicThreadCount;
	vector<thread> m_vIoThreads;
	vector<thread> m_vLogicThreads;
	atomic<bool> m_bRunning = false; // atomic<bool>은 값을 변경하는 건 1스레드여도, 읽는 스레드가 2개 이상이면 무조건 atomic이 맞다.

	vector<unique_ptr<stWorkerQueue>> m_vWorkerQueues;
	atomic<uint32> m_ui32LogicRoundRobin = 0;
};
