#include "pch.h"
#include "Listener.h"

LPFN_ACCEPTEX		Listener::AcceptEx = nullptr;

Listener::Listener() :
	m_listenSocket(INVALID_SOCKET), m_core(nullptr)
{
	m_vThreadPools.clear();
}

Listener::~Listener()
{
	Close();
}

void Listener::Init(IocpCore* core, int32 ioThreadCount)
{
	m_core = core;
	m_listenSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);
	m_i32IoThreadCnt = ioThreadCount;

	// m_listenerEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);
	// m_vlistenerEvent.reserve(110); // 100 + a 만큼 여유분 reserve

	// IO 스레드별 이벤트 풀 생성
	m_vThreadPools.resize(m_i32IoThreadCnt);
	constexpr int32 EVENTS_PER_THREAD = 20;// IO 스레드당 이벤트 수 (수정 가능)
	/*for (int32 i = 0; i < 100; ++i)
	{
		IocpEvent* pAcceptEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);
		m_vlistenerEvent.push_back(pAcceptEvent);
	}*/
	for (int32 threadID = 0; threadID < ioThreadCount; ++threadID)
	{
		auto pool = make_unique<stThreadEventPool>();

		// 각 스레드별로 이벤트 생성
		for (int32 i = 0; i < EVENTS_PER_THREAD; ++i)
		{
			IocpEvent* pAcceptEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);
			pool->m_freeEvents.push_back(pAcceptEvent);
			pool->m_allEvents.push_back(pAcceptEvent); // 정리용
		}

		m_vThreadPools[threadID] = move(pool);
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

bool Listener::StartAccept(uint16 port, IocpCore* core, int32 ioTHreadCount)
{
	Init(core, ioTHreadCount);

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

	// 초기 AcceptEx 요청 : 각 스레드 풀에서 일부 이벤트로 시작
	constexpr int32 INITIAL_ACCEPTS_PER_THREAD = 5; // 스레드당 초기 요청 수
	/*for (int32 i = 0; i < 100; ++i)
	{
		if(m_vlistenerEvent[i] != nullptr)
			PostAccept(m_vlistenerEvent[i]);
	}*/
	for (int32 threadID = 0; threadID < ioTHreadCount; ++threadID)
	{
		for (int32 i = 0; i < INITIAL_ACCEPTS_PER_THREAD; ++i)
		{
			IocpEvent* pEvent = AcquireAcceptEvent(threadID);
			if (pEvent)
			{
				PostAccept(pEvent, threadID);
			}
			else
				cerr << "[StartAccept] Failed to acquire event for thread " << threadID << endl;
		}
	}

	std::cout << "[Listener] Initial AcceptEx requests posted :" << endl;
	return true;
}

void Listener::PostAccept(IocpEvent* pAcceptEvent, int32 ioThreadID)
{
	// 비동기 AcceptEX 사용

	// 1. 클라이언트 소켓 생성 (OVERLAPPED 지원)
	SOCKET clientSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);
	if (clientSocket == INVALID_SOCKET)
	{
		int err = WSAGetLastError();
		std::cerr << "WSASocket failed: " << WSAGetLastError() << std::endl;

		// 이벤트 반환 후 재시도
		ReleaseAcceptEvent(ioThreadID, pAcceptEvent);
		return;
	}

	// 2. AcceptEx에 넘겨줄 버퍼 생성
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	//const int bufferLen = addrLen * 2; // 로컬 + 리모트 주소
	//char* acceptBuffer = new char[bufferLen];

	// 음.. 세션을 미리 만들고 IOCP에 등록만 Dispatch에서 해주면 어떨까?
	// 리스너 전용 세션을 재활용하면..?
	shared_ptr<Session> session = make_shared<Session>(clientSocket, SessionType::Server);

	// 3. Overlapped 이벤트 생성
	// IocpEvent* pEvent = new IocpEvent(IocpEvent::Type::Accept, session);
	pAcceptEvent->SetPartsSession(session);
	pAcceptEvent->m_stIoData.SetIoThreadID(ioThreadID);

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
		// PostAccept(pAcceptEvent);

		// 이벤트 반환 후 재시도
		ReleaseAcceptEvent(ioThreadID, pAcceptEvent);
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
	if (!pSession)
	{
		cerr << "[OnAccept] Session is null" << endl;
		return;
	}

	// 스레드 ID 가져오기
	int32 ioThreadID = pIocpEvent->m_stIoData.GetIoThreadID();
	if (ioThreadID < 0)
	{
		// IO 스레드 ID가 설정되지 않음 
		cerr << "[OnAccept] IO Thread ID not set, using 0" << endl;
		ioThreadID = 0;
	}

	if (!m_core->Register(reinterpret_cast<HANDLE>(pSession->GetSocket()), 0))
	{
		cout << "register() failed" << std::endl;
		closesocket(m_listenSocket);
		// PostAccept(pIocpEvent);
		ReleaseAcceptEvent(ioThreadID, pIocpEvent); // 이벤트 반환
		return;
	}

	pSession->Start();

	PostAccept(pIocpEvent, ioThreadID);
}

IocpEvent* Listener::AcquireAcceptEvent(int32 ioThreadID)
{
	// 범위 체크
	if (ioThreadID < 0 || ioThreadID >= m_i32IoThreadCnt)
	{
		std::cerr << "[AcquireAcceptEvent] Invalid thread ID: " << ioThreadID << endl;
		return nullptr;
	}

	stThreadEventPool& pool = *m_vThreadPools[ioThreadID];
	{
		lock_guard<mutex> lock(pool.m_mutex);

		if (pool.m_freeEvents.empty())
		{
			// 이벤트 부족
			std::cerr << "[AcquireAcceptEvent] No free events for thread ID: " << ioThreadID << endl;
			return nullptr;
		}

		IocpEvent* pEvent = pool.m_freeEvents.front();
		pool.m_freeEvents.pop_front();
		return pEvent;
	}

	std::cerr << "[AcquireAcceptEvent] Something Wrong ... thread ID:  " << ioThreadID << endl;
	return nullptr;
}

void Listener::ReleaseAcceptEvent(int32 ioThreadID, IocpEvent* pEvent)
{
	if (ioThreadID < 0 || ioThreadID >= m_i32IoThreadCnt || pEvent == nullptr)
	{
		std::cerr << "[ReleaseAcceptEvent] Invalid param... thrad ID:  " << ioThreadID << endl;
		return;
	}

	stThreadEventPool& pool = *m_vThreadPools[ioThreadID];
	{
		lock_guard<mutex> lock(pool.m_mutex);

		// 이벤트 재사용을 위해 초기화
		pEvent->SetPartsSession(nullptr);
		pEvent->m_stIoData.SetIoThreadID(ioThreadID); // IO 스레드 ID 유지

		// 이벤트를 초기화 한 후 해당 IO 스레드의 풀에 반환
		pool.m_freeEvents.push_back(pEvent); 
	}
}
