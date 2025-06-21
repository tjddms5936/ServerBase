#include "pch.h"
#include "Listener.h"

LPFN_ACCEPTEX		Listener::AcceptEx = nullptr;

Listener::Listener() :
	m_listenSocket(INVALID_SOCKET), m_core(nullptr)
{
	m_vlistenerEvent.clear();
}

Listener::~Listener()
{
	Close();
}

void Listener::Init(IocpCore* core)
{
	m_core = core;
	m_listenSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);

	// m_listenerEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);
	m_vlistenerEvent.reserve(110); // 100 + a 만큼 여유분 reserve
	for (int32 i = 0; i < 100; ++i)
	{
		IocpEvent* pAcceptEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);
		m_vlistenerEvent.push_back(pAcceptEvent);
	}

	if (!m_core->Register(GetHandle(), 0))
	{
		cout << "Listenr register() failed" << std::endl;
		return;
	}

	/* 런타임에 주소 얻어오는 API */
	ASSERT_CRASH(BindWindowsFunction(m_listenSocket, WSAID_ACCEPTEX, reinterpret_cast<LPVOID*>(&AcceptEx)));
}

bool Listener::BindWindowsFunction(SOCKET socket, GUID guid, LPVOID* fn)
{
	DWORD bytes = 0;
	return SOCKET_ERROR != ::WSAIoctl(m_listenSocket, SIO_GET_EXTENSION_FUNCTION_POINTER, &guid, sizeof(guid), fn, sizeof(*fn), &bytes, NULL, NULL);
}

void Listener::Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	OnAccept(pIocpEvent, numOfBytes);
}

bool Listener::StartAccept(uint16 port, IocpCore* core)
{
	Init(core);

	if (m_listenSocket == INVALID_SOCKET)
	{
		cout << "socket() failed" << endl;
		return false;
	}

	SOCKADDR_IN serverAddr = {};
	serverAddr.sin_family = AF_INET;
	serverAddr.sin_addr.S_un.S_addr = htonl(INADDR_ANY);
	serverAddr.sin_port = htons(port);

	if (bind(m_listenSocket, (SOCKADDR*)&serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
	{
		cout << "bind() failed" << endl;
		return false;
	}

	if (listen(m_listenSocket, SOMAXCONN) == SOCKET_ERROR)
	{
		cout << "listen() failed" << endl;
		return false;
	}

	cout << "Listening on port : " << port << "..." << endl;

	std::cout << "[Listener] Waiting for connection...\n";
	for (int32 i = 0; i < 100; ++i)
	{
		if(m_vlistenerEvent[i] != nullptr)
			PostAccept(m_vlistenerEvent[i]);
	}
	std::cout << "PostAccept Setting End ... size :" << m_vlistenerEvent.size() << "\n";
	return true;
}

void Listener::PostAccept(IocpEvent* pAcceptEvent)
{
	// 비동기 AcceptEX 사용

	// 1. 클라이언트 소켓 생성 (OVERLAPPED 지원)
	SOCKET clientSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);
	if (clientSocket == INVALID_SOCKET)
	{
		int err = WSAGetLastError();
		std::cerr << "WSASocket failed: " << WSAGetLastError() << std::endl;
	}

	// 2. AcceptEx에 넘겨줄 버퍼 생성
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	//const int bufferLen = addrLen * 2; // 로컬 + 리모트 주소
	//char* acceptBuffer = new char[bufferLen];

	// 음.. 세션을 미리 만들고 IOCP에 등록만 Dispatch에서 해주면 어떨까?
	// 리스너 전용 세션을 재활용하면..?
	shared_ptr<Session> session = make_shared<Session>(clientSocket);

	// 3. Overlapped 이벤트 생성
	// IocpEvent* pEvent = new IocpEvent(IocpEvent::Type::Accept, session);
	pAcceptEvent->SetPartsSession(session);

	DWORD bytesReceived = 0;
	BOOL result = AcceptEx(
		m_listenSocket,        // 서버 소켓
		clientSocket,          // 새로 연결될 클라이언트 소켓
		session->GetAcceptBuffer(),          // 주소 정보를 받을 버퍼
		0,                     // 수신할 초기 데이터 없음
		addrLen,               // 로컬 주소 크기
		addrLen,               // 리모트 주소 크기
		&bytesReceived,        // 완료 시 실제 받은 바이트 수
		pAcceptEvent        // OVERLAPPED 구조체
	);

	if (result == FALSE && WSAGetLastError() != WSA_IO_PENDING)
	{
		std::cerr << "AcceptEx failed: " << WSAGetLastError() << std::endl;
		// delete[] acceptBuffer;
		closesocket(clientSocket);

		// 일단 다시 Accept 걸어준다.
		PostAccept(pAcceptEvent);
	}

}

void Listener::Close()
{
	if(m_listenSocket != INVALID_SOCKET)
	{
		closesocket(m_listenSocket);
		m_listenSocket = INVALID_SOCKET;
	}
}

void Listener::OnAccept(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	shared_ptr<Session> pSession = pIocpEvent->GetPartsSession();
	if (!m_core->Register(reinterpret_cast<HANDLE>(pSession->GetSocket()), 0))
	{
		cout << "register() failed" << std::endl;
		PostAccept(pIocpEvent);
		return;
	}

	pSession->Start();

	PostAccept(pIocpEvent);
}
