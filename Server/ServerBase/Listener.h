#pragma once
#include "IocpCore.h"
#include "Session.h"

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

private:
	void Init(IocpCore* core, int32 ioThreadCount);
	bool BindWindowsFunction(SOCKET socket, GUID guid, LPVOID* fn);

	void PostAccept(IocpEvent* pAcceptEvent, int32 ioThreadID);

private: 
	SOCKET m_listenSocket;
	IocpCore* m_core;

	// IO Thread별로 이벤트 들고있게 수정
	// vector<IocpEvent*> m_vlistenerEvent;
	vector<unique_ptr<stThreadEventPool>> m_vThreadPools;
	int32 m_i32IoThreadCnt = 0;
};

