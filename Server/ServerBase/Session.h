#pragma once
#include "IocpCore.h"
#include "RecvBuffer.h"
#include "SendBuffer.h"

enum class SessionType
{
	Server,
	Client
};

class Session : public IocpObject
{
public:
	Session(SOCKET socket, SessionType eSessionType);
	virtual ~Session();
	Session(const Session&) = delete;
	Session& operator=(const Session&) = delete;

	virtual HANDLE GetHandle() override { return reinterpret_cast<HANDLE>(m_socket); }
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) override;

public:
	// 인터페이스
	// void Init(SOCKET socket);
	void Start(); // 초기 PostRecv
	void PostRecv(); // 비동기 수신 요청
	void OnRecv(DWORD numOfByptes); // 수신 완료 후 처리
	void PostSend(const char* data, int32 len);
	void OnSend(DWORD numOfBytes);
	void OnSend2(DWORD numOfBytes);

	SOCKET GetSocket() const { return m_socket; }

	char* GetAcceptBuffer() { return m_acceptBuffer.get(); }

	// 송신 큐 & 인플라이트 1개 정책 도입으로 개선
	void SendPacket(const char* payload, int len);

	// 세션 - 워커 고정
	bool TryBindWorker(int32 _i32WorkerID);
	int32 GetBoundWorkerID() const { return m_i32BoundWorkerID.load(); };
	bool IsMyWorker(int32 _i32WorkerID) const { return m_i32BoundWorkerID.load() == _i32WorkerID; }

private:
	void enqueueSend(stSendItem&& item);
	void postNextSend();

private:
	SOCKET m_socket;
	// char m_recvBuffer[MAX_RECIEVE_BUFFER_SIZE]; // 단순 테스트용
	RingRecvBuffer m_recvRingBuffer;
	unique_ptr<char[]> m_acceptBuffer; // AcceptEx 전용 버퍼.

	SessionType m_SessionType;

	SendBufferPool m_SendPool{ 64 * 1024 };
	deque<stSendItem> m_SendQueue;
	atomic<bool> m_bSendInFlight{ false };
	atomic<ullong> m_ullPendingBytes{ 0 };

	// -1 : 아직 미할당, 그 외 : 세션을 담당할 워커 ID
	atomic<int32> m_i32BoundWorkerID{ -1 };
};

