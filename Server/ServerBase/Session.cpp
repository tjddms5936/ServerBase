#include "pch.h"
#include "Session.h"

Session::Session(SOCKET socket, SessionType eSessionType) :
	m_socket(socket), m_SessionType(eSessionType)
{
	// std::fill_n(m_recvBuffer, MAX_RECIEVE_BUFFER_SIZE, 0);

	// AcceptEx에 넘겨줄 버퍼 생성
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	const int bufferLen = addrLen * 2; // 로컬 + 리모트 주소
	m_acceptBuffer = make_unique<char[]>(bufferLen);
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
	case IocpEvent::Type::Recv: 
		OnRecv(numOfBytes); 
		delete pIocpEvent;  // Recv는 항상 완료
		break;
	case IocpEvent::Type::Send: 
		OnSend2(numOfBytes, pIocpEvent);  // pEvent 전달 (부분 완료 처리용)
		// Send는 OnSend2 내부에서 완료 여부에 따라 삭제 결정
		break;
	default:
		delete pIocpEvent;
		break;
	}
}

//void Session::Init(SOCKET socket)
//{
//	m_socket = socket;
//}

void Session::Start()
{
	std::cout << "[Session] Start() called\n";
	PostRecv(); // 최초 수신 요청 시작
}

void Session::PostRecv()
{
	std::cout << "[Session] PostRecv() submitted\n";

	DWORD flags = 0;
	WSABUF wsaBuf[2];
	m_recvRingBuffer.GetRecvWsaBuf(wsaBuf);

	// free space가 없는 경우 수신 불가
	if (wsaBuf[0].len == 0 && wsaBuf[1].len == 0)
	{
		std::cerr << "[PostRecv] No free space in RecvBuffer\n";
		m_bBufferFull.store(true);
	}
	else 
		m_bBufferFull.store(false);

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

	//// 커널 모드에 있는 TCP 수신 버퍼로부터 데이터를 꺼내오는 비동기 요청을 걸어두는 함수
	//// WSARecv()는 "누가 데이터를 보내면 나한테 알려줘!" 하고 요청만 해두는 함수
	//// 커널은 수신 버퍼에 도착한 데이터를 → 유저 모드로 알림 (IOCP 이벤트 발생)
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
		int64 FirstRetryTimeStampSec = m_i64RetryRecvTimestampSec.load();
		bool bBufferFull = m_bBufferFull.load();

		if (bBufferFull == true )
		{
			// 버퍼 가득 참. 재시도 필요
			std::cout << "[OnRecv] Recv buffer Full" << endl;

			if (FirstRetryTimeStampSec == 0)
			{
				m_i64RetryRecvTimestampSec.store(g_TimeMaker.GetTimeStamp_Sec());
				FirstRetryTimeStampSec = m_i64RetryRecvTimestampSec.load();
			}

			// 1분 이상동안 재시도 하면 그냥 연결 끊자
			if(g_TimeMaker.GetTimeStamp_Sec() - FirstRetryTimeStampSec >= 60)
			{
				// 연결 종료
				std::cout << "[OnRecv] Client disconnected\n";
				closesocket(m_socket);
			}
			else
				PostRecv();
		}
		else
		{
			// 연결 종료
			std::cout << "[OnRecv] Client disconnected\n";
			closesocket(m_socket);
		}

		return;
	}

	m_bBufferFull.store(false);
	m_i64RetryRecvTimestampSec.store(0);


	// 1) RingBuffer에 수신 완료된 만큼 Commit
	m_recvRingBuffer.CommitWrite(numOfByptes);

	// 디버깅 찍어보기
	m_recvRingBuffer.DebugPrint();

	// 2) 패킷 단위 처리 (예시: [2바이트 길이][Payload])
	while (true)
	{
		if (m_recvRingBuffer.GetUseSize() < sizeof(uint16))
			break;

		uint16 packetSize;
		if (!m_recvRingBuffer.PeekCopy((char*)&packetSize, sizeof(uint16)))
			break;

		// Windows에서 Little-Endian → 그대로 사용 가능
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
			// PostSend(packet.data(), packetSize); // 에코 기본 방법

			// 정책 통일. 헤더는 SendPacket이 생성
			const char* payload = packet.data() + sizeof(uint16);
			int payloadLen = packetSize - sizeof(uint16);
			SendPacket(payload, payloadLen);
		}
		else
		{
			std::cout << "[Client] Received packet size: " << packetSize
				<< ", payload: "
				<< std::string(packet.begin() + 2, packet.end()) << "\n";
		}
	}

	// 3) 다음 Recv 요청
	PostRecv();


	//if (numOfByptes == 0)
	//{
	//	cout << "[OnRecv] Client disconnected" << endl;
	//	closesocket(m_socket);
	//	return;
	//}

	//std::string msg(m_recvBuffer, numOfByptes);

	//// 서버 모드 세션이라면 에코처리
	//if (m_SessionType == SessionType::Server)
	//{
	//	std::cout << "[Server] Received: " << msg << "\n";
	//	PostSend(m_recvBuffer, numOfByptes);
	//}
	//else
	//{
	//	// 클라이언트 모드 → 받은 메시지 단순 출력
	//	std::cout << "[Client] Received: " << msg << "\n";
	//}

	//// 다음 수신 요청
	//PostRecv();
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

