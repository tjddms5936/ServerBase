#pragma once

class Session
{
public:
	Session(SOCKET socket);
	virtual ~Session();
	Session(const Session&) = delete;
	Session& operator=(const Session&) = delete;

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

