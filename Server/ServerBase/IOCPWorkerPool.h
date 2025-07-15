#pragma once
#include "IocpCore.h"


class IOCPWorkerPool
{
public:
	IOCPWorkerPool(IocpCore* pCore, int32 i32ThreadCount);
	~IOCPWorkerPool();

	void Start();
	void Stop();

private:
	void WorkerThreadLoop();

private:
	IocpCore* m_pCore;
	int32 m_i32ThreadCount;
	vector<thread> m_vThreads;
	atomic<bool> m_bRunning = false; // atomic<bool>은 값을 변경하는 건 1스레드여도, 읽는 스레드가 2개 이상이면 무조건 atomic이 맞다.
};

