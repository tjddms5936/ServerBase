#pragma once
#include "IocpCore.h"
#include "Session.h"

class AcceptRetryScheduler
{
private:
	struct stRetryItem
	{
		IocpEvent* pEvent;
		int32		IoThreadID;
		int32		retryCount;
		chrono::steady_clock::time_point nextRetryTime;
		int errCode; // 에러 코드 저장
	};

public:
	AcceptRetryScheduler() = default;
	~AcceptRetryScheduler() = default;
	AcceptRetryScheduler(const AcceptRetryScheduler&) = delete;
	AcceptRetryScheduler& operator =(const AcceptRetryScheduler&) = delete;

public:
	void RetryThreadLoop();
	void ScheduleRetry(int32 ioThreadID, IocpEvent* pEvent, int32 errCode);
	void SetOwner(Listener* pOwner) { m_pOwner = pOwner; }
	void StartRetry();
	void EndRetry();
private:
	mutex m_retryMutex;
	deque<stRetryItem> m_retryQeuue; // 재시도 대기 큐
	thread m_retryThread; // 재시도 전용 스레드
	condition_variable m_cvRetry;
	atomic<bool> m_bRetryRunning{ false };

	static constexpr int32 MAX_RETRY = 5;
	static constexpr int32 BASE_DELAY_MS = 100;
	static constexpr int32 MAX_DELAY_MS = 5000;

	Listener* m_pOwner = nullptr;
};

class Listener : public IocpObject
{
public:
	static LPFN_ACCEPTEX AcceptEx;

private:
	// 스레드별 이벤트 풀 구조
	struct stThreadEventPool
	{
		mutex				m_mutex;		// 풀 접근 보호
		deque<IocpEvent*>	m_freeEvents;	// 사용 가능한 이벤트
		vector<IocpEvent*>	m_allEvents;	// 모든 이벤트 (정리용)
	};

public:
	Listener();
	~Listener();

	virtual HANDLE GetHandle() override { return reinterpret_cast<HANDLE>(m_listenSocket); }
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) override;

	bool StartAccept(uint16 port, IocpCore* core, int32 ioTHreadCount);
	void Close();

	void OnAccept(IocpEvent* pIocpEvent, int32 numOfBytes);

	// 이벤트 획득/반환 API
	IocpEvent* AcquireAcceptEvent(int32 ioThreadID);
	void ReleaseAcceptEvent(int32 ioThreadID, IocpEvent* pEvent);

	void PostAccept(IocpEvent* pAcceptEvent, int32 ioThreadID);
private:
	void Init(IocpCore* core, int32 ioThreadCount);
	bool BindWindowsFunction(SOCKET socket, GUID guid, LPVOID* fn);


private: 
	SOCKET m_listenSocket;
	IocpCore* m_core;

	// IO Thread별로 이벤트 들고있게 수정
	// vector<IocpEvent*> m_vlistenerEvent;
	vector<unique_ptr<stThreadEventPool>> m_vThreadPools;
	int32 m_i32IoThreadCnt = 0;

	AcceptRetryScheduler m_AcceptScheduler;
};

