#include "pch.h"
#include "Session.h"
#include "MemoryStream.h"
#include "IOCPWorkerPool.h"

Session::Session(SOCKET socket, SessionType eSessionType) :
	m_socket(socket), m_SessionType(eSessionType)
{
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	const int bufferLen = addrLen * 2;
	m_acceptBuffer = make_unique<char[]>(bufferLen);
}

Session::~Session()
{
	CloseSocket();
}

void Session::CloseSocket()
{
	SOCKET socketToClose = INVALID_SOCKET;
	{
		lock_guard<mutex> lock(m_socketCloseLock);
		if (m_socket == INVALID_SOCKET)
			return;
		socketToClose = m_socket;
		m_socket = INVALID_SOCKET;
	}

	closesocket(socketToClose);
	std::cout << "[Session] Socket closed\n";
}

void Session::Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	IocpEvent::Type eType = pIocpEvent->GetType();
	const bool bCompletionSuccess = pIocpEvent->m_stIoData.bCompletionSuccess;
	const DWORD dwCompletionError = pIocpEvent->m_stIoData.dwCompletionError;

	switch (eType)
	{
	case IocpEvent::Type::Recv:
		if (!bCompletionSuccess)
		{
			std::cerr << "[OnRecv] IOCP completion failed: " << dwCompletionError << std::endl;
			CloseSocket();
			delete pIocpEvent;
			break;
		}

		OnRecv_v2(numOfBytes);
		delete pIocpEvent;
		break;
	case IocpEvent::Type::Send:
		if (!bCompletionSuccess)
		{
			std::cerr << "[OnSend2] IOCP completion failed: " << dwCompletionError << std::endl;
			m_bSendInFlight.store(false);
			delete pIocpEvent;
			CloseSocket();
			break;
		}

		OnSend2(numOfBytes, pIocpEvent);
		break;
	case IocpEvent::Type::DeferredSendPacket:
		if (pIocpEvent->m_pDeferredPacket)
			SendPacket(pIocpEvent->m_pDeferredPacket.get());
		delete pIocpEvent;
		break;
	default:
		delete pIocpEvent;
		break;
	}
}

void Session::Start()
{
	std::cout << "[Session] Start() called\n";
	PostRecv();
}

void Session::PostRecv()
{
	std::cout << "[Session] PostRecv() submitted\n";

	DWORD flags = 0;
	WSABUF wsaBuf[2];
	m_recvRingBuffer.GetRecvWsaBuf(wsaBuf);

	if (wsaBuf[0].len == 0 && wsaBuf[1].len == 0)
	{
		std::cerr << "[PostRecv] No free space in RecvBuffer\n";
		m_bBufferFull.store(true);
		return;
	}
	m_bBufferFull.store(false);

	int bufCount = (wsaBuf[1].len > 0) ? 2 : 1;

	DWORD bytesReceived = 0;
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
			CloseSocket();
			return;
		}
	}
}

void Session::OnRecv(DWORD numOfByptes)
{
	if (numOfByptes == 0)
	{
		std::cout << "[OnRecv] Client disconnected\n";
		CloseSocket();
		return;
	}

	m_bBufferFull.store(false);
	m_i64RetryRecvTimestampSec.store(0);

	m_recvRingBuffer.CommitWrite(numOfByptes);
	m_recvRingBuffer.DebugPrint();

	while (true)
	{
		if (m_recvRingBuffer.GetUseSize() < sizeof(uint16))
			break;

		uint16 packetSize;
		if (!m_recvRingBuffer.PeekCopy((char*)&packetSize, sizeof(uint16)))
			break;

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

	PostRecv();
}

void Session::OnRecv_v2(DWORD numOfByptes)
{
	if (numOfByptes == 0)
	{
		std::cout << "[OnRecv] Client disconnected\n";
		CloseSocket();
		return;
	}

	m_bBufferFull.store(false);
	m_i64RetryRecvTimestampSec.store(0);

	m_recvRingBuffer.CommitWrite(numOfByptes);
	m_recvRingBuffer.DebugPrint();

	ParsePackets();

	if (m_socket == INVALID_SOCKET)
		return;

	PostRecv();
}

void Session::PostSend(const char* data, int32 len)
{
	std::cout << "[Session] PostSend() submitted\n";

	WSABUF wsabuf;
	wsabuf.buf = const_cast<char*>(data);
	wsabuf.len = len;

	DWORD bytesSent = 0;
	IocpEvent* event = new IocpEvent(IocpEvent::Type::Send, shared_from_this(), nullptr);

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
		int err = WSAGetLastError();
		if (err != WSA_IO_PENDING)
		{
			cerr << "[PostSend] WSASend Error: " << err << endl;
			delete event;
			CloseSocket();
		}
	}
}

void Session::OnSend(DWORD numOfBytes)
{
	std::cout << "[OnSend] Sent " << numOfBytes << " bytes to client." << std::endl;
}

