#include "pch.h"
#include "Session.h"

Session::Session(SOCKET socket, SessionType eSessionType) :
	m_socket(socket), m_SessionType(eSessionType)
{
	// std::fill_n(m_recvBuffer, MAX_RECIEVE_BUFFER_SIZE, 0);

	// AcceptEx�� �Ѱ��� ���� ����
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	const int bufferLen = addrLen * 2; // ���� + ����Ʈ �ּ�
	m_acceptBuffer = make_unique<char[]>(bufferLen);
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

//void Session::Init(SOCKET socket)
//{
//	m_socket = socket;
//}

void Session::Start()
{
	std::cout << "[Session] Start() called\n";
	PostRecv(); // ���� ���� ��û ����
}

void Session::PostRecv()
{
	std::cout << "[Session] PostRecv() submitted\n";

	DWORD flags = 0;
	WSABUF wsaBuf[2];
	m_recvRingBuffer.GetRecvWsaBuf(wsaBuf);

	// free space�� ���� ��� ���� �Ұ�
	if (wsaBuf[0].len == 0 && wsaBuf[1].len == 0)
	{
		std::cerr << "[PostRecv] No free space in RecvBuffer\n";
		return;
	}

	int bufCount = (wsaBuf[1].len > 0) ? 2 : 1;

	DWORD bytesReceived = 0;
	DWORD recvBytes = 0;
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, shared_from_this(), nullptr);

	int result = WSARecv(
		m_socket,
		wsaBuf,
		bufCount,
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
			return;
		}
	}

	//DWORD flags = 0;
	//DWORD bytesReceived = 0;

	//WSABUF wsaBuf;
	//wsaBuf.buf = m_recvBuffer;
	//wsaBuf.len = MAX_RECIEVE_BUFFER_SIZE;

	//// IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, this);
	//IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, shared_from_this(), nullptr);

	//// Ŀ�� ��忡 �ִ� TCP ���� ���۷κ��� �����͸� �������� �񵿱� ��û�� �ɾ�δ� �Լ�
	//// WSARecv()�� "���� �����͸� ������ ������ �˷���!" �ϰ� ��û�� �صδ� �Լ�
	//// Ŀ���� ���� ���ۿ� ������ �����͸� �� ���� ���� �˸� (IOCP �̺�Ʈ �߻�)
	//int result = WSARecv(
	//	m_socket,
	//	&wsaBuf,
	//	1,
	//	&bytesReceived,
	//	&flags,
	//	event,
	//	nullptr
	//);

	//if (result == SOCKET_ERROR)
	//{
	//	int err = WSAGetLastError();
	//	if (err != WSA_IO_PENDING)
	//	{
	//		std::cerr << "[PostRecv] WSARecv Error: " << err << std::endl;
	//		delete event;
	//	}
	//}
}

void Session::OnRecv(DWORD numOfByptes)
{
	if (numOfByptes == 0)
	{
		std::cout << "[OnRecv] Client disconnected\n";
		closesocket(m_socket);
		return;
	}

	// 1) RingBuffer�� ���� �Ϸ�� ��ŭ Commit
	m_recvRingBuffer.CommitWrite(numOfByptes);

	// ����� ����
	m_recvRingBuffer.DebugPrint();

	// 2) ��Ŷ ���� ó�� (����: [2����Ʈ ����][Payload])
	while (true)
	{
		if (m_recvRingBuffer.GetUseSize() < sizeof(uint16))
			break;

		uint16 packetSize;
		if (!m_recvRingBuffer.PeekCopy((char*)&packetSize, sizeof(uint16)))
			break;

		// Windows���� Little-Endian �� �״�� ��� ����
		if (packetSize < sizeof(uint16))
		{
			std::cerr << "[Error] Invalid packet size: " << packetSize << "\n";
			break;
		}

		if (m_recvRingBuffer.GetUseSize() < packetSize)
			break;

		std::vector<char> packet(packetSize);
		m_recvRingBuffer.Read(packet.data(), packetSize);

		if (m_SessionType == SessionType::Server)
		{
			std::cout << "[Server] Received packet size: " << packetSize
				<< ", payload: "
				<< std::string(packet.begin() + 2, packet.end()) << "\n";
			PostSend(packet.data(), packetSize); // ����
		}
		else
		{
			std::cout << "[Client] Received packet size: " << packetSize
				<< ", payload: "
				<< std::string(packet.begin() + 2, packet.end()) << "\n";
		}
	}

	// 3) ���� Recv ��û
	PostRecv();


	//if (numOfByptes == 0)
	//{
	//	cout << "[OnRecv] Client disconnected" << endl;
	//	closesocket(m_socket);
	//	return;
	//}

	//std::string msg(m_recvBuffer, numOfByptes);

	//// ���� ��� �����̶�� ����ó��
	//if (m_SessionType == SessionType::Server)
	//{
	//	std::cout << "[Server] Received: " << msg << "\n";
	//	PostSend(m_recvBuffer, numOfByptes);
	//}
	//else
	//{
	//	// Ŭ���̾�Ʈ ��� �� ���� �޽��� �ܼ� ���
	//	std::cout << "[Client] Received: " << msg << "\n";
	//}

	//// ���� ���� ��û
	//PostRecv();
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