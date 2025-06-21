#pragma once
#include "IocpCore.h"
#include "Session.h"

class Listener : public IocpObject
{
public:
	static LPFN_ACCEPTEX AcceptEx;

public:
	Listener();
	~Listener();

	virtual HANDLE GetHandle() override { return reinterpret_cast<HANDLE>(m_listenSocket); }
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) override;

	bool StartAccept(uint16 port, IocpCore* core);
	void Close();

	void OnAccept(IocpEvent* pIocpEvent, int32 numOfBytes);

private:
	void Init(IocpCore* core);
	bool BindWindowsFunction(SOCKET socket, GUID guid, LPVOID* fn);

	void PostAccept(IocpEvent* pAcceptEvent);

private: 
	SOCKET m_listenSocket;
	IocpCore* m_core;
	vector<IocpEvent*> m_vlistenerEvent;
};