void Session::OnSend2(DWORD numOfBytes, IocpEvent* pEvent)
{
	std::cout << "[OnSend2] Sent " << numOfBytes << " bytes to client." << std::endl;
	m_ullPendingBytes.fetch_sub(numOfBytes);

	pEvent->m_stSendItem.i32SentLen += numOfBytes;
	
	if (pEvent->m_stSendItem.i32SentLen < pEvent->m_stSendItem.i32TotalLen)
	{
		PartialSend(pEvent);
	}
	else
	{
		delete pEvent;
		postNextSend();
	}
}

void Session::SendPacket(const char* payload, int len)
{
	const int32 currentWorkerID = IOCPWorkerPool::GetCurrentLogicWorkerID();
	const int32 boundWorkerID = GetBoundWorkerID();
	const bool bCanSendInline = (currentWorkerID != -1) && (boundWorkerID == -1 || currentWorkerID == boundWorkerID);
	if (!bCanSendInline)
	{
		std::cerr << "[SendPacket(raw)] must be called from the owner worker\n";
		return;
	}

	shared_ptr<SendBuffer> hdr = m_SendPool.alloc(sizeof(uint16));
	uint16 ui16PacketSize = static_cast<uint16>(len + sizeof(uint16));
	memcpy(hdr->writable(), &ui16PacketSize, sizeof(uint16));
	hdr->commit(sizeof(uint16));

	shared_ptr<SendBuffer> body = m_SendPool.alloc(len);
	memcpy(body->writable(), payload, len);
	body->commit(len);

	stSendItem stItem{};
	stItem.bufs[0].buf = hdr->data();
	stItem.bufs[0].len = static_cast<ULONG>(hdr->size());
	stItem.bufs[1].buf = body->data();
	stItem.bufs[1].len = static_cast<ULONG>(body->size());
	stItem.bufCount = 2;
	stItem.keep1 = hdr;
	stItem.keep2 = body;

	enqueueSend(move(stItem));
}

bool Session::QueuePacketToWorker(const IIocpPacket& packet)
{
	// 1. DummyClient SendPacket용. <-- Race Condition 방지. 
	// 2. 이 외 코드 작업에서 Session : WorkerThread가 1:1을 위반한 SendPacket이 들어간 경우. <-- 이 경우 별도로 서버를 다운 시키던지 해서 코드 수정 필요.
	if (m_socket == INVALID_SOCKET)
		return false;

	if (m_pCore == nullptr)
	{
		std::cerr << "[QueuePacketToWorker] IocpCore is not assigned\n";
		return false;
	}

	shared_ptr<IIocpPacket> clonedPacket = packet.Clone();
	if (!clonedPacket)
	{
		std::cerr << "[QueuePacketToWorker] Packet clone failed\n";
		return false;
	}

	IocpEvent* pEvent = new IocpEvent(IocpEvent::Type::DeferredSendPacket, shared_from_this(), nullptr);
	pEvent->m_pDeferredPacket = move(clonedPacket);

	if (!::PostQueuedCompletionStatus(m_pCore->GetHandle(), 0, 0, pEvent))
	{
		std::cerr << "[QueuePacketToWorker] PostQueuedCompletionStatus failed: " << GetLastError() << std::endl;
		delete pEvent;
		CloseSocket();
		return false;
	}

	return true;
}

void Session::SendPacket(IIocpPacket* packet)
{
	if (packet == nullptr)
		return;

	const int32 currentWorkerID = IOCPWorkerPool::GetCurrentLogicWorkerID();
	const int32 boundWorkerID = GetBoundWorkerID();
	const bool bCanSendInline = (currentWorkerID != -1) && (boundWorkerID == -1 || currentWorkerID == boundWorkerID);
	if (!bCanSendInline)
	{
		if (!QueuePacketToWorker(*packet))
			std::cerr << "[SendPacket] Failed to marshal packet send to owner worker\n";
		return;
	}

	OutputMemoryStream payloadStream;
	packet->SerializePayload(payloadStream);

	if (payloadStream.GetBuffer().size() >= (std::numeric_limits<int32>::max() - sizeof(PacketHeader)))
		throw std::length_error("payloadStream length over int32_Maximum");

	PacketHeader header;
	header.pkgID = packet->PkgHeader.pkgID;
	header.pkgSize = static_cast<int32>(sizeof(PacketHeader)) + static_cast<int32>(payloadStream.GetBuffer().size());
	
	OutputMemoryStream headerStream;
	headerStream.Serialize(header.pkgID, header.pkgSize);

	shared_ptr<SendBuffer> hdr = m_SendPool.alloc(headerStream.GetBuffer().size());
	memcpy(hdr->writable(), headerStream.GetBuffer().data(), headerStream.GetBuffer().size());
	hdr->commit(headerStream.GetBuffer().size());

	const vector<uint8>& payloadData = payloadStream.GetBuffer();
	shared_ptr<SendBuffer> body = m_SendPool.alloc(payloadData.size());
	memcpy(body->writable(), payloadData.data(), payloadData.size());
	body->commit(payloadData.size());

	stSendItem stItem{};
	stItem.bufs[0].buf = hdr->data();
	stItem.bufs[0].len = static_cast<ULONG>(hdr->size());
	stItem.bufs[1].buf = body->data();
	stItem.bufs[1].len = static_cast<ULONG>(body->size());
	stItem.bufCount = 2;
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
			cerr << "[PostSend] WSASend Error: " << err << endl;
			delete pEvent;
			m_bSendInFlight.store(false);
			CloseSocket();
			return;
		}
	}
}