void Session::OnSend2(DWORD numOfBytes, IocpEvent* pEvent)
{
	std::cout << "[OnSend2] Sent " << numOfBytes << " bytes to client." << std::endl;
	m_ullPendingBytes.fetch_sub(numOfBytes); // 누적 전송량 차감

	// 부분 완료 추적
	pEvent->m_stSendItem.i32SentLen += numOfBytes;
	
	if (pEvent->m_stSendItem.i32SentLen < pEvent->m_stSendItem.i32TotalLen)
	{
		// 부분 완료: 나머지 데이터 재전송
		PartialSend(pEvent);
		// pEvent는 PartialSend에서 재사용되므로 삭제하지 않음
	}
	else
	{
		// 전체 완료: 이벤트 삭제 후 다음 전송
		delete pEvent;
		postNextSend();
	}
}

void Session::SendPacket(const char* payload, int len)
{
	// 1) 헤더 버퍼 (2바이트)
	shared_ptr<SendBuffer> hdr = m_SendPool.alloc(sizeof(uint16));
	uint16 ui16PacketSize = static_cast<uint16>(len + sizeof(uint16));
	memcpy(hdr->writable(), &ui16PacketSize, sizeof(uint16));
	hdr->commit(sizeof(uint16));

	// 2) payload 보관 (이미 풀 버퍼라면 그 shared_ptr을 그대로 keep2에 넣자)
	shared_ptr<SendBuffer> body = m_SendPool.alloc(len);
	memcpy(body->writable(), payload, len);
	body->commit(len);

	stSendItem stItem{};
	stItem.bufs[0].buf = hdr->data();
	stItem.bufs[0].len = static_cast<ULONG>(hdr->size());
	stItem.bufs[1].buf = body->data();
	stItem.bufs[1].len = static_cast<ULONG>(body->size());
	stItem.bufCount = 2;

	// 수명 보장 : keeper에 보관
	stItem.keep1 = hdr;
	stItem.keep2 = body;

	enqueueSend(move(stItem));
}

bool Session::TryBindWorker(int32 _i32WorkerID)
{
	int32 expected = -1;

	return m_i32BoundWorkerID.compare_exchange_strong(expected, _i32WorkerID);
}

void Session::enqueueSend(stSendItem&& item)
{
	// 역압(예: 1MB 초과시 드랍/대기) – 선택
	m_ullPendingBytes += (item.bufs[0].len + item.bufs[1].len);
	m_SendQueue.push_back(move(item));
	if (!m_bSendInFlight)
		postNextSend();
}

