#include "pch.h"
#include "Listener.h"

LPFN_ACCEPTEX		Listener::AcceptEx = nullptr;

Listener::Listener() :
	m_listenSocket(INVALID_SOCKET), m_core(nullptr)
{
	m_vThreadPools.clear();
	m_AcceptScheduler.SetOwner(this);
}

Listener::~Listener()
{
	m_AcceptScheduler.EndRetry();
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

	m_AcceptScheduler.StartRetry();
	return true;
}

void Listener::PostAccept(IocpEvent* pAcceptEvent, int32 ioThreadID, int32 retryCount = 0)
{
	// 비동기 AcceptEX 사용

	// 1. 클라이언트 소켓 생성 (OVERLAPPED 지원)
	SOCKET clientSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);
	if (clientSocket == INVALID_SOCKET)
	{
		int err = WSAGetLastError();
		std::cerr << "WSASocket failed: " << WSAGetLastError() << std::endl;

		//// 이벤트 반환 후 재시도
		//ReleaseAcceptEvent(ioThreadID, pAcceptEvent);

		// 재시도 스케쥴링 (비동기)
		m_AcceptScheduler.ScheduleRetry(ioThreadID, pAcceptEvent, err);
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

		// // 이벤트 반환 후 재시도
		// ReleaseAcceptEvent(ioThreadID, pAcceptEvent);

		// 재시도 스케쥴링 (비동기)
		m_AcceptScheduler.ScheduleRetry(ioThreadID, pAcceptEvent, WSAGetLastError(), retryCount);
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

void AcceptRetryScheduler::RetryThreadLoop()
{
	while (m_bRetryRunning)
	{
		unique_lock<mutex> lock(m_retryMutex);

		if (m_retryQeuue.empty())
		{
			// 큐가 비어있으면 대기
			m_cvRetry.wait(lock);
			continue;
		}

		// 다음 재시도 시간까지 대기
		auto now = chrono::steady_clock::now();
		auto nextRetry = m_retryQeuue.front().nextRetryTime;

		if (now < nextRetry)
		{
			// 아직 시간 안됨. 대기
			m_cvRetry.wait_until(lock, nextRetry);
			continue;
		}

		// 자시도 시간 도달
		stRetryItem item = m_retryQeuue.front();
		m_retryQeuue.pop_front();
		lock.unlock(); // 락 해제 (PostAccept 호출 전)

		// 최대 재시도 횟수 체크
		if (item.retryCount >= MAX_RETRY)
		{
			cerr << "[RetryThreadLoop] Max retry count exceeded for thread " << item.IoThreadID << endl;
			if (pOwner)
				pOwner->ReleaseAcceptEvent(item.IoThreadID, item.pEvent);
			continue;
		}

		// 재시도
		cout << "[RetryThread] Retrying (count : " << item.retryCount << ") for thread " << item.IoThreadID << endl;

		// PostAccept 재호출 (재시도 횟수 전달 필요)
		pOwner->PostAccept(item.pEvent, item.IoThreadID, item.retryCount + 1);
	}
}

void AcceptRetryScheduler::ScheduleRetry(int32 ioThreadID, IocpEvent* pEvent, int32 errCode, int32 retryCount)
{
	// 영구적 에러 체크
	if (errCode == WSAEINVAL || errCode == WSAEAFNOSUPPORT || errCode == WSAENOTSOCK)
	{
		cerr << "[ScheduleRetry] Permanet error : " << errCode << ", releasing event" << endl;
		
		if (pOwner)
			pOwner->ReleaseAcceptEvent(ioThreadID, pEvent);
		return;
	}

	lock_guard<mutex> lock(m_retryMutex);
	{
		// 이벤트에서 재시도 횟수 읽기 (또는 새로 생성)
		stRetryItem item;
		item.pEvent = pEvent;
		item.IoThreadID = ioThreadID;
		item.errCode = errCode;
		item.retryCount = retryCount;
		
		// 지수 백오프 계산. 상한선 설정
		int32 delayMs = min(BASE_DELAY_MS * (1 << item.retryCount), MAX_DELAY_MS);
		item.nextRetryTime = chrono::steady_clock::now() + chrono::milliseconds(delayMs);

		m_retryQeuue.push_back(item);
		m_cvRetry.notify_one(); // 재시도 스레드 깨우기
	}

}

void AcceptRetryScheduler::StartRetry()
{
	// 재시도 스레드 시작
	m_bRetryRunning.store(true);
	m_retryThread = thread([this()]{ this->RetryThreadLoop(); });
}

void AcceptRetryScheduler::EndRetry()
{
	// 재시도 스레드 종료
	m_bRetryRunning.store(false);
	{
		lock_guard<mutex> lock(m_retryMutex);
		m_cvRetry.notify_all();
	}

	if (m_retryThread.joinable())
		m_retryThread.join();

	// 남은 재시도 항목 정리
	for (auto& item : m_retryQeuue)
	{
		if (pOwner)
			pOwner->ReleaseAcceptEvent(item.IoThreadID, item.pEvent);
	}
}
