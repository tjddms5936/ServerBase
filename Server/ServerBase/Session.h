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
	// �������̽�
	void Start(); // �ʱ� PostRecv
	void PostRecv(); // �񵿱� ���� ��û
	void OnRecv(DWORD numOfByptes); // ���� �Ϸ� �� ó��
	void PostSend(const char* data, int32 len);
	void OnSend(DWORD numOfBytes);

	SOCKET GetSocket() const { return m_socket; }


private:
	SOCKET m_socket;
	char m_recvBuffer[MAX_RECIEVE_BUFFER_SIZE]; // �ܼ� �׽�Ʈ��
};