void Session::postNextSend()
{
	if (m_SendQueue.empty())
	{
		m_bSendInFlight.store(false);
		return;
	}

	m_bSendInFlight.store(true);

	IocpEvent* pEvent = new IocpEvent(IocpEvent::Type::Send, shared_from_this(), nullptr);
	pEvent->m_stSendItem = move(m_SendQueue.front());
	m_SendQueue.pop_front();

	// 부분 완료 추적을 위한 초기화
	pEvent->m_stSendItem.i32TotalLen = 0;
	for (DWORD i = 0; i < pEvent->m_stSendItem.bufCount; ++i)
	{
		pEvent->m_stSendItem.i32TotalLen += pEvent->m_stSendItem.bufs[i].len;
	}
	pEvent->m_stSendItem.i32SentLen = 0;

	DWORD sent = 0;
	int result = WSASend(
		m_socket,
		pEvent->m_stSendItem.bufs,
		pEvent->m_stSendItem.bufCount,
		&sent,
		0,
		pEvent,
		nullptr
	);

	if (result == SOCKET_ERROR)
	{
		int err = WSAGetLastError();
		if (err != WSA_IO_PENDING)
		{
			cerr << "[PostSend] WSASend Error: " << WSAGetLastError() << endl;
			delete pEvent;
			m_bSendInFlight.store(false);
		}
	}
}

void Session::PartialSend(IocpEvent* pEvent)
{
	// 부분 완료 처리: 전송되지 않은 나머지 데이터로 재전송
	
	int32 remaining = pEvent->m_stSendItem.i32TotalLen - pEvent->m_stSendItem.i32SentLen;
	if (remaining <= 0)
	{
		// 이미 전체 전송 완료 (이론적으로 도달하지 않아야 함)
		delete pEvent;
		postNextSend();
		return;
	}

	// 전송된 바이트 수를 기준으로 WSABUF 배열 재구성
	WSABUF remainingBufs[2];
	DWORD remainingBufCount = 0;
	int32 offset = pEvent->m_stSendItem.i32SentLen;  // 전송된 바이트 수

	// 첫 번째 버퍼에서 남은 부분 계산
	if (offset < static_cast<int32>(pEvent->m_stSendItem.bufs[0].len))
	{
		// 첫 번째 버퍼의 일부만 전송됨
		remainingBufs[0].buf = pEvent->m_stSendItem.bufs[0].buf + offset;
		remainingBufs[0].len = pEvent->m_stSendItem.bufs[0].len - offset;
		remainingBufCount = 1;
		
		// 두 번째 버퍼도 포함해야 하는지 확인
		if (remaining > static_cast<int32>(remainingBufs[0].len) && pEvent->m_stSendItem.bufCount > 1)
		{
			remainingBufs[1] = pEvent->m_stSendItem.bufs[1];
			remainingBufCount = 2;
		}
	}
	else
	{
		// 첫 번째 버퍼는 완전히 전송됨, 두 번째 버퍼의 일부만 전송됨
		offset -= pEvent->m_stSendItem.bufs[0].len;
		remainingBufs[0].buf = pEvent->m_stSendItem.bufs[1].buf + offset;
		remainingBufs[0].len = pEvent->m_stSendItem.bufs[1].len - offset;
		remainingBufCount = 1;
	}

	// 나머지 데이터로 재전송
	DWORD sent = 0;
	int result = WSASend(
		m_socket,
		remainingBufs,
		remainingBufCount,
		&sent,
		0,
		pEvent,  // 동일한 IocpEvent 재사용
		nullptr
	);

	if (result == SOCKET_ERROR)
	{
		int err = WSAGetLastError();
		if (err != WSA_IO_PENDING)
		{
			std::cerr << "[PartialSend] WSASend Error: " << err << std::endl;
			delete pEvent;
			m_bSendInFlight.store(false);
			postNextSend();
		}
		// WSA_IO_PENDING이면 정상: 비동기 대기 중
	}
}
