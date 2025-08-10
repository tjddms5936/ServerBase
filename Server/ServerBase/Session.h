#pragma once
#include "IocpCore.h"
#include "RecvBuffer.h"
#include "SendBuffer.h"

enum class SessionType
{
	Server,
	Client
};

class Session : public IocpObject
{
public:
	Session(SOCKET socket, SessionType eSessionType);
	virtual ~Session();
	Session(const Session&) = delete;
	Session& operator=(const Session&) = delete;

	virtual HANDLE GetHandle() override { return reinterpret_cast<HANDLE>(m_socket); }
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) override;

public:
	// �������̽�
	// void Init(SOCKET socket);
	void Start(); // �ʱ� PostRecv
	void PostRecv(); // �񵿱� ���� ��û
	void OnRecv(DWORD numOfByptes); // ���� �Ϸ� �� ó��
	void PostSend(const char* data, int32 len);
	void OnSend(DWORD numOfBytes);
	void OnSend2(DWORD numOfBytes);

	SOCKET GetSocket() const { return m_socket; }

	char* GetAcceptBuffer() { return m_acceptBuffer.get(); }

	// �۽� ť & ���ö���Ʈ 1�� ��å �������� ����
	void SendPacket(const char* payload, int len);

private:
	void enqueueSend(stSendItem&& item);
	void postNextSend();

private:
	SOCKET m_socket;
	// char m_recvBuffer[MAX_RECIEVE_BUFFER_SIZE]; // �ܼ� �׽�Ʈ��
	RingRecvBuffer m_recvRingBuffer;
	unique_ptr<char[]> m_acceptBuffer; // AcceptEx ���� ����.

	SessionType m_SessionType;

	SendBufferPool m_SendPool{ 64 * 1024 };
	deque<stSendItem> m_SendQueue;
	atomic<bool> m_bSendInFlight{ false };
	atomic<ullong> m_ullPendingBytes{ 0 };
};

