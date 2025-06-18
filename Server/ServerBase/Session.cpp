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
		std::cout << "[Session] 소멸자 호출됨 - 소켓 닫힘\n";
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
	PostRecv(); // 최초 수신 요청 시작
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

	// 커널 모드에 있는 TCP 수신 버퍼로부터 데이터를 꺼내오는 비동기 요청을 걸어두는 함수
	// WSARecv()는 "누가 데이터를 보내면 나한테 알려줘!" 하고 요청만 해두는 함수
	// 커널은 수신 버퍼에 도착한 데이터를 → 유저 모드로 알림 (IOCP 이벤트 발생)
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

	// 로그로 찍기만 해보자.
	cout << "[OnRecv] Received: " << string(m_recvBuffer, numOfByptes) << endl;

	// 에코처리
	PostSend(m_recvBuffer, numOfByptes);

	// 다음 수신 요청
	PostRecv();
}

void Session::PostSend(const char* data, int32 len)
{
	std::cout << "[Session] PostSend() submitted\n";

	WSABUF wsabuf;
	wsabuf.buf = const_cast<char*>(data); // 주의 : 임시 버퍼라면 안전한 복사 필요
	wsabuf.len = len;

	DWORD bytesSent = 0;
	// IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, this);
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, shared_from_this(), nullptr);

	// 커널에 “이 데이터를 클라이언트에게 보내줘” 라고 비동기로 요청
	// 커널이 실제로 데이터를 소켓에 밀어넣는 시점은 비동기이며 나중에 IOCP로 완료 통보해줌 (OnSend() 호출 트리거됨)
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
	// 전송 완료 후 따로 처리할 일은 아직 없음
}
