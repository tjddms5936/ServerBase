﻿#include "pch.h"
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
	case IocpEvent::Type::Recv: OnRecv(numOfBytes); break;
	// case IocpEvent::Type::Send: OnSend(numOfBytes); break;
	case IocpEvent::Type::Send: OnSend2(numOfBytes); break; // 큐 기반 송신 방법
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
		std::cout << "[OnRecv] Client disconnected\n";
		closesocket(m_socket);
		return;
	}

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

void Session::OnSend2(DWORD numOfBytes)
{
	/*
		IOCP에서 WSASend는 일반적으로 요청한 전체 WSABUF 합계를 한 번에 완료하지만, 부분 완료가 올 수도 있어.
		지금은 numBytes만큼 pendingBytes를 깎고 다음 큐를 진행하므로 대부분의 케이스에서 OK.
		부분 완료까지 엄격히 관리하려면 IocpEvent에 “이번 요청 총 길이”를 저장하고, numBytes < 요청길이면 나머지 구간으로 재전송하는 보강이 필요. (필요 시 나중에 확장해도 됨)
	*/
	std::cout << "[OnSend] Sent " << numOfBytes << " bytes to client." << std::endl;
	m_ullPendingBytes.fetch_sub(numOfBytes); // 누적 전송량 차감

	// 이벤트 삭제는 Dispatch 쪽에서 하면 동일 정책 유지
	postNextSend();
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

	if (result == SOCKET_ERROR && WSAGetLastError() != WSA_IO_PENDING)
	{
		cerr << "[PostSend] WSASend Error: " << WSAGetLastError() << endl;
		delete pEvent;
		m_bSendInFlight.store(false);
	}
}
