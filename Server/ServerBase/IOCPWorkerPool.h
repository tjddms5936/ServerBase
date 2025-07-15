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
	atomic<bool> m_bRunning = false; // atomic<bool>�� ���� �����ϴ� �� 1�����忩��, �д� �����尡 2�� �̻��̸� ������ atomic�� �´�.
};

