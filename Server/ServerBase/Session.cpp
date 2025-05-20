#include "pch.h"
#include "Session.h"
#include "IocpCore.h"

Session::Session(SOCKET socket) :
	m_socket(socket)
{
	std::fill_n(m_recvBuffer, MAX_RECIEVE_BUFFER_SIZE, 0);
}

Session::~Session()
{
	if(m_socket != INVALID_SOCKET)
		closesocket(m_socket);
}

void Session::Start()
{
	PostRecv(); // 최초 수신 요청 시작
}

void Session::PostRecv()
{
	DWORD flags = 0;
	DWORD bytesReceived = 0;

	WSABUF wsaBuf;
	wsaBuf.buf = m_recvBuffer;
	wsaBuf.len = MAX_RECIEVE_BUFFER_SIZE;

	IocpEvent* event = new IocpEvent(IocpEvent::Type::Recv, this);

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
		if (WSAGetLastError() != WSA_IO_PENDING)
		{
			cerr << "[PostRecv] WSARecv Error: " << WSAGetLastError() << endl;
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

	// 에코처리
	PostSend(m_recvBuffer, numOfByptes);

	// 다음 수신 요청
	PostRecv();
}

void Session::PostSend(const char* data, int32 len)
{

	WSABUF wsabuf;
	wsabuf.buf = const_cast<char*>(data); // 주의 : 임시 버퍼라면 안전한 복사 필요
	wsabuf.len = len;

	DWORD bytesSent = 0;
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, this);

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