void Session::PartialSend(IocpEvent* pEvent)
{
	int32 remaining = pEvent->m_stSendItem.i32TotalLen - pEvent->m_stSendItem.i32SentLen;
	if (remaining <= 0)
	{
		delete pEvent;
		postNextSend();
		return;
	}

	WSABUF remainingBufs[2];
	DWORD remainingBufCount = 0;
	int32 offset = pEvent->m_stSendItem.i32SentLen;

	if (offset < static_cast<int32>(pEvent->m_stSendItem.bufs[0].len))
	{
		remainingBufs[0].buf = pEvent->m_stSendItem.bufs[0].buf + offset;
		remainingBufs[0].len = pEvent->m_stSendItem.bufs[0].len - offset;
		remainingBufCount = 1;
		
		if (remaining > static_cast<int32>(remainingBufs[0].len) && pEvent->m_stSendItem.bufCount > 1)
		{
			remainingBufs[1] = pEvent->m_stSendItem.bufs[1];
			remainingBufCount = 2;
		}
	}
	else
	{
		offset -= pEvent->m_stSendItem.bufs[0].len;
		remainingBufs[0].buf = pEvent->m_stSendItem.bufs[1].buf + offset;
		remainingBufs[0].len = pEvent->m_stSendItem.bufs[1].len - offset;
		remainingBufCount = 1;
	}

	DWORD sent = 0;
	int result = WSASend(
		m_socket,
		remainingBufs,
		remainingBufCount,
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
			std::cerr << "[PartialSend] WSASend Error: " << err << std::endl;
			delete pEvent;
			m_bSendInFlight.store(false);
			CloseSocket();
			return;
		}
	}
}

void Session::RetryRecv()
{
	int64 FirstRetryTimeStampSec = m_i64RetryRecvTimestampSec.load();
	bool bBufferFull = m_bBufferFull.load();

	if (bBufferFull == true)
	{
		std::cout << "[OnRecv] Recv buffer Full" << endl;

		if (FirstRetryTimeStampSec == 0)
		{
			m_i64RetryRecvTimestampSec.store(g_TimeMaker.GetTimeStamp_Sec());
			FirstRetryTimeStampSec = m_i64RetryRecvTimestampSec.load();
		}

		if (g_TimeMaker.GetTimeStamp_Sec() - FirstRetryTimeStampSec >= 60)
		{
			std::cout << "[OnRecv] Client disconnected\n";
			CloseSocket();
		}
		else
			PostRecv();
	}
	else
	{
		std::cout << "[OnRecv] Client disconnected\n";
		CloseSocket();
	}
}

void Session::ParsePackets()
{
	while (true)
	{
		if (m_recvRingBuffer.GetUseSize() < sizeof(PacketHeader))
			break;

		PacketHeader header;
		if (!m_recvRingBuffer.PeekCopy(reinterpret_cast<char*>(&header), sizeof(PacketHeader)))
			break;

		header.pkgID = ntohl(header.pkgID);
		header.pkgSize = ntohl(header.pkgSize);
		if (header.pkgSize < sizeof(PacketHeader))
		{
			std::cerr << "[Error] Invalid packet size: " << header.pkgSize << "\n";
			CloseSocket();
			return;
		}

		if (static_cast<ullong>(header.pkgSize) > m_recvRingBuffer.GetUsableCapacity())
		{
            std::cerr << "[Error] Packet size exceeds recv buffer capacity: " << header.pkgSize << "\n";
			CloseSocket();
			return;
		}
		if (m_recvRingBuffer.GetUseSize() < header.pkgSize)
			break;

		std::vector<char> packet(header.pkgSize);
		m_recvRingBuffer.Read(packet.data(), header.pkgSize);

		InputMemoryStream PacketStream(reinterpret_cast<uint8*>(packet.data()), packet.size());

		PacketHeader parsedHeader;
		if (!PacketStream.DeSerialize(parsedHeader.pkgID, parsedHeader.pkgSize))
		{
			std::cerr << "[Error] Failed to deserialize packet header\n";
			CloseSocket();
			return;
		}

		OnPacketReceived(static_cast<PACKET_NUMBER>(parsedHeader.pkgID), parsedHeader.pkgSize, PacketStream);

		if (m_socket == INVALID_SOCKET)
			return;

		if (PacketStream.GetRemainingSize() != 0)
		{
			std::cerr << "[Error] Packet payload has trailing bytes: " << PacketStream.GetRemainingSize() << "\n";
			CloseSocket();
			return;
		}
	}
}

