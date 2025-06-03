#pragma once
#include "IocpCore.h"

class Session : public IocpObject
{
public:
	Session(SOCKET socket);
	virtual ~Session();
	Session(const Session&) = delete;
	Session& operator=(const Session&) = delete;

	virtual HANDLE GetHandle() override { return reinterpret_cast<HANDLE>(m_socket); }
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) override;

public:
	// 인터페이스
	void Start(); // 초기 PostRecv
	void PostRecv(); // 비동기 수신 요청
	void OnRecv(DWORD numOfByptes); // 수신 완료 후 처리
	void PostSend(const char* data, int32 len);
	void OnSend(DWORD numOfBytes);

	SOCKET GetSocket() const { return m_socket; }


private:
	SOCKET m_socket;
	char m_recvBuffer[MAX_RECIEVE_BUFFER_SIZE]; // 단순 테스트용
};

