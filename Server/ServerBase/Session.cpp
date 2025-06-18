#include "pch.h"
#include "Session.h"

Session::Session(SOCKET socket) :
	m_socket(INVALID_SOCKET)
{
	std::fill_n(m_recvBuffer, MAX_RECIEVE_BUFFER_SIZE, 0);
}

Session::~Session()
{
	if (m_socket != INVALID_SOCKET)
	{
		closesocket(m_socket);
		m_socket = INVALID_SOCKET;
		std::cout << "[Session] �Ҹ��� ȣ��� - ���� ����\n";
	}
}

void Session::Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	IocpEvent::Type eType = pIocpEvent->GetType();
	switch (eType)
	{
	case IocpEvent::Type::Recv: OnRecv(numOfBytes); break;
	case IocpEvent::Type::Send: OnSend(numOfBytes); break;
	default:
		break;
	}

	delete pIocpEvent;
}

void Session::Init(SOCKET socket)
{
	m_socket = socket;
}

void Session::Start()
{
	std::cout << "[Session] Start() called\n";
	PostRecv(); // ���� ���� ��û ����
}

void Session::PostRecv()
{
	std::cout << "[Session] PostRecv() submitted\n";
	DWORD flags = 0;
	DWORD bytesReceived = 0;

	WSABUF wsaBuf;
	wsaBuf.buf = m_recvBuffer;
	wsaBuf.len = MAX_RECIEVE_BUFFER_SIZE;

	// IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, this);
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, shared_from_this(), nullptr);

	// Ŀ�� ��忡 �ִ� TCP ���� ���۷κ��� �����͸� �������� �񵿱� ��û�� �ɾ�δ� �Լ�
	// WSARecv()�� "���� �����͸� ������ ������ �˷���!" �ϰ� ��û�� �صδ� �Լ�
	// Ŀ���� ���� ���ۿ� ������ �����͸� �� ���� ���� �˸� (IOCP �̺�Ʈ �߻�)
	int result = WSARecv(
		m_socket,
		&wsaBuf,
		1,
		&bytesReceived,
		&flags,
		event,
		nullptr
	);

	if (result == SOCKET_ERROR)
	{
		int err = WSAGetLastError();
		if (err != WSA_IO_PENDING)
		{
			std::cerr << "[PostRecv] WSARecv Error: " << err << std::endl;
			delete event;
		}
	}
}

void Session::OnRecv(DWORD numOfByptes)
{
	if (numOfByptes == 0)
	{
		cout << "[OnRecv] Client disconnected" << endl;
		closesocket(m_socket);
		return;
	}

	// �α׷� ��⸸ �غ���.
	cout << "[OnRecv] Received: " << string(m_recvBuffer, numOfByptes) << endl;

	// ����ó��
	PostSend(m_recvBuffer, numOfByptes);

	// ���� ���� ��û
	PostRecv();
}

void Session::PostSend(const char* data, int32 len)
{
	std::cout << "[Session] PostSend() submitted\n";

	WSABUF wsabuf;
	wsabuf.buf = const_cast<char*>(data); // ���� : �ӽ� ���۶�� ������ ���� �ʿ�
	wsabuf.len = len;

	DWORD bytesSent = 0;
	// IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, this);
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, shared_from_this(), nullptr);

	// Ŀ�ο� ���� �����͸� Ŭ���̾�Ʈ���� �����ࡱ ��� �񵿱�� ��û
	// Ŀ���� ������ �����͸� ���Ͽ� �о�ִ� ������ �񵿱��̸� ���߿� IOCP�� �Ϸ� �뺸���� (OnSend() ȣ�� Ʈ���ŵ�)
	int result = WSASend(
		m_socket,
		&wsabuf,
		1,
		&bytesSent,
		0,
		event,
		nullptr
	);

	if (result == SOCKET_ERROR)
	{
		if (WSAGetLastError() != WSA_IO_PENDING)
		{
			cerr << "[PostSend] WSASend Error: " << WSAGetLastError() << endl;
			delete event;
		}
	}
}

void Session::OnSend(DWORD numOfBytes)
{
	std::cout << "[OnSend] Sent " << numOfBytes << " bytes to client." << std::endl;
	// ���� �Ϸ� �� ���� ó���� ���� ���� ����
}
